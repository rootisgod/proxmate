"""Generalize only a newly built, authenticated guest before template conversion."""
import base64
import re
import shlex
import time
from urllib.parse import quote

import operations
from lifecycle import machine_identity


WINDOWS_GENERALIZE=r'''
$ErrorActionPreference='Stop'
$root='C:\ProgramData\Proxmate'
if (-not (Test-Path "$root\Bootstrap.ps1")) { throw 'Bootstrap helper missing; refusing template conversion' }
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
foreach ($file in @('C:\Windows\Panther\unattend.xml','C:\Windows\Panther\Unattend\unattend.xml','C:\Windows\System32\Sysprep\unattend.xml')) {
  Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
}
$process=Start-Process -FilePath "$env:SystemRoot\System32\Sysprep\Sysprep.exe" -ArgumentList '/generalize /oobe /quit /quiet' -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Sysprep failed; VM retained for inspection' }
'''


def finish(cfg,manager,log):
    p=operations.Proxmox();node=cfg['node'];vmid=cfg['vmid'];base=f'nodes/{node}/qemu/{vmid}'
    current=p.call('GET',base+'/config')
    builds=[b for b in manager.all('builds') if b['node']==node and b['vmid']==vmid]
    if not builds or machine_identity(current)!=max(builds,key=lambda b:b['created'])['machine_identity']:
        raise ValueError('Built VM identity changed; refusing template preparation')
    if cfg['kind']=='windows':
        script=WINDOWS_GENERALIZE
        command=['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    else:
        user=cfg['username']
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user): raise ValueError('Invalid Ubuntu username')
        script='set -eu\ncloud-init clean --logs --machine-id\nrm -f /etc/ssh/ssh_host_* /var/lib/proxmate/bootstrap-ready '+shlex.quote('/home/'+user+'/.ssh/authorized_keys')+'\n'
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
    p.wait(node,p.call('POST',base+'/status/shutdown',timeout=120,forceStop=0),log)
    if p.call('GET',base+'/status/current').get('status')!='stopped': raise ValueError('VM did not shut down; no template conversion performed')
    if cfg['kind']=='windows':
        operations.execute(dict(action='finish-setup',node=node,vmid=vmid,prepared=True),log)
        p.call('PUT',base+'/config',delete='ide2,sata2',boot='order=sata0',onboot=0)
    else:
        seed=current.get('ide2','').split(',')[0]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/proxmate-seed-'+str(vmid)+r'-[a-f0-9]{32}\.iso',seed): raise ValueError('Unexpected seed media; no template conversion performed')
        p.call('PUT',base+'/config',ide2=cfg['storage']+':cloudinit',onboot=0)
        p.wait(node,p.call('DELETE',f'nodes/{node}/storage/{seed.split(":")[0]}/content/{quote(seed,safe="")}'),log)
    operations.execute(dict(action='convert-template',node=node,vmid=vmid,prepared=True),log)
    with manager.db() as con: con.execute("DELETE FROM records WHERE kind='connections' AND id=?",(node+':'+str(vmid),))
    log('Template ready. Each clone needs its own login configuration and guest identity.')
