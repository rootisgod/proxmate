"""Generalize only a newly built, authenticated guest before template conversion."""
import base64
import re
import shlex
import time
from urllib.parse import quote

import operations
from vm_config import machine_identity


WINDOWS_GENERALIZE=r'''
$ErrorActionPreference='Stop'
$root='C:\ProgramData\Proxmate'
if (-not (Test-Path "$root\Bootstrap.ps1")) { throw 'Bootstrap helper missing; refusing template conversion' }
# A guided clone boots with one DVD. Windows can reassign drive letters after Sysprep.
$cloneAnswer = '\\?\GLOBALROOT\Device\CdRom0\Autounattend.xml'
# A clone must generate its own WinRM certificate and readiness marker.
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\Proxmate\Bootstrap.ps1'
$trigger=New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName 'Proxmate-CloneBootstrap' -Action $action -Trigger $trigger -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
if (Test-Path "$root\winrm.pem") {
  $cert=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("$root\winrm.pem")
  Remove-Item -LiteralPath ('Cert:\LocalMachine\My\'+$cert.Thumbprint) -Force -ErrorAction SilentlyContinue
}
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' } | Remove-Item -Recurse -Force
Unregister-ScheduledTask -TaskName 'Proxmate-Bootstrap' -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$root\winrm.pem","$root\bootstrap-ready","$root\winrm-ready","$root\winrm-state.json","$root\bootstrap-attempt","$root\bootstrap-status.json" -Force -ErrorAction SilentlyContinue
function Remove-ProxmateCachedAnswer {
  foreach ($file in @('C:\Windows\Panther\unattend.xml','C:\Windows\Panther\unattend-original.xml','C:\Windows\Panther\Unattend\unattend.xml','C:\Windows\System32\Sysprep\unattend.xml')) {
    if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force -ErrorAction Stop }
  }
}
Remove-ProxmateCachedAnswer
$process=Start-Process -FilePath "$env:SystemRoot\System32\Sysprep\Sysprep.exe" -ArgumentList '/generalize /oobe /quit /quiet' -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Sysprep failed; VM retained for inspection' }
# Generalization can cache the still-attached installer answer again.
Remove-ProxmateCachedAnswer
# Generalized Windows can skip implicit DVD discovery during specialize.
New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\Setup' -Name UnattendFile -Value $cloneAnswer -PropertyType String -Force | Out-Null
'''


def finish(cfg,manager,log,expected_identity):
    p=operations.Proxmox();node=cfg['node'];vmid=cfg['vmid'];base=f'nodes/{node}/qemu/{vmid}'
    def owned_config():
        current=p.call('GET',base+'/config')
        if current.get('template') or machine_identity(current)!=expected_identity:
            raise ValueError('Built VM identity changed. Inspect it before template preparation.')
        return current
    current=owned_config()
    if cfg['kind']=='windows':
        script=WINDOWS_GENERALIZE
        command=['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    else:
        user=cfg['username']
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user): raise ValueError('Invalid Ubuntu username')
        clean='cloud-init clean --logs --machine-id'
        if cfg['kind']=='ubuntu-iso':
            clean+=' --seed'
        script='set -eu\n'+clean+'\nrm -f /etc/ssh/ssh_host_* /var/lib/proxmate/bootstrap-ready '+shlex.quote('/home/'+user+'/.ssh/authorized_keys')+'\n'
        if cfg['kind']=='ubuntu-iso':
            script+=('rm -f /etc/cloud/cloud.cfg.d/99-installer.cfg '
                     '/etc/cloud/cloud.cfg.d/subiquity-disable-cloudinit-networking.cfg '
                     '/etc/netplan/00-installer-config.yaml /etc/netplan/50-cloud-init.yaml\n')
        command=['/bin/sh','-c',script]
    log('Preparing reusable template: cleaning guest identity and setup secrets.')
    pid=p.call('POST',base+'/agent/exec',command=command)['pid']
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        status=p.call('GET',base+'/agent/exec-status',pid=pid)
        if status.get('exited'):
            if status.get('exitcode')!=0: raise RuntimeError('Guest generalization failed. Inspect the retained VM; it was not converted.')
            break
        time.sleep(5)
    else: raise RuntimeError('Generalization timed out. Inspect the retained VM; it was not converted.')
    owned_config()
    p.wait(node,p.call('POST',base+'/status/shutdown',timeout=120,forceStop=0),log)
    if p.call('GET',base+'/status/current').get('status')!='stopped': raise ValueError('VM did not shut down; no template conversion performed')
    current=owned_config()
    if cfg['kind']=='windows':
        operations.execute(dict(action='finish-setup',node=node,vmid=vmid,prepared=True),log)
        p.call('PUT',base+'/config',delete='ide2,sata2',boot='order=sata0',onboot=0)
    elif cfg['kind']=='ubuntu-iso':
        if current.get('ide2') or current.get('sata1'):
            raise ValueError('Ubuntu installation media is still attached. Inspect the guest before converting.')
        p.call('PUT',base+'/config',ide2=cfg['storage']+':cloudinit',ipconfig0='ip=dhcp',
               ciuser=cfg['username'],onboot=0,digest=current['digest'])
    else:
        seed=current.get('ide2','').split(',')[0]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/proxmate-seed-'+str(vmid)+r'-[a-f0-9]{32}\.iso',seed): raise ValueError('Unexpected seed media; no template conversion performed')
        p.call('PUT',base+'/config',ide2=cfg['storage']+':cloudinit',onboot=0)
        p.wait(node,p.call('DELETE',f'nodes/{node}/storage/{seed.split(":")[0]}/content/{quote(seed,safe="")}'),log)
    owned_config()
    operations.execute(dict(action='convert-template',node=node,vmid=vmid,prepared=True),log)
    with manager.db() as con: con.execute("DELETE FROM records WHERE kind='connections' AND id=?",(node+':'+str(vmid),))
    log('Template ready. Each clone needs its own login configuration and guest identity.')
