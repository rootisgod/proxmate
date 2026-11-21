"""Portable Proxmate lab ZIP versions 1 and 2. No members are extracted to disk."""
import copy
import hashlib
import io
import re
import stat
import zipfile
import zlib

import yaml
from yaml.events import AliasEvent

from guest_tasks import BUILTINS, input_type_schema, script_parameters, validate_input
from script_library import check_script
from windows_setup import OPTIONS as WINDOWS_SETTINGS


MAX_PACK_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_SCRIPT_BYTES = 400_000  # UTF-8 encoding of the library's 100,000 character limit.
MAX_FILES = 32
MAX_SELECTED_BYTES = 8 * 1024 * 1024
HARDWARE_LIMITS = {'cores': (1, 128), 'memory': (512, 1048576), 'disk': (8, 4096)}
EXTENSIONS = {'yml': 'ansible', 'yaml': 'ansible', 'sh': 'shell', 'ps1': 'powershell'}
TASK_PATH = re.compile(r'tasks/[A-Za-z0-9][A-Za-z0-9_.-]{0,75}\.(yml|yaml|ps1|sh)')
MACHINE_NAME = re.compile(r'[A-Za-z][A-Za-z0-9-]{0,14}')
INPUT_REF = re.compile(r'{{\s*proxmate_inputs\.([a-z][a-z0-9_]{0,39})\s*}}')
SECRET_FIELD = re.compile(r'password|passphrase|private_key|api_key|secret|credential|token', re.I)
# A change to the meaning or inputs of a published reference needs a new version.
PUBLISHED_V1 = frozenset({
    'windows-domain-controller', 'install-chocolatey', 'chocolatey-package', 'windows-feature',
    'enable-rdp', 'disable-firewall', 'enable-firewall', 'windows-manual-updates', 'windows-updates',
    'linux-package', 'linux-updates', 'linux-service', 'windows-service',
})
BUILTIN_V1 = {item['id']: item for item in BUILTINS if item['id'] in PUBLISHED_V1}


class _PackLoader(yaml.SafeLoader):
    """Bound the YAML tree before construction. Do not expand aliases or merge keys."""
    def __init__(self, source):
        super().__init__(source)
        self.depth = self.nodes = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, AliasEvent) or event.anchor or event.tag:
            raise ValueError('YAML anchors, aliases and explicit tags are unsupported in lab packs.')
        self.depth += 1
        self.nodes += 1
        if self.depth > 24 or self.nodes > 12000:
            raise ValueError('The pack YAML is too deeply nested or has too many entries.')
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key, _ in node.value:
            if key.tag != 'tag:yaml.org,2002:str' or key.value in seen:
                raise ValueError('Pack YAML requires unique text mapping keys.')
            seen.add(key.value)
        return super().construct_mapping(node, deep)


class _ManifestLoader(_PackLoader):
    # Treat words such as "off" as text while preserving true/false settings.
    yaml_implicit_resolvers = {}


_ManifestLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(true|false)$'), list('tf'))
_ManifestLoader.add_implicit_resolver('tag:yaml.org,2002:int', re.compile(r'^(0|[1-9][0-9]{0,9})$'), list('0123456789'))


def _load_yaml(source, loader):
    try:
        return yaml.load(source, Loader=loader)
    except (yaml.YAMLError, UnicodeError) as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f' on line {mark.line + 1}' if mark else ''
        raise ValueError('Invalid pack YAML' + where + '.') from None


def _fields(value, allowed, label):
    if not isinstance(value, dict) or any(k not in allowed for k in value):
        raise ValueError(f'Unsupported {label} fields. Allowed: ' + ', '.join(sorted(allowed)) + '.')


def _label(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 180 or not value.isprintable():
        raise ValueError('Enter a short printable ' + label + '.')


def _values(value, maximum, label):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{label} must be a list of at most {maximum} entries.')
    return value


def _choice(value, choices, label):
    if value not in choices:
        raise ValueError('Unsupported ' + label + '.')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}.')


def ordered_vms(manifest):
    """Return machine keys in dependency order, retaining declaration order among ready VMs."""
    machines = manifest['machines']
    done = []
    for name, machine in machines.items():
        after = _values(machine.get('after', []), len(machines), 'after')
        if any(not isinstance(dep, str) or dep not in machines for dep in after):
            raise ValueError(f'{name} has an unknown machine in after.')
        if len(set(after)) != len(after):
            raise ValueError(f'{name} repeats a dependency in after.')
    while len(done) < len(machines):
        ready = next((k for k, m in machines.items() if k not in done
                      and all(dep in done for dep in m.get('after', []))), None)
        if ready is None:
            raise ValueError('The machines have a dependency cycle. Check after.')
        done.append(ready)
    return done


def validate_machine_settings(machine, version=1):
    """Validate portable OS, source and settings without consulting local inventory."""
    os_name = machine.get('os')
    source = machine.get('source')
    _fields(source, {'kind', 'hint', 'edition'}, 'source')
    kind = source.get('kind')
    if 'hint' in source:
        hint = source['hint']
        _label(hint, 'source hint')
        if hint.isdigit() or re.search(r'[/\\:]', hint):
            raise ValueError('Use a portable filename or display name as the source hint.')
    if version == 2 and kind == 'existing':
        _fields(source, {'kind', 'hint'}, 'existing guest source')
        _choice(os_name, ('windows', 'linux'), 'existing guest OS')
        if machine.get('settings', {}) != {}:
            raise ValueError('Existing guests accept tasks only. Remove installation and hardware settings.')
        if not _values(machine.get('tasks', []), 50, 'tasks'):
            raise ValueError('Select at least one guest task for an existing machine.')
        return
    _choice(os_name, ('windows', 'ubuntu', 'debian', 'rocky'), 'machine OS')
    _choice(kind, ('iso', 'template', 'cloud') if os_name == 'ubuntu' else ('iso', 'template'), 'source kind')
    if 'edition' in source:
        if os_name != 'windows' or kind != 'iso':
            raise ValueError('Edition labels require a Windows ISO source.')
        _label(source['edition'], 'Windows edition label')
    settings = machine.get('settings', {})
    allowed = {'cores', 'memory', 'disk', 'purpose'}
    if kind == 'template':
        allowed.add('clone_mode')
    if os_name == 'windows':
        allowed.update(('rdp', 'manual_updates', 'windows_settings', 'data_disks', 'locale', 'timezone'))
    else:
        allowed.update(('profile', 'username', 'password_login', 'key_login'))
        if os_name == 'ubuntu':
            allowed.update(('ubuntu_firewall', 'ubuntu_updates'))
            if kind == 'iso':
                allowed.add('ubuntu_install_source')
    _fields(settings, allowed, 'portable VM setting')
    for key, (low, high) in HARDWARE_LIMITS.items():
        if key in settings:
            _integer(settings[key], low, high, key)
    _choice(settings.get('purpose', 'vm'), ('vm',) if kind == 'template' else ('vm', 'template'), 'build purpose')
    _choice(settings.get('clone_mode', 'full'), ('full', 'linked'), 'clone mode')
    for key in ('rdp', 'manual_updates', 'password_login', 'key_login'):
        if key in settings and type(settings[key]) is not bool:
            raise ValueError(key + ' must be true or false.')
    if settings.get('password_login') and settings.get('purpose') == 'template':
        raise ValueError('A Linux template cannot request guest password login.')
    for key in ('profile', 'username', 'locale', 'timezone', 'ubuntu_install_source'):
        if key in settings and (not isinstance(settings[key], str) or
                                not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._ -]{0,63}', settings[key])):
            raise ValueError('Invalid portable ' + key + '.')
    _choice(settings.get('ubuntu_firewall', 'default'), ('default', 'off', 'ssh'), 'Ubuntu firewall setting')
    _choice(settings.get('ubuntu_updates', 'default'), ('default', 'manual', 'security'), 'Ubuntu update setting')
    for option in _values(settings.get('windows_settings', []), len(WINDOWS_SETTINGS), 'windows_settings'):
        _choice(option, [s['id'] for s in WINDOWS_SETTINGS], 'Windows setting')
    for disk in _values(settings.get('data_disks', []), 3, 'data_disks'):
        _fields(disk, {'size', 'label', 'letter'}, 'data disk')
        _integer(disk.get('size'), 1, 4096, 'Data disk size')
        if 'letter' in disk and (not isinstance(disk['letter'], str) or not re.fullmatch('[D-Z]?', disk['letter'])):
            raise ValueError('Invalid data disk letter.')
        if 'label' in disk and (not isinstance(disk['label'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,31}', disk['label'])):
            raise ValueError('Invalid data disk label.')


def _file_kind(path):
    match = TASK_PATH.fullmatch(path) if isinstance(path, str) else None
    if not match or re.match(r'tasks/(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\.', path, re.I):
        raise ValueError('Use a relative tasks/ filename ending in .yaml, .yml, .ps1 or .sh.')
    return EXTENSIONS[match[1]]


def _script_item(path, body, meta, platform):
    kind = _file_kind(path)
    _fields(meta, {'name', 'platform', 'kind', 'parameters', 'defaults', 'input_types'}, 'script metadata')
    if meta.get('platform', platform) != platform or meta.get('kind', kind) != kind:
        raise ValueError('The machine platform or file extension does not match script metadata for ' + path + '.')
    if kind == 'shell' and platform != 'linux':
        raise ValueError('POSIX shell files require a Linux machine.')
    name = meta.get('name', path.split('/')[1])
    _label(name, 'script name')
    if not isinstance(body, str) or not body.strip() or len(body) > 100000 or '\0' in body:
        raise ValueError('Each script must contain 1–100,000 characters of text.')
    inferred = {key: key for key in INPUT_REF.findall(body)} if kind == 'ansible' else {}
    params = meta.get('parameters', inferred)
    if not isinstance(params, dict) or any(not isinstance(k, str) or SECRET_FIELD.search(k) for k in params):
        raise ValueError('Custom pack scripts support named, non-secret inputs only.')
    params, defaults = script_parameters(params, meta.get('defaults', {}))
    if params and kind != 'ansible':
        raise ValueError('Named inputs require an Ansible task list.')
    types = input_type_schema(params, defaults, meta.get('input_types', {}), platform)
    defaults = {**{k: 'false' for k, spec in types.items() if spec['type'] == 'boolean'}, **defaults}
    # The frozen body stays with the selection. Never resolve a pack ID against a local task library.
    digest = hashlib.sha256((path + '\0' + body).encode()).hexdigest()[:32]
    return dict(id='pack-' + digest, name=name, platform=platform, kind=kind, body=body,
                dangerous=True, parameters=params, defaults=defaults, input_types=types)


def _check_templates(value, params):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.startswith('_') or any(marker in key for marker in ('{{', '{%', '{#')):
                raise ValueError('Portable task keys cannot contain templates or internal module options.')
            _check_templates(child, params)
    elif isinstance(value, list):
        for child in value:
            _check_templates(child, params)
    elif isinstance(value, str):
        remaining = INPUT_REF.sub('', value)
        if any(marker in remaining for marker in ('{{', '{%', '{#')) or set(INPUT_REF.findall(value)) - set(params):
            raise ValueError('Portable scripts may interpolate only declared {{ proxmate_inputs.name }} values.')


def _check_file(item):
    body, kind = item['body'], item['kind']
    if kind == 'ansible':
        tasks = _load_yaml(body, _PackLoader)
        _check_templates(tasks, item['parameters'])
        # The library checks module and task keywords. Action plugins for copy and generic
        # providers also have controller-side options which do not belong in a shared pack.
        for task in tasks if isinstance(tasks, list) else ():
            if not isinstance(task, dict):
                continue
            for module, source in (('ansible.builtin.copy', 'src'), ('ansible.windows.win_copy', 'src'),
                                   ('ansible.windows.win_powershell', 'path')):
                if module in task:
                    args = task[module]
                    if not isinstance(args, dict) or (source in args and args.get('remote_src') is not True):
                        raise ValueError(module + ' requires inline content or a guest source with remote_src: true.')
            for module in ('ansible.builtin.package', 'ansible.builtin.service', 'ansible.builtin.dnf'):
                if module in task and (not isinstance(task[module], dict) or
                                       set(task[module]) & {'use', 'use_backend'}):
                    raise ValueError('Portable tasks must use the default guest module provider.')
    else:
        _check_templates(body, {})
    check_script(item['platform'], kind, body)


def _selections(machine, manifest, scripts, secrets, checked=None):
    selections = []
    platform = 'windows' if machine['os'] == 'windows' else 'linux'
    for task in _values(machine.get('tasks', []), 50, 'tasks'):
        if not isinstance(task, dict) or ('run' in task) == ('file' in task):
            raise ValueError('Each ordered task must name one builtin run or one bundled file.')
        if 'run' in task:
            _fields(task, {'run', 'version', 'with'}, 'builtin task')
            ident = task['run']
            if not isinstance(ident, str) or ident not in BUILTIN_V1 or type(task.get('version')) is not int or task['version'] != 1:
                raise ValueError('Unknown pack builtin reference or version. Use a catalog task with version: 1.')
            item = copy.deepcopy(BUILTIN_V1[ident])
            if item['platform'] != platform:
                raise ValueError(ident + ' requires a ' + item['platform'] + ' guest.')
        else:
            _fields(task, {'file', 'with'}, 'file task')
            path = task['file']
            _file_kind(path)
            if path not in scripts:
                raise ValueError('Missing bundled task file: ' + path)
            item = _script_item(path, scripts[path], manifest.get('scripts', {}).get(path, {}), platform)
            if checked is not None and path not in checked:
                _check_file(item)
                checked.add(path)
        supplied = task.get('with', {})
        _fields(supplied, item.get('parameters', {}), 'task input')
        params = {**item.get('defaults', {}), **supplied}
        if set(params) != set(item.get('parameters', {})):
            raise ValueError('Supply all declared inputs for ' + item['name'] + '.')
        for key, value in params.items():
            spec = item.get('input_types', {}).get(key, {})
            if spec.get('type') == 'password':
                if not isinstance(value, dict) or set(value) != {'prompt'} or not isinstance(value['prompt'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', value['prompt']):
                    raise ValueError('Secret task inputs must reference a named prompt. Remove the literal value.')
                if secrets is None:
                    continue  # Import validates the reference before destination credentials exist.
                if value['prompt'] not in secrets:
                    raise ValueError('Supply the destination secret for prompt ' + value['prompt'] + '.')
                value = secrets[value['prompt']]
            elif spec.get('type') == 'boolean' and type(value) is bool:
                value = 'true' if value else 'false'
            validate_input(key, value, spec)
            params[key] = value
        selections.append(dict(item=item, params=params))
    return selections


def resolve_tasks(machine, manifest, scripts, secret_inputs=None):
    """Resolve a validated machine's ordered work with fresh destination prompt values."""
    if secret_inputs is not None and not isinstance(secret_inputs, dict):
        raise ValueError('Supply task prompts as a mapping.')
    return _selections(machine, manifest, scripts, secret_inputs or {})


def _validate(manifest, scripts):
    _fields(manifest, {'version', 'name', 'machines', 'scripts'}, 'lab manifest')
    version = manifest.get('version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported lab pack version. Expected version 1 or 2.')
    _label(manifest.get('name'), 'lab name')
    machines = manifest.get('machines')
    if not isinstance(machines, dict) or not 1 <= len(machines) <= 20:
        raise ValueError('Define 1–20 machines by name.')
    if not isinstance(scripts, dict) or len(scripts) > MAX_FILES:
        raise ValueError('A pack supports up to 32 script files.')
    for path in scripts:
        _file_kind(path)
    if len({p.casefold() for p in scripts}) != len(scripts):
        raise ValueError('Duplicate task filenames in the pack.')
    metadata = manifest.get('scripts', {})
    if not isinstance(metadata, dict) or set(metadata) - set(scripts):
        raise ValueError('Script metadata must name a bundled task file.')
    checked = set()
    names = set()
    selected_bytes = 0
    for name, machine in machines.items():
        if not isinstance(name, str) or not MACHINE_NAME.fullmatch(name) or name.endswith('-') or name.lower() in names:
            raise ValueError('Choose unique machine names of up to 15 letters, numbers or hyphens, beginning with a letter.')
        names.add(name.lower())
        _fields(machine, {'os', 'source', 'after', 'settings', 'tasks'}, 'machine')
        validate_machine_settings(machine, version)
        selections = _selections(machine, manifest, scripts, secrets=None, checked=checked)
        selected_bytes += sum(len(step['item'].get('body', '').encode()) for step in selections)
        if selected_bytes > MAX_SELECTED_BYTES:
            raise ValueError('Repeated task selections exceed the 8 MiB frozen script limit.')
    if set(scripts) != checked:
        raise ValueError('Every bundled script must be selected by a machine.')
    ordered_vms(manifest)


def read_pack(data):
    """Inspect bounded ZIP bytes, returning (manifest, {relative task path: UTF-8 text})."""
    if not isinstance(data, bytes) or len(data) > MAX_PACK_BYTES:
        raise ValueError('Choose a lab ZIP of at most 2 MiB.')
    files = {}
    seen = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_FILES + 2:
                raise ValueError('Too many files in the lab pack.')
            for member in archive.infolist():
                original = member.orig_filename
                # Windows PowerShell's Compress-Archive writes a backslash for the
                # single tasks directory. Canonicalize before duplicate/path checks.
                path = original.replace('\\', '/') if original.startswith('tasks\\') else original
                if path.casefold() in seen:
                    raise ValueError('Duplicate ZIP member.')
                seen.add(path.casefold())
                mode = stat.S_IFMT(member.external_attr >> 16)
                if (member.filename not in (original, path) or '\0' in original or member.flag_bits & 1 or
                        member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('Use regular, unencrypted ZIP entries with portable names.')
                if path == 'tasks/' and member.file_size == 0 and mode in (0, stat.S_IFDIR) and not member.external_attr & 0x400:
                    continue
                if mode not in (0, stat.S_IFREG) or member.external_attr & 0x410:
                    raise ValueError('Links, directories and special files are unsupported in lab packs.')
                limit = MAX_MANIFEST_BYTES if path == 'lab.yaml' else MAX_SCRIPT_BYTES
                if path != 'lab.yaml':
                    _file_kind(path)
                total += member.file_size
                if member.file_size > limit or total > MAX_PACK_BYTES:
                    raise ValueError('The lab ZIP contents exceed the pack size limit.')
                with archive.open(member) as stream:
                    # A forged header can understate the size of a compressed stream.
                    raw = stream.read(member.file_size + 1)
                if len(raw) != member.file_size:
                    raise ValueError('Truncated ZIP member.')
                files[path] = raw.decode('utf-8')
    except (zipfile.BadZipFile, UnicodeError, zlib.error, EOFError, NotImplementedError) as exc:
        raise ValueError('Invalid lab ZIP or UTF-8 file contents.') from exc
    if 'lab.yaml' not in files:
        raise ValueError('The ZIP must contain lab.yaml at its root.')
    manifest = _load_yaml(files.pop('lab.yaml'), _ManifestLoader)
    _validate(manifest, files)
    return manifest, files


class _ManifestDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def write_pack(manifest, scripts):
    """Serialize portable inputs as a deterministic ZIP in declaration order."""
    _validate(manifest, scripts)
    source = yaml.dump(manifest, Dumper=_ManifestDumper, allow_unicode=True, sort_keys=False).encode()
    entries = {'lab.yaml': source, **{path: scripts[path].encode() for path in sorted(scripts)}}
    if len(source) > MAX_MANIFEST_BYTES or sum(map(len, entries.values())) > MAX_PACK_BYTES:
        raise ValueError('The lab contents exceed the pack size limit.')
    # Apply the reader's structural bound to generated manifests as well.
    _load_yaml(source, _ManifestLoader)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for path, content in entries.items():
            member = zipfile.ZipInfo(path)
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, content, compress_type=zipfile.ZIP_DEFLATED)
    data = output.getvalue()
    if len(data) > MAX_PACK_BYTES:
        raise ValueError('The lab ZIP exceeds 2 MiB.')
    return data
