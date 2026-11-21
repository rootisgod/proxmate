import base64
import io
import json
from pathlib import Path
import re
import zipfile

import lab_pack
import yaml
from test_api import service
from test_lab_runs import existing_lab
from test_simple_flow import simple_lab, settings


def test_portable_saved_build_on_a_second_dashboard(simple_lab, tmp_path, monkeypatch):
    _, source, headers, _ = simple_lab
    def post(path, data):
        response = source.post('/api/'+path, json=data, headers=headers)
        assert response.status_code < 300, response.json
        return response

    body = "- name: Create tools\n  ansible.windows.win_file:\n    path: 'C:\\{{ proxmate_inputs.name }}'\n    state: directory\n"
    task = post('manage/tasks', dict(name='Team tools', platform='windows', kind='ansible', body=body,
                parameters={'name': 'Folder name'}, defaults={'name': 'Tools'})).json
    restore = 'Restore-Original#1234'
    draft = {**settings(), 'count': 1, 'tasks': [
        {'id': task['id'], 'params': {'name': 'TeamTools'}},
        {'id': 'windows-domain-controller', 'params': {'domain_name': 'dev.example.test', 'dsrm_password': restore}}]}
    plan = post('simple/plan', draft).json
    saved = post('simple/recipes', {'plan_id': plan['id'], 'name': 'Lab DC'}).json
    original_job = post('simple/run', dict(plan_id=plan['id'], confirmed=True, risk_confirmed=True)).json['jobs'][0]['id']
    post('manage/tasks', {**task, 'body': body.replace('Create tools', 'Changed after review')})
    assembled = dict(name='Office lab', machines=[{'key': 'dc01', 'recipe_id': saved['id']}])
    archive = post('simple/labs/export', assembled)
    assert archive.mimetype == 'application/zip'
    data = archive.data
    for field, value in [('plan_id', plan['id']), ('job_id', original_job)]:
        other = post('simple/labs/export', dict(name='Office lab', machines=[{'key': 'dc01', field: value}]))
        assert other.data == data
    manifest, files = lab_pack.read_pack(data)
    machine = manifest['machines']['dc01']
    assert machine['source'] == {'kind': 'iso', 'hint': 'server.iso', 'edition': 'Standard (Desktop Experience)'}
    file_task, dc_task = machine['tasks']
    assert files[file_task['file']] == body
    assert file_task['with'] == {'name': 'TeamTools'}
    assert dc_task['with']['dsrm_password'] == {'prompt': 'dsrm_password'}
    expanded = json.dumps([manifest, files])
    assert all(value not in expanded for value in (draft['password'], restore, task['id'], '192.0.2.', 'local:iso', 'vmbr0'))

    # A different database, encryption key, server ID, catalog and storage volume.
    dest_path = tmp_path / 'second-dashboard'
    dest_path.mkdir()
    dest_service = service.__wrapped__(dest_path, monkeypatch)
    from test_simple_flow import simple_lab as lab_fixture
    app, client, auth, inv = lab_fixture.__wrapped__(dest_service, monkeypatch)
    inv['details']['lab']['storage'][0]['storage'] = 'fast-ssd'
    inv['details']['lab']['isos'][0]['volid'] = 'local:iso/destination.iso'
    upload = {'archive': base64.b64encode(data).decode()}
    result = client.post('/api/simple/labs/import', json=upload, headers=auth)
    assert result.status_code == 200, result.json
    imp = result.json
    assert imp['machines'][0]['secret_inputs'][0]['key'] == 'dsrm_password'
    assert client.get('/api/simple/labs/packs', headers=auth).json[0]['machine_count'] == 1
    assert client.get('/api/simple/labs/packs/'+imp['id'], headers=auth).json == imp
    assert client.get('/api/simple/labs/packs/'+imp['id']+'/export', headers=auth).data == data
    assert client.post('/api/simple/labs/import', json=upload, headers=auth).json['id'] == imp['id']
    assert not app.manager.all('tasks') and not app.manager.all('credentials')
    local = dict(pack_id=imp['id'], bindings={'dc01': dict(name='office-dc', storage='fast-ssd',
                 source='local:iso/destination.iso', edition=7, edition_identity='media-v1')},
                 secrets={'dc01': {'password': 'Destination-Guest#123', 'inputs': {'dsrm_password': 'Destination-DSRM#123'}}})
    reviewed = client.post('/api/simple/labs/plan', json=local, headers=auth)
    assert reviewed.status_code == 200, reviewed.json
    assert reviewed.json['dangerous'] and reviewed.json['machines'][0]['plan']['machines'][0]['storage'] == 'fast-ssd'
    assert 'Destination-' not in reviewed.get_data(as_text=True)
    assert client.get('/api/jobs', headers=auth).json == []
    queued = client.post('/api/simple/labs/run', json=dict(plan_id=reviewed.json['id'], confirmed=True, risk_confirmed=True), headers=auth)
    assert queued.status_code == 202, queued.json
    job_id = queued.json['machines'][0]['job_id']
    with app.db() as con:
        payload = app.decode_payload(con.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
    selected = payload['plan']['tasks']
    assert selected[0]['item']['body'] == body and selected[0]['params'] == {'name': 'TeamTools'}
    assert selected[1]['params']['dsrm_password'] == 'Destination-DSRM#123'
    assert not app.manager.all('tasks')


def test_import_rejects_invalid_inputs_without_storing_or_queuing(simple_lab):
    app, client, headers, _ = simple_lab
    for request_body in ({'archive':'%%%'} , {'archive': base64.b64encode(b'x').decode()}, [], {'wrong':1}):
        assert client.post('/api/simple/labs/import', json=request_body, headers=headers).status_code == 400
    assert not client.get('/api/simple/labs/packs', headers=headers).json
    assert not client.get('/api/jobs', headers=headers).json
    old = dict(id='historical', name='Old build', recipe={})
    app.manager.put('guided-recipes', old)
    err = client.post('/api/simple/labs/export', json=dict(name='Old', machines=[dict(key='dc01',recipe_id='historical')]),headers=headers)
    assert err.status_code == 400 and 'original job' in err.json['error']


def test_starter_pack_is_the_documented_ubuntu_example(simple_lab):
    app, client, headers, _ = simple_lab
    data = client.get('/api/simple/labs/example', headers=headers)
    assert data.status_code == 200 and data.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(data.data)) as archive:
        assert set(archive.namelist()) == {'lab.yaml', 'tasks/tools.yml'}
    manifest, _ = lab_pack.read_pack(data.data)
    assert manifest['machines']['tools01']['source']['kind'] == 'cloud'
    saved = client.post('/api/simple/labs/import', json={'archive':base64.b64encode(data.data).decode()}, headers=headers).json
    review = client.post('/api/simple/labs/plan', json={'pack_id':saved['id']}, headers=headers)
    assert review.status_code == 200, review.json
    assert review.json['dangerous'] and not client.get('/api/jobs', headers=headers).json


def test_published_pack_specification_matches_codec_and_routes(simple_lab):
    app, client, headers, _ = simple_lab
    spec = (Path(app.app.static_folder) / 'lab-packs.md').read_text(encoding='utf-8')
    manifest_text, task_text, existing_text = re.findall(r'```yaml\n(.*?)```', spec, re.S)
    manifest = yaml.safe_load(manifest_text)
    packed = lab_pack.write_pack(manifest, {'tasks/tools.yml':task_text})
    assert lab_pack.read_pack(packed) == (manifest, {'tasks/tools.yml':task_text})
    assert lab_pack.ordered_vms(manifest) == ['dc01', 'web01']
    mixed = yaml.safe_load(existing_text)
    assert lab_pack.read_pack(lab_pack.write_pack(mixed, {})) == (mixed, {})
    assert lab_pack.ordered_vms(mixed) == ['tools01', 'tools-admin']
    docs = client.get('/api/openapi.json', headers=headers).json
    actual = {str(rule).replace('<ident>', '{id}') for rule in app.app.url_map.iter_rules()
              if str(rule).startswith('/api/simple/labs/')}
    assert actual == {route for route in docs['paths'] if route.startswith('/api/simple/labs/')}
    assert 'blocked' in docs['components']['schemas']['JobSummary']['properties']['status']['enum']


def test_agent_guide_saves_and_reviews_mixed_roles_without_queuing_jobs(existing_lab):
    app, client, headers, _, _, guest_calls = existing_lab
    guide = (Path(app.app.static_folder) / 'llms.txt').read_text(encoding='utf-8')
    section = guide.split('### Create a saved lab directly', 1)[1]
    draft = json.loads(re.search(r'```json\n(.*?)```', section, re.S).group(1))
    saved = client.post('/api/simple/labs/packs', json=draft, headers=headers)
    assert saved.status_code == 201, saved.json
    pack = saved.json
    assert [m['key'] for m in pack['machines']] == ['tools01', 'tools-admin']
    assert pack['manifest']['version'] == 2
    example = pack['machines'][0]['tasks'][0]
    assert example['file'] in pack['scripts']
    assert pack['machines'][1]['tasks'][0]['run'] == 'linux-package'
    assert client.get('/api/simple/labs/packs', headers=headers).json[0]['id'] == pack['id']
    archive = client.get('/api/simple/labs/packs/'+pack['id']+'/export', headers=headers)
    assert lab_pack.read_pack(archive.data) == (pack['manifest'], pack['scripts'])

    reviewed = client.post('/api/simple/labs/plan', headers=headers, json={
        'pack_id': pack['id'],
        'bindings': {'tools-admin': {'node':'lab', 'vmid':201}},
        'secrets': {'tools-admin': {'credential':{'platform':'linux','username':'operator','password':'At-Deployment#1234'}}}
    })
    assert reviewed.status_code == 200, reviewed.json
    assert [(m['key'], m['mode']) for m in reviewed.json['machines']] == [('tools01','create'),('tools-admin','existing')]
    assert reviewed.json['existing'] and reviewed.json['machines'][1]['plan']['targets'][0]['vmid'] == 201
    assert not guest_calls
    assert not client.get('/api/jobs', headers=headers).json and not app.manager.all('credentials')
