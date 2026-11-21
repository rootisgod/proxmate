import copy
import io
import stat
import warnings
import zipfile

import pytest
import yaml

import lab_pack


PATH = 'tasks/folder.yml'
BODY = """- name: Create a folder
  ansible.builtin.file:
    path: '/opt/{{ proxmate_inputs.folder }}'
    state: directory
"""


def pack():
    return {
        'version': 1,
        'name': 'Two machines',
        'machines': {
            'app': {
                'os': 'ubuntu', 'source': {'kind': 'template', 'hint': 'Base Noble'},
                'settings': {'cores': 2, 'memory': 4096, 'key_login': True},
                'after': ['db'],
                'tasks': [
                    {'file': PATH, 'with': {'folder': 'team'}},
                    {'run': 'linux-package', 'version': 1, 'with': {'package': 'jq'}},
                ],
            },
            'db': {'os': 'debian', 'source': {'kind': 'iso', 'hint': 'debian-13.iso'}},
        },
    }, {PATH: BODY}


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Duplicate name:')
            for path, data in entries:
                stream.writestr(path, data)
    return output.getvalue()


def test_round_trip_portable_files_and_ordered_builtin_inputs():
    manifest, scripts = pack()
    data = lab_pack.write_pack(manifest, scripts)
    assert lab_pack.write_pack(*pack()) == data
    assert lab_pack.read_pack(data) == (manifest, scripts)
    assert lab_pack.ordered_vms(manifest) == ['db', 'app']
    first, second = lab_pack.resolve_tasks(manifest['machines']['app'], manifest, scripts)
    assert first['item']['id'].startswith('pack-')
    assert first['item']['body'] == BODY and first['item']['dangerous']
    assert first['params'] == {'folder': 'team'}
    assert second['item']['id'] == 'linux-package' and second['params'] == {'package': 'jq'}
    second['item']['name'] = 'changed downstream'
    assert lab_pack.BUILTIN_V1['linux-package']['name'] != 'changed downstream'


def test_metadata_preserves_default_and_typed_parameters_and_no_aliases_in_written_yaml():
    manifest, scripts = pack()
    manifest['scripts'] = {PATH: {
        'name': 'Prepare folder', 'kind': 'ansible', 'platform': 'linux',
        'parameters': {'folder': 'Folder name', 'enabled': 'Enable option'},
        'defaults': {'folder': 'team'}, 'input_types': {'enabled': {'type': 'boolean'}},
    }}
    steps = manifest['machines']['app']['tasks']
    steps[:] = [{'file': PATH}, {'file': PATH, 'with': {'enabled': True}}]
    manifest['machines']['db']['tasks'] = steps
    manifest, scripts = lab_pack.read_pack(lab_pack.write_pack(manifest, scripts))
    first, second = lab_pack.resolve_tasks(manifest['machines']['app'], manifest, scripts)
    assert first['params'] == {'folder': 'team', 'enabled': 'false'}
    assert second['params'] == {'folder': 'team', 'enabled': 'true'}


def test_password_prompt_is_portable_and_secret_only_enters_frozen_task():
    machine = {
        'os': 'windows', 'source': {'kind': 'iso', 'edition': 'Standard (Desktop Experience)'},
        'tasks': [{'run': 'windows-domain-controller', 'version': 1, 'with': {
            'domain_name': 'ad.example.test', 'dsrm_password': {'prompt': 'restore_login'},
        }}],
    }
    manifest = {'version': 1, 'name': 'Domain', 'machines': {'dc': machine}}
    raw = lab_pack.write_pack(manifest, {})
    saved, scripts = lab_pack.read_pack(raw)
    with pytest.raises(ValueError, match='restore_login'):
        lab_pack.resolve_tasks(machine, saved, scripts)
    secret = 'Fresh local secret #12'
    selection, = lab_pack.resolve_tasks(machine, saved, scripts, {'restore_login': secret})
    assert selection['params']['dsrm_password'] == secret
    assert saved == manifest and secret not in repr(manifest)
    machine['tasks'][0]['with']['dsrm_password'] = secret
    with pytest.raises(ValueError, match='named prompt') as error:
        lab_pack.write_pack(manifest, {})
    assert secret not in str(error.value)


@pytest.mark.parametrize('change', [
    {'vmid': 123}, {'name': 'local-name'}, {'os': 'proxmox'},
    {'source': {'kind': 'template', 'template_id': 101}},
    {'source': {'kind': 'iso', 'hint': '/srv/isos/a.iso'}},
    {'source': {'kind': 'template', 'hint': 'pve:123'}},
    {'source': {'kind': 'template', 'hint': '123'}},
    {'settings': {'password': 'LocalOnly'}}, {'settings': {'ssh_public_key': 'LocalOnly'}},
    {'settings': {'storage': 'local-lvm'}}, {'settings': {'count': 2}},
    {'settings': {'targets': [{'node': 'pve', 'vmid': 111}]}},
    {'settings': {'purpose': 'template'}}, {'after': ['missing']}, {'after': ['app']},
    {'after': ['db', 'db']}, {'settings': {'cores': True}},
    {'tasks': [{'run': 'linux-package', 'version': 2}]},
    {'tasks': [{'run': 'linux-package', 'version': True}]},
    {'tasks': [{'run': 'linux-package'}]}, {'tasks': [{'run': 'example-linux-folder', 'version': 1}]},
    {'tasks': [{'run': 'windows-service', 'version': 1}]},
    {'tasks': [{'run': 'linux-updates', 'version': 1, 'with': {'password': 'LocalOnly'}}]},
    {'tasks': [{'file': PATH, 'with': {'folder': {'prompt': 'unauthorized'}}}]},
    {'tasks': [{'file': 'tasks/missing.sh'}]},
])
def test_reject_scope_local_bindings_invalid_references_and_dependencies(change):
    manifest, scripts = pack()
    manifest['machines']['app'].update(change)
    with pytest.raises(ValueError):
        lab_pack.write_pack(manifest, scripts)


@pytest.mark.parametrize('content', [
    'name: a\nname: b\n', 'x: &ref [value]\ny: *ref\n', 'data: !!binary Zg==',
    'x: ' + '[' * 30 + 'x' + ']' * 30, 'x: [broken', '---\n1\n---\n2\n',
])
def test_reject_ambiguous_or_complex_yaml(content):
    with pytest.raises(ValueError):
        lab_pack.read_pack(archive([('lab.yaml', content)]))


@pytest.mark.parametrize('member', [
    '../lab.yaml', '/lab.yaml', 'C:/lab.yaml', 'tasks/../script.yml', 'tasks\\a.yml',
    'extra', 'tasks/.hidden.yml', 'tasks/file.py', 'tasks/nested/one.yml', 'tasks/con.yml',
    'tasks/aux.tar.yml', 'lab.yaml', 'LAB.yaml',
])
def test_reject_unexpected_files_even_with_valid_manifest(member):
    manifest, scripts = pack()
    with pytest.raises(ValueError):
        lab_pack.read_pack(archive([('lab.yaml', yaml.safe_dump(manifest)), *scripts.items(), (member, BODY)]))


def test_windows_zip_uses_the_same_portable_paths_and_duplicate_checks():
    manifest, files = pack()
    entries = [('lab.yaml', yaml.safe_dump(manifest)), *files.items()]
    # Use the on-disk representation of Compress-Archive on Windows PowerShell 5.
    portable = archive(entries)
    windows = portable.replace(b'tasks/folder.yml', b'tasks\\folder.yml')
    assert windows != portable
    assert lab_pack.read_pack(windows) == (manifest, files)
    conflicting = archive([*entries, ('tasks/second.yml', BODY)])
    conflicting = conflicting.replace(b'tasks/second.yml', b'tasks\\folder.yml')
    with pytest.raises(ValueError, match='Duplicate'):
        lab_pack.read_pack(conflicting)


def test_reject_symlink_special_files_bad_unicode_oversize_and_missing_manifest():
    manifest, scripts = pack()
    entry = zipfile.ZipInfo(PATH)
    entry.create_system = 3
    for mode in (stat.S_IFLNK, stat.S_IFIFO):
        entry.external_attr = (mode | 0o777) << 16
        with pytest.raises(ValueError, match='special files'):
            lab_pack.read_pack(archive([('lab.yaml', yaml.safe_dump(manifest)), (entry, 'target')]))
    for entries in ([], [(PATH, BODY)], [('lab.yaml', b'\xff')],
                    [('lab.yaml', b'a' * (lab_pack.MAX_MANIFEST_BYTES + 1))],
                    [('lab.yaml', 'test'), (PATH, 'a' * (lab_pack.MAX_SCRIPT_BYTES + 1))]):
        with pytest.raises(ValueError):
            lab_pack.read_pack(archive(entries))
    with pytest.raises(ValueError, match='2 MiB'):
        lab_pack.read_pack(b'x' * (lab_pack.MAX_PACK_BYTES + 1))
    data = archive([('tasks/', ''), ('lab.yaml', yaml.safe_dump(manifest, sort_keys=False)), *scripts.items()])
    assert lab_pack.read_pack(data) == (manifest, scripts)


@pytest.mark.parametrize('body', [
    '- ansible.builtin.debug: {}',
    '- ansible.builtin.command: echo ok\n  delegate_to: localhost',
    '- ansible.builtin.copy: {src: local.txt, dest: /tmp/example}',
    '- ansible.builtin.copy: src=local.txt dest=/tmp/example',
    '- ansible.windows.win_powershell: {path: local.ps1}',
    '- ansible.builtin.package: {name: curl, use: custom_backend}',
    '- ansible.builtin.copy: {content: example, _original_basename: example}',
    '- ansible.builtin.file: {"{{ proxmate_inputs.folder }}": directory}',
    "- ansible.builtin.command: \"echo {{ lookup('env', 'PACK_EXAMPLE') }}\"",
    r'- ansible.builtin.command: "echo \u007b\u007b unexpected_variable }}"',
    "- ansible.builtin.command: '{% if true %}echo ok{% endif %}'",
    '- &first {ansible.builtin.command: echo ok}\n- *first',
])
def test_reject_controller_features_in_portable_ansible(body):
    manifest, scripts = pack()
    manifest['machines']['app']['tasks'] = [{'file': PATH}]
    scripts[PATH] = body
    with pytest.raises(ValueError):
        lab_pack.read_pack(archive([('lab.yaml', yaml.safe_dump(manifest)), *scripts.items()]))


def test_guest_side_copy_and_shell_are_portable_without_library_records():
    manifest, _ = pack()
    manifest['machines']['app']['tasks'] = [{'file': PATH}, {'file': 'tasks/status.sh'}]
    scripts = {
        PATH: '- ansible.builtin.copy: {src: /etc/hostname, dest: /tmp/name, remote_src: true}',
        'tasks/status.sh': "printf '%s\\n' ready\n",
    }
    manifest, scripts = lab_pack.read_pack(lab_pack.write_pack(manifest, scripts))
    selections = lab_pack.resolve_tasks(manifest['machines']['app'], manifest, scripts)
    assert [s['item']['kind'] for s in selections] == ['ansible', 'shell']
    assert selections[1]['item']['body'] == scripts['tasks/status.sh']
    scripts['tasks/status.sh'] = 'echo "{{ unexpected }}"'
    with pytest.raises(ValueError, match='interpolate'):
        lab_pack.write_pack(manifest, scripts)


def test_no_unselected_files_and_bound_frozen_selections(monkeypatch):
    manifest, scripts = pack()
    with pytest.raises(ValueError, match='selected'):
        lab_pack.write_pack(manifest, {**scripts, 'tasks/unused.sh': 'true'})
    monkeypatch.setattr(lab_pack, 'MAX_SELECTED_BYTES', len(BODY.encode()) + 1)
    manifest['machines']['db']['tasks'] = copy.deepcopy(manifest['machines']['app']['tasks'])
    with pytest.raises(ValueError, match='frozen script limit'):
        lab_pack.write_pack(manifest, scripts)


def test_windows_powershell_bytes_are_preserved_and_logical_batch_names_fit():
    path = 'tasks/version.ps1'
    body = "Write-Output ([Environment]::OSVersion.Version)\r\n"
    machine = {'os': 'windows', 'source': {'kind': 'template', 'hint': 'Gold Standard'},
               'settings': {'clone_mode': 'full'}, 'tasks': [{'file': path}]}
    manifest = {'version': 1, 'name': 'Windows', 'machines': {'windowsbuild-01': machine}}
    loaded, scripts = lab_pack.read_pack(lab_pack.write_pack(manifest, {path: body}))
    task, = lab_pack.resolve_tasks(machine, loaded, scripts)
    assert task['item']['kind'] == 'powershell' and task['item']['body'] == body


def test_ready_machines_follow_declaration_order_across_dependencies():
    manifest = {'machines': {'first': {'after': ['second']}, 'second': {}, 'third': {}, 'last': {'after': ['third', 'first']}}}
    assert lab_pack.ordered_vms(manifest) == ['second', 'first', 'third', 'last']


def test_v2_existing_role_is_unbound_and_can_follow_a_create_role():
    manifest, files = pack()
    existing = {'os': 'linux', 'source': {'kind': 'existing', 'hint': 'Shared tools'},
                'after': ['app'], 'tasks': [{'file': PATH, 'with': {'folder': 'team'}}]}
    manifest['machines']['tools'] = existing
    with pytest.raises(ValueError):
        lab_pack.write_pack(manifest, files)
    manifest['version'] = 2
    read, scripts = lab_pack.read_pack(lab_pack.write_pack(manifest, files))
    assert read == manifest and scripts == files
    assert lab_pack.ordered_vms(read) == ['db', 'app', 'tools']
    assert lab_pack.resolve_tasks(existing, read, scripts)[0]['params'] == {'folder': 'team'}


@pytest.mark.parametrize('change', [
    {'tasks': []}, {'os': 'ubuntu'}, {'settings': {'cores': 4}}, {'settings': {'purpose': 'vm'}},
    {'settings': None}, {'source': {'kind': 'existing', 'vmid': 234}},
    {'source': {'kind': 'existing', 'hint': 'pve:234'}}, {'source': {'kind': 'existing', 'edition': 'Windows'}},
    {'targets': ['pve:234']}, {'connection': 'saved'}, {'password': 'destination'},
    {'tasks': [{'run': 'windows-feature', 'version': 1, 'with': {'feature': 'Web-Server'}}]},
])
def test_v2_existing_roles_reject_creation_settings_host_identity_and_empty_work(change):
    existing = {'os': 'linux', 'source': {'kind': 'existing'}, 'tasks': [{'run': 'linux-updates', 'version': 1}], **change}
    data = {'version': 2, 'name': 'Guest maintenance', 'machines': {'guest': existing}}
    with pytest.raises(ValueError):
        lab_pack.read_pack(archive([('lab.yaml', yaml.safe_dump(data))]))
