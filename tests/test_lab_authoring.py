import base64
import json

import pytest

import lab_exchange
import lab_pack
import operations
from test_api import service
from test_simple_flow import settings, simple_lab


LABS = '/api/simple/labs'
PACKS = LABS + '/packs'


def post(client, headers, path, body, status=200):
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == status, result.json
    return result


def create_source_job(client, headers):
    body = "- ansible.windows.win_file:\n    path: 'C:\\{{ proxmate_inputs.folder }}'\n    state: directory\n"
    script = post(client, headers, '/api/manage/tasks', {
        'name': 'Tools directory', 'kind': 'ansible', 'platform': 'windows', 'body': body,
        'parameters': {'folder': 'Folder name'}, 'defaults': {'folder': 'LabTools'},
    }).json
    draft = {**settings(), 'count': 1, 'manual_updates': True, 'tasks': [
        {'id': script['id'], 'params': {'folder': 'Historical'}},
        {'id': 'windows-domain-controller', 'params': {'domain_name': 'old.example.test', 'dsrm_password': 'Old restore #125!'}},
        {'id': 'enable-rdp'},
    ]}
    plan = post(client, headers, '/api/simple/plan', draft).json
    job = post(client, headers, '/api/simple/run', {
        'plan_id': plan['id'], 'confirmed': True, 'risk_confirmed': True,
    }, 202).json['jobs'][0]['id']
    return job, script, body


def test_saved_build_and_pack_copy_keep_reviewed_steps_after_library_removal(simple_lab):
    app, client, headers, _ = simple_lab
    job, script, body = create_source_job(client, headers)
    saved = post(client, headers, '/api/simple/recipes', {'name': 'Ready for labs', 'job_id': job}, 201).json
    with app.db() as con:
        con.execute("DELETE FROM records WHERE kind='tasks' AND id=?", (script['id'],))
    source = client.get('/api/simple/recipes/' + saved['id'] + '/lab-source', headers=headers).json
    assert source['base_recipe_id'] == saved['id']
    assert source['tasks'][0]['params']['folder'] == 'Historical'
    assert 'Old restore' not in json.dumps(source)
    entry = dict(source, key='dc', tasks=[{'original': 1}, {'original': 0}])
    pack = post(client, headers, PACKS, {'name': 'First lab', 'machines': [entry]}, 201).json
    opened = client.get(PACKS + '/' + pack['id'], headers=headers).json
    machine = opened['machines'][0]
    assert [t['name'] for t in machine['editable_tasks']] == ['Set up a Windows domain controller', 'Tools directory']
    assert machine['editable_tasks'][0]['params'] == {'domain_name': 'old.example.test'}

    # Imported packs may use their own named prompts and file paths.
    manifest = opened['manifest']
    manifest['machines']['dc']['tasks'][0]['with']['dsrm_password'] = {'prompt': 'lab_recovery'}
    imported = post(client, headers, LABS + '/import', {
        'archive': base64.b64encode(lab_pack.write_pack(manifest, opened['scripts'])).decode(),
    }).json
    path = PACKS + '/' + imported['id'] + '/export'
    original = client.get(path, headers=headers).data
    entry.pop('base_recipe_id')
    entry.update(base_pack_id=imported['id'], base_role='dc', tasks=[
        {'original': 1, 'params': {'folder': 'Second'}}, {'original': 1}, {'original': 0}])
    copy = post(client, headers, PACKS, {'name': 'Second lab', 'machines': [entry]}, 201).json
    first, second, dc = copy['machines'][0]['tasks']
    assert first['file'] == second['file'] and copy['scripts'][first['file']] == body
    assert [t['with']['folder'] for t in (first, second)] == ['Second', 'Historical']
    assert dc['with']['dsrm_password'] == {'prompt': 'lab_recovery'}
    assert client.get(path, headers=headers).data == original
    for invalid in ({'base_role': 'missing'}, {'base_job_id': job}, {'tasks': [{'original': 99}]},
                    {'tasks': [{'original': 0, 'params': {'dsrm_password': 'should-stay-private'}}]}):
        result = post(client, headers, PACKS, {'name': 'Bad copy', 'machines': [dict(entry, **invalid)]}, 400)
        assert 'should-stay-private' not in result.get_data(as_text=True)
    assert len(client.get('/api/jobs', headers=headers).json) == 1


def test_author_from_blank_catalog_mix_roles_without_touching_guests(simple_lab, monkeypatch):
    app, client, headers, _ = simple_lab
    def no_host(*args, **kwargs):
        pytest.fail('Saving a lab must not contact the lab host or create a build review.')
    monkeypatch.setattr(operations, 'inventory', no_host)
    monkeypatch.setattr(app.simple, 'review_build', no_host)
    assert not app.manager.all('tasks')
    draft = dict(name='Package rollout', machines=[
        {'key': 'shared', 'os': 'linux', 'source': {'kind': 'existing', 'hint': 'Team server'}, 'after': ['web'],
         'tasks': [{'id': 'linux-service', 'params': {'service': 'cron', 'state': 'restarted'}}]},
        {'key': 'web', 'os': 'ubuntu', 'source': {'kind': 'cloud'}, 'settings': {'cores': 2},
         'tasks': [{'id': 'linux-package', 'params': {'package': 'jq'}}]},
    ])
    result = post(client, headers, PACKS, draft, 201).json
    assert result['manifest']['version'] == 2
    assert [m['key'] for m in result['machines']] == ['web', 'shared']
    assert result['manifest']['machines']['shared']['tasks'] == [
        {'run': 'linux-service', 'version': 1, 'with': {'service': 'cron', 'state': 'restarted'}}]
    assert post(client, headers, PACKS, draft, 201).json == result
    download = client.get(PACKS + '/' + result['id'] + '/export', headers=headers)
    assert lab_pack.read_pack(download.data) == (result['manifest'], {})
    imported = post(client, headers, LABS + '/import', {'archive': base64.b64encode(download.data).decode()}).json
    assert imported == result and len(client.get(PACKS, headers=headers).json) == 1
    assert not app.manager.all('credentials') and not app.manager.all('connections')
    with app.db() as con:
        assert con.execute('SELECT COUNT(*) FROM plans').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_author_preserves_frozen_originals_and_public_inputs_alongside_new_tasks(simple_lab):
    app, client, headers, _ = simple_lab
    job, old_script, body = create_source_job(client, headers)
    baseline_jobs = len(client.get('/api/jobs', headers=headers).json)
    source = lab_exchange.editable_source(app.manager, job)
    assert [t['name'] for t in source['tasks']] == ['Tools directory', 'Set up a Windows domain controller', 'Enable Remote Desktop']
    assert source['tasks'][1]['params'] == {'domain_name': 'old.example.test'}
    assert source['tasks'][1]['input_types']['dsrm_password']['type'] == 'password'
    assert source['base_job_id'] == job and source['settings']['manual_updates']
    assert all(secret not in json.dumps(source) for secret in ('Old restore', 'Secret-Setup', 'local:iso', '192.0.2.'))
    original_export = post(client, headers, LABS + '/export', {
        'name': 'Copied machine', 'machines': [{'key': 'dc', 'job_id': job}],
    }).data
    entry = dict(source, key='dc', tasks=[{'original': n} for n in range(len(source['tasks']))])
    unchanged = post(client, headers, PACKS, {'name': 'Copied machine', 'machines': [entry]}, 201).json
    assert client.get(PACKS + '/' + unchanged['id'] + '/export', headers=headers).data == original_export

    updated = {**old_script, 'body': body.replace('directory', 'absent')}
    post(client, headers, '/api/manage/tasks', updated)
    entry['tasks'] = [
        {'original': 1, 'params': {'domain_name': 'fresh.example.test'}},
        {'id': old_script['id']},
        {'original': 0, 'params': {'folder': 'Frozen'}},
        {'id': 'windows-feature', 'params': {'feature': 'Web-Server'}},
    ]
    made = post(client, headers, PACKS, {'name': 'Copied machine', 'machines': [entry]}, 201).json
    assert made['manifest']['version'] == 1
    dc, current, old, feature = made['machines'][0]['tasks']
    assert dc['with'] == {'domain_name': 'fresh.example.test', 'dsrm_password': {'prompt': 'dsrm_password'}}
    assert made['machines'][0]['secret_inputs'] == [{'key': 'dsrm_password', 'label': 'dsrm password'}]
    assert current['with'] == {'folder': 'LabTools'} and old['with'] == {'folder': 'Frozen'}
    assert made['scripts'][current['file']] == updated['body'] and made['scripts'][old['file']] == body
    assert feature == {'run': 'windows-feature', 'version': 1, 'with': {'feature': 'Web-Server'}}
    assert 'enable-rdp' not in json.dumps(made)
    assert all(private not in json.dumps(made) for private in (job, old_script['id'], 'Old restore', 'Secret-Setup'))
    with app.db() as con:
        con.execute("DELETE FROM records WHERE kind='tasks' AND id=?", (old_script['id'],))
    assert not app.manager.all('tasks')
    entry['tasks'] = [{'original': 0}]
    inherited = post(client, headers, PACKS, {'name': 'Still frozen', 'machines': [entry]}, 201).json
    assert list(inherited['scripts'].values()) == [body]
    assert len(client.get('/api/jobs', headers=headers).json) == baseline_jobs


@pytest.mark.parametrize('change', [
    {'settings': {'node': 'pve'}}, {'password': 'do-not-echo'}, {'source': {'kind': 'iso', 'hint': '/data/local.iso'}},
    {'tasks': [{'id': 'not-in-the-library'}]}, {'tasks': [{'original': 0}]}, {'tasks': [{'original': False}]},
    {'tasks': [{'original': -1}]}, {'tasks': [{'id': 'install-chocolatey', 'original': 0}]},
    {'tasks': [{'id': 'windows-domain-controller', 'params': {'dsrm_password': 'do-not-echo'}}]},
    {'tasks': [{'id': 'windows-feature', 'params': {'secret': 'do-not-echo'}}]},
    {'tasks': [{'id': 'windows-feature', 'params': {'feature': 'bad value'}}]},
    {'tasks': [{'id': 'windows-feature', 'body': 'ignored malicious text'}]},
    {'tasks': [{'id': []}]}, {'tasks': ['invalid']}, {'after': ['missing']},
    {'os': 'windows', 'source': {'kind': 'existing'}, 'tasks': []},
])
def test_invalid_authoring_is_atomic_and_redacted(simple_lab, change):
    app, client, headers, _ = simple_lab
    machines = [{'key': 'first', 'os': 'ubuntu', 'source': {'kind': 'cloud'}, 'tasks': [{'id': 'linux-updates'}]},
                {'key': 'second', 'os': 'windows', 'source': {'kind': 'iso'}, **change}]
    error = post(client, headers, PACKS, {'name': 'Rejected', 'machines': machines}, 400)
    assert 'do-not-echo' not in error.text
    assert not app.manager.all('lab-packs') and not app.manager.all('lab-pack-index')
    assert not client.get('/api/jobs', headers=headers).json


def test_author_bounds_source_lookups_and_rejects_foreign_or_legacy_jobs(simple_lab, monkeypatch):
    app, client, headers, _ = simple_lab
    entry = {'key': 'guest', 'os': 'ubuntu', 'source': {'kind': 'cloud'}, 'tasks': [{'id': 'linux-updates'}]}
    monkeypatch.setattr(app.manager, 'task', lambda *_: pytest.fail('Looked up a task before checking batch bounds.'))
    for entries in ([entry] * 21, [entry, {**entry, 'tasks': entry['tasks'] * 51}]):
        post(client, headers, PACKS, {'name': 'Oversize', 'machines': entries}, 400)
    job = 'old-job'
    with app.db() as con:
        con.execute("INSERT INTO jobs(id,action,status,created,payload,log) VALUES (?,'simple','success',0,?,'')",
                    (job, app.encode_payload({'server_id': 'another-server', 'plan': {}})))
    machine = {**entry, 'base_job_id': job, 'tasks': [{'original': 0}]}
    err = post(client, headers, PACKS, {'name': 'Foreign', 'machines': [machine]}, 400)
    assert 'selected server' in err.json['error']
    with app.db() as con:
        con.execute("UPDATE jobs SET payload=? WHERE id=?", (app.encode_payload({'server_id': app.server.read()['id'], 'plan': {}}), job))
    with pytest.raises(ValueError, match='historical'):
        lab_exchange.editable_source(app.manager, job)
    assert not app.manager.all('lab-pack-index')


def test_editable_source_redacts_personal_keys_logins_and_historical_sensitive_choices(simple_lab, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    import linux_access

    app, client, headers, _ = simple_lab
    workstation = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
    monkeypatch.setattr(linux_access, 'github_keys', lambda handle: [workstation])
    legacy_value = 'OldToken-123'
    saved = post(client, headers, '/api/manage/tasks', {
        'name': 'Old local-only task', 'kind': 'ansible', 'platform': 'linux',
        'parameters': {'api_token': 'API token'}, 'defaults': {'api_token': legacy_value},
        'input_types': {'api_token': {'type': 'choice', 'choices': [legacy_value]}},
        'body': '- ansible.builtin.command: hostname',
    }).json
    draft = dict(os='ubuntu', source_kind='cloud', name='linuxkey', ssh_public_key=workstation,
                 github_username='privatehandle', linux_password='Historical-login#123',
                 tasks=[{'id': saved['id']}])
    reviewed = post(client, headers, '/api/simple/plan', draft).json
    job = post(client, headers, '/api/simple/run',
               dict(plan_id=reviewed['id'], confirmed=True, risk_confirmed=True), 202).json['jobs'][0]['id']
    source = lab_exchange.editable_source(app.manager, job)
    assert source['settings']['key_login'] and source['settings']['password_login']
    original, = source['tasks']
    assert original['params'] == original['defaults'] == {}
    assert original['input_types'] == {'api_token': {'type': 'password'}}
    assert all(v not in json.dumps(source) for v in (workstation, 'privatehandle', draft['linux_password'], legacy_value))
    entry = {**source, 'key': 'linuxcopy', 'tasks': [{'original': 0}]}
    err = post(client, headers, PACKS, {'name': 'Not portable', 'machines': [entry]}, 400)
    assert legacy_value not in err.text
    entry['tasks'] = []
    made = post(client, headers, PACKS, {'name': 'Without old script', 'machines': [entry]}, 201).json
    assert made['scripts'] == {} and made['machines'][0]['tasks'] == []


def test_new_existing_guest_task_creates_deployment_prompt_without_login_material(simple_lab):
    app, client, headers, _ = simple_lab
    entry = {'key': 'directory', 'os': 'windows', 'source': {'kind': 'existing'}, 'tasks': [
        {'id': 'windows-domain-controller', 'params': {'domain_name': 'ad.example.test'}},
    ]}
    result = post(client, headers, PACKS, {'name': 'Directory service', 'machines': [entry]}, 201).json
    task, = result['machines'][0]['tasks']
    assert task['with'] == {'domain_name': 'ad.example.test', 'dsrm_password': {'prompt': 'dsrm_password'}}
    with pytest.raises(ValueError):
        lab_pack.write_pack({**result['manifest'], 'version': 1}, {})
    assert not app.manager.all('credentials') and not client.get('/api/jobs', headers=headers).json
