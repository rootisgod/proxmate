"""Opt-in first-boot configuration. Generated files contain no guest passwords."""
import ipaddress
import re
import yaml


def options(value, kind):
    if value is None: return {}
    if not isinstance(value, dict): raise ValueError('Bootstrap options must be an object')
    if not isinstance(value.get('enabled', False), bool): raise ValueError('Bootstrap enabled must be boolean')
    if not value.get('enabled'): return {}
    if kind not in ('windows','linux-cloud'): raise ValueError('Interactive Linux ISOs use the downloadable bootstrap helper after installation')
    result = {'enabled': True, 'verify': value.get('verify', False)}
    if not isinstance(result['verify'],bool): raise ValueError('Bootstrap verify must be boolean')
    if kind=='windows':
        try: subnet=str(ipaddress.ip_network(value.get('management_cidr',''),strict=False))
        except ValueError: raise ValueError('Supply the management client IP/CIDR allowed to reach WinRM') from None
        if ipaddress.ip_network(subnet).prefixlen==0: raise ValueError('Choose a specific management subnet, not every address')
        iso=value.get('agent_iso','')
        if not isinstance(iso,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/[A-Za-z0-9_.-]+\.iso',iso): raise ValueError('Select a trusted VirtIO ISO containing guest-agent/qemu-ga-x86_64.msi')
        result.update(management_cidr=subnet,agent_iso=iso)
        if value.get('agent_sha256'):
            if not isinstance(value['agent_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['agent_sha256']): raise ValueError('Invalid guest agent SHA256')
            result['agent_sha256']=value['agent_sha256']
    else:
        storage=value.get('seed_storage') or value.get('snippet_storage','')
        if not isinstance(storage,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}',storage): raise ValueError('Choose storage with snippets enabled for cloud-init bootstrap')
        result['seed_storage' if value.get('seed_storage') else 'snippet_storage']=storage
    if result['verify']:
        ident=value.get('credential_id','')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident): raise ValueError('Select a saved guest credential for verification')
        result['credential_id']=ident
        result['host']=value.get('host','')
        if not isinstance(result['host'],str) or (result['host'] and not re.fullmatch(r'[A-Za-z0-9:.\-]{1,253}',result['host'])): raise ValueError('Invalid verification address')
        seconds=value.get('timeout',1800)
        if isinstance(seconds,bool) or not str(seconds).isdigit() or not 60 <= int(seconds) <= 3600: raise ValueError('Bootstrap timeout must be 60-3600 seconds')
        result['timeout']=int(seconds)
    return result


def linux_script():
    return '''#!/bin/sh
set -eu
test "$(id -u)" = 0 || { echo 'Run this helper as root inside the guest.'; exit 1; }
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y qemu-guest-agent openssh-server python3 sudo
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y qemu-guest-agent openssh-server python3 sudo
else
  echo 'Supported package managers: apt-get or dnf'; exit 1
fi
ssh-keygen -A
systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd
systemctl start qemu-guest-agent
mkdir -p /var/lib/proxmate
printf 'ready\\n' > /var/lib/proxmate/bootstrap-ready
echo 'Guest bootstrap complete. Verify its SSH host key and login through Proxmate.'
'''


def cloud_vendor():
    # Vendor data preserves Proxmox-generated users, SSH keys and network settings.
    return '#cloud-config\n'+yaml.safe_dump({'package_update':True,'packages':['qemu-guest-agent','openssh-server','python3','sudo'],
        'runcmd':[['sh','-c','ssh-keygen -A && (systemctl enable --now ssh || systemctl enable --now sshd) && systemctl start qemu-guest-agent && mkdir -p /var/lib/proxmate && printf ready > /var/lib/proxmate/bootstrap-ready']]},sort_keys=False)


def cloud_seed(path,username,public_key,hostname):
    """NoCloud media avoids requiring a snippets-enabled Proxmox storage."""
    import io
    import uuid
    import pycdlib
    config=yaml.safe_load(cloud_vendor())
    config.update(users=[{'name':username,'groups':['adm','sudo'],'shell':'/bin/bash','sudo':'ALL=(ALL) NOPASSWD:ALL',
                          'lock_passwd':True,'ssh_authorized_keys':public_key.splitlines()}],ssh_pwauth=False)
    files={'user-data':'#cloud-config\n'+yaml.safe_dump(config),
           'meta-data':yaml.safe_dump({'instance-id':'proxmate-'+uuid.uuid4().hex,'local-hostname':hostname}),
           'network-config':yaml.safe_dump({'version':2,'ethernets':{'lan':{'match':{'name':'e*'},'dhcp4':True}}})}
    iso=pycdlib.PyCdlib();iso.new(interchange_level=3,joliet=3,rock_ridge='1.09',vol_ident='cidata')
    try:
        for i,(name,body) in enumerate(files.items()):
            data=body.encode();iso.add_fp(io.BytesIO(data),len(data),iso_path=f'/FILE{i}.;1',joliet_path='/'+name,rr_name=name)
        iso.write(str(path))
    finally: iso.close()


def windows_script(config):
    from pathlib import Path
    subnet=str(ipaddress.ip_network(config['management_cidr'],strict=False))
    digest=config.get('agent_sha256','')
    if digest and not re.fullmatch(r'[a-f0-9]{64}',digest): raise ValueError('Invalid guest agent SHA256')
    token=config.get('clone_token','')
    if token and not re.fullmatch('[0-9a-f]{32}',token): raise ValueError('Invalid clone identity token')
    return Path(__file__).with_name('windows_bootstrap.ps1').read_text(encoding='utf-8').replace('__SUBNET__',subnet).replace('__AGENT_SHA256__',digest).replace('__CLONE_TOKEN__',token)


WINDOWS_COMMAND = r'''powershell.exe -NoProfile -NonI -ExecutionPolicy Bypass -Command "$d=@(Get-CimInstance Win32_CDROMDrive | Where-Object VolumeName -eq 'PROXMATE_SETUP');if($d.Count -ne 1){throw 'Check setup media'};& (Join-Path ($d[0].Drive+'\') 'ProxmateBootstrap.ps1')"'''
