"""Guest-only task catalog and isolated Ansible invocation (trusted administrator code)."""
import base64
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


BUILTINS = [
    dict(id='install-chocolatey', name='Install Chocolatey', platform='windows', description='Install the Chocolatey package manager.', parameters={}),
    dict(id='chocolatey-package', name='Install a Chocolatey package', platform='windows', description='Install a named package without allowing checksum bypass.', parameters={'package': 'Package ID, e.g. 7zip'}),
    dict(id='enable-rdp', name='Enable Remote Desktop', platform='windows', description='Enable RDP with NLA and open domain/private firewall profiles.', parameters={}),
    dict(id='disable-firewall', name='Disable Windows firewall', platform='windows', description='Disables all three firewall profiles. Requires separate risk confirmation.', dangerous=True, parameters={}),
    dict(id='enable-firewall', name='Enable Windows firewall', platform='windows', description='Enable all three firewall profiles.', parameters={}),
    dict(id='windows-updates', name='Install Windows updates', platform='windows', description='Install security and critical updates. Does not reboot automatically.', parameters={}),
    dict(id='linux-package', name='Install a Linux package', platform='linux', description='Install using the guest package manager. Requires sudo.', parameters={'package': 'Package name, e.g. curl'}),
    dict(id='linux-updates', name='Update Linux packages', platform='linux', description='Refresh metadata and upgrade packages on Ubuntu, Fedora or Rocky. Does not reboot.', parameters={}),
    dict(id='linux-service', name='Manage a Linux service', platform='linux', description='Start, stop or restart a service. Requires sudo.', parameters={'service': 'Service name', 'state': 'started, stopped or restarted'}),
    dict(id='windows-service', name='Manage a Windows service', platform='windows', description='Start, stop or restart a service.', parameters={'service': 'Service name', 'state': 'started, stopped or restarted'}),
]


def compile_task(item, params):
    if not isinstance(params, dict) or set(params) != set(item.get('parameters', {})):
        raise ValueError('Supply exactly the parameters listed for ' + item['name'])
    for key, value in params.items():
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+@:-]{0,127}', value):
            raise ValueError('Invalid task parameter: ' + key)
    if 'state' in params and params['state'] not in ('started', 'stopped', 'restarted'):
        raise ValueError('Service state must be started, stopped or restarted')
    ident = item['id']
    if ident == 'install-chocolatey': return [{'chocolatey.chocolatey.win_chocolatey': {'name': 'chocolatey', 'state': 'present'}}]
    if ident == 'chocolatey-package': return [{'chocolatey.chocolatey.win_chocolatey': {'name': params['package'], 'state': 'present'}}]
    if ident == 'enable-rdp':
        return [{'ansible.windows.win_regedit': {'path': r'HKLM:\System\CurrentControlSet\Control\Terminal Server', 'name': 'fDenyTSConnections', 'data': 0, 'type': 'dword'}},
                {'ansible.windows.win_regedit': {'path': r'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp', 'name': 'UserAuthentication', 'data': 1, 'type': 'dword'}},
                {'ansible.windows.win_powershell': {'script': "Get-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP','RemoteDesktop-UserMode-In-UDP' | Set-NetFirewallRule -Enabled True -Profile Domain,Private"}}]
    if ident in ('disable-firewall', 'enable-firewall'):
        return [{'ansible.windows.win_powershell': {'script': 'Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled $' + ('false' if ident == 'disable-firewall' else 'true')}}]
    if ident == 'windows-updates': return [{'ansible.windows.win_updates': {'category_names': ['SecurityUpdates', 'CriticalUpdates'], 'reboot': False}}]
    if ident == 'linux-package': return [{'ansible.builtin.package': {'name': params['package'], 'state': 'present'}}]
    if ident == 'linux-updates':
        return [{'ansible.builtin.apt': {'upgrade': 'dist', 'update_cache': True}, 'when': "ansible_facts['os_family'] == 'Debian'"},
                {'ansible.builtin.dnf': {'name': '*', 'state': 'latest', 'update_cache': True}, 'when': "ansible_facts['os_family'] == 'RedHat'"}]
    if ident in ('linux-service', 'windows-service'):
        return [{('ansible.builtin.service' if ident == 'linux-service' else 'ansible.windows.win_service'): {'name': params['service'], 'state': params['state']}}]
    if item['kind'] == 'powershell': return [{'ansible.windows.win_powershell': {'script': item['body']}}]
    if item['kind'] == 'shell': return [{'ansible.builtin.shell': {'cmd': item['body']}}]
    # Saved Ansible snippets are task lists. This is deliberately an admin code feature,
    # not an untrusted multi-tenant execution sandbox.
    return yaml.safe_load(item['body'])


def validate_script(body):
    try: tasks = yaml.safe_load(body)
    except yaml.YAMLError: raise ValueError('Invalid Ansible task YAML') from None
    if not isinstance(tasks, list) or not tasks or len(tasks) > 100:
        raise ValueError('Provide a YAML list of 1-100 Ansible tasks, not a full playbook')
    # Prevent accidental host/controller delegation and inventory/connection overrides.
    allowed_modules = {'ansible.builtin.command', 'ansible.builtin.shell', 'ansible.builtin.package',
                       'ansible.builtin.apt', 'ansible.builtin.dnf', 'ansible.builtin.service',
                       'ansible.builtin.copy', 'ansible.builtin.file', 'ansible.builtin.lineinfile',
                       'ansible.windows.win_powershell', 'ansible.windows.win_shell',
                       'ansible.windows.win_command', 'ansible.windows.win_service',
                       'ansible.windows.win_copy', 'ansible.windows.win_file',
                       'ansible.windows.win_regedit', 'ansible.windows.win_updates',
                       'chocolatey.chocolatey.win_chocolatey'}
    for task in tasks:
        if not isinstance(task, dict): raise ValueError('Each task must be an object')
        modules = set(task) & allowed_modules
        if len(modules) != 1 or set(task) - allowed_modules - {'name'}:
            raise ValueError('Use one supported fully qualified guest module per task, with an optional name; delegation and connection overrides are not supported')
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
