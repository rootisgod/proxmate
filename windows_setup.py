"""Reviewed, fixed Ansible tasks for newly provisioned Windows guests."""
import ipaddress


PROGRAMS = [
    ('vscode', 'Visual Studio Code'), ('git', 'Git'), ('7zip', '7-Zip'),
    ('notepadplusplus', 'Notepad++'), ('powershell-core', 'PowerShell 7'),
    ('microsoft-windows-terminal', 'Windows Terminal'), ('python3', 'Python 3'),
    ('nodejs-lts', 'Node.js LTS'),
]

OPTIONS = [
    dict(id='openssh', name='Enable OpenSSH Server', detail='Allow TCP 22 from the reviewed management network.'),
    dict(id='edge-first-run', name='Skip Edge first-run prompts', desktop=True),
    dict(id='explorer', name='Show hidden files and file extensions', desktop=True,
         detail='Apply to the build account and new user profiles.'),
    dict(id='server-manager', name='Keep Server Manager closed at sign-in', desktop=True),
    dict(id='ie-esc', name='Disable legacy IE Enhanced Security', desktop=True, advanced=True,
         detail='Applies to administrators and users where the legacy components exist.'),
    dict(id='disable-firewall', name='Turn off Windows Firewall', advanced=True,
         detail='Turns off Domain, Private and Public profiles, including their inbound restrictions.'),
    dict(id='console-sign-in', name='Sign in automatically once at the next boot', vm_only=True, advanced=True,
         detail='Opens the VM console as the build Administrator. Windows stores the login in an LSA secret accessible to local administrators.'),
]


SSH_RULE = r"""
param([string]$Subnet)
$name = 'OpenSSH-Server-In-TCP'
$rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue
if (!$rule) {
    New-NetFirewallRule -Name $name -DisplayName 'OpenSSH Server' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress $Subnet | Out-Null
    return
}
$port = $rule | Get-NetFirewallPortFilter
if ($rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or $port.Protocol -ne 'TCP' -or $port.LocalPort -ne '22') {
    throw 'The existing OpenSSH firewall rule has a different purpose. Inspect the rule inside the VM.'
}
$addresses = @(($rule | Get-NetFirewallAddressFilter).RemoteAddress)
$Ansible.Changed = ($rule.Enabled -ne 'True' -or $addresses.Count -ne 1 -or $addresses[0] -ne $Subnet)
if ($Ansible.Changed) {
    $rule | Set-NetFirewallRule -Enabled True -RemoteAddress $Subnet
}
"""


# The guided ISO build still has its reviewed installer and companion discs mounted.
NETFX_MEDIA = r"""
$Ansible.Changed = $false
$sources = @(foreach ($cd in Get-CimInstance -ClassName Win32_CDROMDrive -ErrorAction Stop) {
    if (!$cd.Drive -or !$cd.MediaLoaded) { continue }
    $root = Join-Path $cd.Drive 'sources'
    $sxs = Join-Path $root 'sxs'
    if ((Test-Path -LiteralPath $sxs -PathType Container) -and
        ((Test-Path -LiteralPath (Join-Path $root 'install.wim')) -or
         (Test-Path -LiteralPath (Join-Path $root 'install.esd')))) { $sxs }
})
$Ansible.Result = if ($sources.Count -eq 1) { $sources[0] } else { '' }
"""


def dword(path, name, data, **extra):
    return {'ansible.windows.win_regedit': dict(path=path, name=name, type='dword', data=data, **extra)}


def steps(ident, subnet):
    if ident == 'openssh':
        network = ipaddress.ip_network(subnet, strict=False)
        if not network.prefixlen:
            raise ValueError('Choose a specific management network for OpenSSH')
        scope = str(network.network_address) if network.num_addresses == 1 else str(network)
        return [
            {'ansible.windows.win_capability': {'name':'OpenSSH.Server~~~~0.0.1.0','state':'present'}, 'register':'ssh_install'},
            {'ansible.windows.win_reboot': {'reboot_timeout':900}, 'when':'ssh_install.reboot_required'},
            {'ansible.windows.win_powershell': {'script':SSH_RULE,'parameters':{'Subnet':scope}}},
            {'ansible.windows.win_service': {'name':'sshd','start_mode':'auto','state':'started'}},
        ]
    if ident == 'edge-first-run':
        return [dword(r'HKLM:\SOFTWARE\Policies\Microsoft\Edge','HideFirstRunExperience',1)]
    if ident == 'server-manager':
        return [dword(r'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Server\ServerManager','DoNotOpenAtLogon',1)]
    if ident == 'explorer':
        path = r'\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
        tasks = []
        for key, value in [('Hidden',1), ('HideFileExt',0)]:
            tasks.append(dword('HKCU:'+path,key,value))
            tasks.append(dword(r'HKLM:\ANSIBLE'+path,key,value,hive=r'C:\Users\Default\NTUSER.DAT'))
        return tasks
    if ident == 'ie-esc':
        tasks = []
        for role in ('A7','A8'):
            path = r'HKLM:\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1'+role+'-37EF-4b3f-8CFC-4F3A74704073}'
            tasks.append({'ansible.windows.win_reg_stat':{'path':path,'name':'IsInstalled'},'register':'ie_component'})
            tasks.append(dict(dword(path,'IsInstalled',0),when='ie_component.exists'))
        return tasks
    if ident == 'console-sign-in':
        return [{'ansible.windows.win_auto_logon': {'state':'present','username':'{{ ansible_user }}',
                 'password':'{{ ansible_password }}','logon_count':1},'no_log':True}]
    raise ValueError('Unknown Windows setup option')


def select(requested, tasks, manager, *, platform, purpose, subnet, desktop):
    if not isinstance(requested,list) or any(not isinstance(i,str) for i in requested):
        raise ValueError('Select Windows setup options from the list')
    chosen = set(requested)
    if chosen - {o['id'] for o in OPTIONS} or (chosen and platform!='windows'):
        raise ValueError('Choose available setup options for a Windows build')
    before, after = [], []
    for option in OPTIONS:
        ident = option['id']
        if ident not in chosen: continue
        if option.get('vm_only') and purpose!='vm':
            raise ValueError('Automatic console sign-in is available only for working VMs')
        if option.get('desktop') and desktop is False:
            raise ValueError(option['name']+' requires Desktop Experience')
        if ident=='disable-firewall':
            item = manager.task(ident)
        else:
            item = dict(id='setup-'+ident, kind='windows-setup', name=option['name'], platform='windows',
                        dangerous=bool(option.get('advanced')), steps=steps(ident,subnet))
        group = after if option.get('advanced') else before
        group.append({'item':item,'params':{}})
    return before + tasks + after
