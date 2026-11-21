"""Guest-only task catalog and isolated Ansible invocation (trusted administrator code)."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

import paramiko
import yaml

from script_library import EXAMPLES, validate_script


SERVICE_STATE_INPUT = {'state': {'type': 'choice', 'choices': ['started', 'stopped', 'restarted']}}

BUILTINS = [
    dict(id='install-chocolatey', name='Install Chocolatey', platform='windows', description='Install the Chocolatey package manager.', parameters={}),
    dict(id='chocolatey-package', name='Install a Chocolatey package', platform='windows', description='Install a named package without allowing checksum bypass.', parameters={'package': 'Package ID, e.g. 7zip'}),
    dict(id='windows-feature', name='Install a Windows Server feature (restart if needed)', platform='windows', description='Install a role or feature with its management tools. Restart automatically if required.', parameters={'feature':'Feature name, e.g. Web-Server'}),
    dict(id='enable-rdp', name='Enable Remote Desktop', platform='windows', description='Enable RDP with NLA and open domain/private firewall profiles.', parameters={}),
    dict(id='disable-firewall', name='Disable Windows firewall', platform='windows', description='Disables all three firewall profiles. Requires separate risk confirmation.', dangerous=True, parameters={}),
    dict(id='enable-firewall', name='Enable Windows firewall', platform='windows', description='Enable all three firewall profiles.', parameters={}),
    dict(id='windows-manual-updates',name='Use manual Windows updates',platform='windows',description='Disable automatic updates for this lab machine using its local update policy.',parameters={}),
    dict(id='windows-updates', name='Install Windows updates', platform='windows', description='Install security and critical updates. Does not reboot automatically.', parameters={}),
    dict(id='linux-package', name='Install a Linux package', platform='linux', description='Install using the guest package manager. Requires sudo.', parameters={'package': 'Package name, e.g. curl'}),
    dict(id='linux-updates', name='Update Linux packages', platform='linux', description='Refresh metadata and upgrade packages on Ubuntu, Fedora or Rocky. Does not reboot.', parameters={}),
    dict(id='linux-service', name='Manage a Linux service', platform='linux', description='Start, stop or restart a service. Requires sudo.', parameters={'service': 'Service name', 'state': 'Service action'}, input_types=SERVICE_STATE_INPUT),
    dict(id='windows-service', name='Manage a Windows service', platform='windows', description='Start, stop or restart a service.', parameters={'service': 'Service name', 'state': 'Service action'}, input_types=SERVICE_STATE_INPUT),
] + EXAMPLES

PLAIN_VALUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+@:-]{0,127}')


def input_type_schema(parameters, defaults, types, platform):
    if not isinstance(types,dict) or set(types)-set(parameters):
        raise ValueError('Specify input types only for declared variables')
    for key, spec in types.items():
        if not isinstance(spec,dict) or spec.get('type') not in ('text','choice','boolean','drive'):
            raise ValueError('Invalid input type for '+key)
        kind=spec['type']
        if set(spec) != ({'type','choices'} if kind=='choice' else {'type'}):
            raise ValueError('Unexpected input settings for '+key)
        if kind=='choice':
            choices=spec['choices']
            if (not isinstance(choices,list) or not 1<=len(choices)<=50 or
                    not all(isinstance(v,str) and PLAIN_VALUE.fullmatch(v) for v in choices) or len(set(choices))!=len(choices)):
                raise ValueError('Supply 1–50 unique plain choices for '+key)
        if kind=='drive' and platform!='windows':
            raise ValueError('Drive-letter inputs require a Windows script')
        if key in defaults: validate_input(key, defaults[key], spec)
    return types


def validate_input(key,value,spec):
    if not isinstance(value,str) or not PLAIN_VALUE.fullmatch(value):
        raise ValueError('Invalid task parameter: '+key)
    kind=spec.get('type')
    if kind=='choice' and value not in spec['choices']: raise ValueError('Choose a listed value for '+key)
    if kind=='boolean' and value not in ('true','false'): raise ValueError('Choose yes or no for '+key)
    if kind=='drive' and not re.fullmatch('[D-Z]',value): raise ValueError('Choose a drive letter from D to Z for '+key)


def selected_drive_letters(selections):
    return {s['params'][key] for s in selections for key,spec in s['item'].get('input_types',{}).items()
            if spec['type']=='drive'}


def script_parameters(schema, defaults):
    if not isinstance(schema,dict) or len(schema)>20 or not isinstance(defaults,dict) or set(defaults)-set(schema):
        raise ValueError('Define up to 20 named inputs and defaults for those inputs')
    for key,label in schema.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',key) or not isinstance(label,str) or not 1<=len(label.strip())<=100:
            raise ValueError('Give each input a lowercase name and a short label')
    for value in defaults.values():
        if not isinstance(value,str) or not PLAIN_VALUE.fullmatch(value):
            raise ValueError('Use a single plain value for each input default')
    return schema,defaults


def compile_task(item, params):
    if not isinstance(params, dict) or set(params) != set(item.get('parameters', {})):
        raise ValueError('Supply exactly the parameters listed for ' + item['name'])
    for key, value in params.items():
        validate_input(key,value,item.get('input_types',{}).get(key,{}))
    if item['id'] in ('linux-service','windows-service') and 'state' in params and params['state'] not in ('started', 'stopped', 'restarted'):
        raise ValueError('Service state must be started, stopped or restarted')
    ident = item['id']
    if item.get('kind') in ('windows-setup','ubuntu-setup'): return copy.deepcopy(item['steps'])
    if ident == 'install-chocolatey': return [{'chocolatey.chocolatey.win_chocolatey': {'name': 'chocolatey', 'state': 'present'}}]
    if ident == 'chocolatey-package': return [{'chocolatey.chocolatey.win_chocolatey': {'name': params['package'], 'state': 'present'}}]
    if ident == 'windows-feature':
        feature = {'name': params['feature'], 'state': 'present', 'include_management_tools': True}
        tasks = []
        if item.get('installer_sxs') and params['feature'] == 'NET-Framework-Core':
            from windows_setup import NETFX_MEDIA
            tasks.append({'name': 'Find .NET feature files on Windows installation media',
                          'ansible.windows.win_powershell': {'script': NETFX_MEDIA}, 'register': 'proxmate_netfx_media'})
            feature['source'] = '{{ proxmate_netfx_media.result | default(omit, true) }}'
        return tasks + [{'ansible.windows.win_feature': feature, 'register': 'proxmate_feature'},
                        {'ansible.windows.win_reboot': {'reboot_timeout': 900}, 'when': 'proxmate_feature.reboot_required'}]
    if ident == 'enable-rdp':
        return [{'ansible.windows.win_regedit': {'path': r'HKLM:\System\CurrentControlSet\Control\Terminal Server', 'name': 'fDenyTSConnections', 'data': 0, 'type': 'dword'}},
                {'ansible.windows.win_regedit': {'path': r'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp', 'name': 'UserAuthentication', 'data': 1, 'type': 'dword'}},
                {'ansible.windows.win_powershell': {'script': "Get-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP','RemoteDesktop-UserMode-In-UDP' | Set-NetFirewallRule -Enabled True -Profile Domain,Private"}}]
    if ident in ('disable-firewall', 'enable-firewall'):
        return [{'ansible.windows.win_powershell': {'script': 'Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled $' + ('false' if ident == 'disable-firewall' else 'true')}}]
    if ident == 'windows-manual-updates':
        return [{'ansible.windows.win_regedit':{'path':r'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU','name':'NoAutoUpdate','type':'dword','data':1}}]
    if ident == 'prepare-created-volume':
        from windows_build import volume_task
        return [volume_task(item['disk'])]
    if ident == 'windows-updates': return [{'ansible.windows.win_updates': {'category_names': ['SecurityUpdates', 'CriticalUpdates'], 'reboot': False}}]
    if ident == 'linux-package': return [{'ansible.builtin.package': {'name': params['package'], 'state': 'present'}}]
    if ident == 'linux-updates':
        return [{'ansible.builtin.apt': {'upgrade': 'dist', 'update_cache': True}, 'when': "ansible_facts['os_family'] == 'Debian'"},
                {'ansible.builtin.dnf': {'name': '*', 'state': 'latest', 'update_cache': True}, 'when': "ansible_facts['os_family'] == 'RedHat'"}]
    if ident in ('linux-service', 'windows-service'):
        return [{('ansible.builtin.service' if ident == 'linux-service' else 'ansible.windows.win_service'): {'name': params['service'], 'state': params['state']}}]
    if item['kind'] == 'powershell' and item['platform'] == 'linux':
        return [{'ansible.builtin.command': {
            'argv': ['pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                     "$ErrorActionPreference='Stop'; & ([scriptblock]::Create([Console]::In.ReadToEnd()))"],
            'stdin': item['body'], 'stdin_add_newline': False, 'expand_argument_vars': False}}]
    if item['kind'] == 'powershell': return [{'ansible.windows.win_powershell': {'script': item['body']}}]
    if item['kind'] == 'shell': return [{'ansible.builtin.shell': {'cmd': item['body']}}]
    # Saved Ansible snippets are task lists. This is deliberately an admin code feature,
    # not an untrusted multi-tenant execution sandbox.
    tasks=validate_script(item['body'])
    if params:
        inputs={k:(v=='true' if item.get('input_types',{}).get(k,{}).get('type')=='boolean' else v) for k,v in params.items()}
        for task in tasks: task['vars']={'proxmate_inputs':inputs}
    return tasks


def known_host(binding):
    """Verify a presented SSH key against the independently supplied SHA256 pin."""
    sock = socket.create_connection((binding['host'], binding['port']), timeout=10)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if fingerprint != binding['fingerprint']: raise ValueError('Guest SSH fingerprint mismatch')
        host = binding['host'] if binding['port'] == 22 else f'[{binding["host"]}]:{binding["port"]}'
        return f'{host} {key.get_name()} {key.get_base64()}\n'
    finally:
        transport.close()
        sock.close()


def run_guest(binding, credential, tasks, log, readiness=False):
    """Suppress module output: arbitrary scripts can print credentials or encoded secrets."""
    with tempfile.TemporaryDirectory(prefix='proxmate-guest-') as tmp:
        root = Path(tmp)
        root.chmod(0o700)
        def write(name, value):
            path = root / name
            path.write_text(value, encoding='utf-8'); path.chmod(0o600)
            return str(path)
        hostvars = {'ansible_host': binding['host'], 'ansible_port': binding['port'],
                    'ansible_user': credential['username'], 'ansible_password': credential.get('password', ''),
                    'ansible_connect_timeout': 15}
        if binding['platform'] == 'windows':
            hostvars.update(ansible_connection='winrm', ansible_winrm_scheme='https', ansible_winrm_transport='ntlm',
                            ansible_winrm_server_cert_validation='validate' if binding['verify_tls'] else 'ignore',
                            ansible_winrm_connection_timeout=30)
            if binding.get('ca_pem') or credential.get('ca_pem'): hostvars['ansible_winrm_ca_trust_path'] = write('ca.pem', binding.get('ca_pem') or credential['ca_pem'])
        else:
            kh = write('known_hosts', known_host(binding))
            hostvars.update(ansible_connection='ssh', ansible_ssh_common_args=f'-o StrictHostKeyChecking=yes -o UserKnownHostsFile={kh} -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=15',
                            ansible_become_password=credential.get('become_password') or credential.get('password', ''),
                            ansible_python_interpreter='auto_silent')
            if credential.get('private_key'):
                hostvars['ansible_ssh_private_key_file'] = write('private_key', credential['private_key'])
        inv = write('inventory.yml', yaml.safe_dump({'all': {'children': {'proxmate_guests': {'hosts': {'guest': hostvars}}}}}))
        if readiness:
            tasks = [{'name': 'Authenticate and check guest runtime', ('ansible.windows.win_ping' if binding['platform'] == 'windows' else 'ansible.builtin.ping'): {}}]
        # no_log applied to every task and the play; callback output is never published.
        tasks = [dict(t, no_log=True) for t in tasks]
        for task in tasks:
            if isinstance(task.get('ansible.windows.win_powershell'), dict):
                task['ansible.windows.win_powershell'].setdefault('error_action', 'stop')
        play = [{'name': 'Proxmate guest operation', 'hosts': 'proxmate_guests', 'gather_facts': not readiness and binding['platform']=='linux',
                 'become': not readiness and binding['platform']=='linux' and binding.get('become', True), 'no_log': True, 'tasks': tasks}]
        playbook = write('playbook.yml', yaml.safe_dump(play, sort_keys=False))
        # Do not inherit mounted hypervisor credentials as environment variables.
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'TEMP', 'TMP', 'ANSIBLE_COLLECTIONS_PATH')}
        env.update(ANSIBLE_HOST_KEY_CHECKING='True', ANSIBLE_NOCOLOR='1', ANSIBLE_LOCAL_TEMP=str(root/'ansible-tmp'), ANSIBLE_RETRY_FILES_ENABLED='False')
        try:
            result = subprocess.run(['ansible-playbook', '-i', inv, playbook], cwd=tmp, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90 if readiness else 3600)
        except subprocess.TimeoutExpired: raise RuntimeError('Guest operation timed out; inspect the guest before retrying') from None
        if result.returncode:
            raise RuntimeError(f'Guest Ansible run failed (exit {result.returncode}). Check credentials, connectivity, privileges and module requirements. Script output is withheld to protect secrets.')
        log('Guest authentication/runtime ready.' if readiness else 'Guest task completed. Output withheld to protect secrets; updates may require a reboot.')
