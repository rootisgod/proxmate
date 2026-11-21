"""Inspect a selected Windows guest and queue a fixed, scoped HTTPS listener setup."""
import base64
import ipaddress
import json
import re
import time
import uuid

from flask import jsonify, request
from requests import RequestException
import management
import operations
from vm_config import machine_identity, vm_path


INSPECT=r"""
$ErrorActionPreference='Stop'
$svc=Get-Service WinRM
if ($svc.Status -ne 'Running') { Write-Output 'stopped'; exit 0 }
$https=@(Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' })
if (!$https.Count) { Write-Output 'no-listener'; exit 0 }
Write-Output 'https-listener'
"""

SETUP=r"""
$ErrorActionPreference='Stop'
$rule='Proxmate-Guest-HTTPS'
$service=Get-Service WinRM
if ($service.Status -ne 'Running') {
    if ($service.StartType -eq 'Disabled') { Set-Service WinRM -StartupType Automatic }
    Start-Service WinRM
}
$listeners=@(Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' })
if ($listeners.Count) { Write-Output 'An HTTPS listener already exists. Check its certificate and firewall scope.'; return }
if (Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue) { throw 'Review the existing Proxmate firewall rule before continuing.' }
Set-Service WinRM -StartupType Automatic
$ips=@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object -ExpandProperty IPAddress)
if (!$ips.Count) { throw 'No usable guest IPv4 address' }
$san='2.5.29.17={text}DNS='+$env:COMPUTERNAME
foreach ($ip in $ips) { $san+='&IPAddress='+$ip }
$cert=New-SelfSignedCertificate -Subject ('CN='+$env:COMPUTERNAME) -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 2048 -TextExtension @($san,'2.5.29.37={text}1.3.6.1.5.5.7.3.1')
$createdRule=$null
try {
    $createdRule=New-NetFirewallRule -Name $rule -DisplayName 'Proxmate guest HTTPS WinRM' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 -RemoteAddress '__SCOPE__'
    New-Item WSMan:\localhost\Listener -Transport HTTPS -Address '*' -CertificateThumbprint $cert.Thumbprint -Force | Out-Null
} catch {
    if ($createdRule) { $createdRule | Remove-NetFirewallRule }
    Remove-Item -LiteralPath ('Cert:\LocalMachine\My\'+$cert.Thumbprint) -Force
    throw
}
'-----BEGIN CERTIFICATE-----'
[Convert]::ToBase64String($cert.RawData,[Base64FormattingOptions]::InsertLineBreaks)
'-----END CERTIFICATE-----'
"""


def subnet(value):
    try: net=ipaddress.ip_network(value,strict=False)
    except (TypeError,ValueError): raise ValueError('Enter the management client IP or subnet allowed to reach port 5986') from None
    if net.prefixlen==0 or net.is_multicast or net.is_loopback or net.is_unspecified: raise ValueError('Choose a specific management network')
    return str(net)


def agent_script(p,target,identity,script):
    path=vm_path(target)
    def check():
        cfg=p.call('GET',path+'/config')
        if cfg.get('template') or not identity or machine_identity(cfg)!=identity: raise ValueError('Machine identity changed. Review this VM again.')
    check()
    os=p.call('GET',path+'/agent/get-osinfo').get('result',{})
    if os.get('id','').lower() not in ('mswindows','windows','win32'): raise ValueError('Guest agent must report Windows')
    command=['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    check()
    pid=p.call('POST',path+'/agent/exec',command=command)['pid']
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        result=p.call('GET',path+'/agent/exec-status',pid=pid)
        if result.get('exited'):
            if result.get('exitcode')!=0: raise RuntimeError('Guest command failed. Inspect Windows through its console.')
            check()
            return result.get('out-data','').strip()
        time.sleep(.5)
    raise RuntimeError('Guest command timed out. Inspect Windows before retrying.')


def used_drive_letters(p,target,config):
    if not (config.get('smbios1') or config.get('vmgenid')):
        raise ValueError('Guest identity is unavailable for drive discovery.')
    probe = r"""$ErrorActionPreference='Stop'
$letters=@([System.IO.Directory]::GetLogicalDrives() | ForEach-Object { $_.Substring(0,1) })
ConvertTo-Json -Compress -InputObject $letters"""
    result=json.loads(agent_script(p,target,machine_identity(config),probe))
    if not isinstance(result,list) or not result or not all(isinstance(v,str) and re.fullmatch('[A-Z]',v) for v in result):
        raise ValueError('Windows did not report its occupied drive letters.')
    return set(result)


def check_letters(letters,occupied):
    conflicts=sorted(letters & occupied)
    if conflicts: raise ValueError('Drive letter already in use or reserved: '+', '.join(conflicts)+'. Choose another letter.')


def execute(manager,plan,log):
    p=operations.Proxmox()
    output=agent_script(p,plan,plan['identity'],SETUP.replace('__SCOPE__',subnet(plan['scope'])))
    if output.startswith('An HTTPS listener already exists'):
        log('Windows already has an HTTPS listener. Check the saved trust and test its login.')
        return
    # Only the public certificate of the listener just created crosses this boundary.
    from cryptography import x509
    try: x509.load_pem_x509_certificate(output.encode('ascii'))
    except (ValueError,UnicodeError): raise ValueError('Listener setup did not return a public certificate. Inspect the guest.') from None
    manager.put('winrm-trust',dict(id=management.target_id(plan),identity=plan['identity'],ca_pem=output))
    log('HTTPS listener configured. Test the Windows login on the machine page.')


def register(app,manager,life):
    @app.post('/api/simple/machine/winrm/review')
    def winrm_review():
        data=request.get_json()
        target=management.target(data)
        p=operations.Proxmox()
        resource,cfg=life.guest(p,target,False)
        identity=machine_identity(cfg)
        if not (cfg.get('smbios1') or cfg.get('vmgenid')) or identity!=data.get('identity'):
            raise ValueError('Machine identity changed. Refresh its details.')
        if not cfg.get('ostype','').startswith(('win','w2k')): raise ValueError('Choose a Windows machine')
        entered=data.get('scope')
        if not entered:
            bridge=re.search(r'(?:^|,)bridge=([^,]+)',cfg.get('net0',''))
            networks=p.call('GET',f"nodes/{target['node']}/network") if bridge else []
            network=next((n for n in networks if n.get('iface')==bridge[1]),{})
            entered=network.get('cidr')
            if not entered and network.get('address') and network.get('netmask'):
                entered=str(network['address'])+'/'+str(network['netmask'])
        scope=subnet(entered) if entered else None
        status='stopped-vm' if resource.get('status')!='running' else 'agent-unavailable'
        if status!='stopped-vm':
            try: checked=agent_script(p,target,identity,INSPECT)
            except (ValueError,RuntimeError,RequestException): checked=None
            if checked in ('stopped','no-listener','https-listener'): status=checked
        ident=None
        if scope and status in ('stopped','no-listener'):
            ident=uuid.uuid4().hex
            plan=dict(target,operation='winrm-setup',identity=identity,scope=scope)
            with manager.db() as con: con.execute('INSERT INTO plans VALUES (?,?,?)',(ident,time.time(),manager.encode(plan)))
        commands=SETUP.replace('__SCOPE__',scope).strip() if scope else None
        return jsonify(status=status,plan_id=ident,scope=scope,commands=commands)

    @app.post('/api/simple/machine/winrm/run')
    def winrm_run():
        data=request.get_json()
        plan=manager.load_plan(data.get('plan_id'))
        if plan.get('operation')!='winrm-setup' or data.get('confirmed') is not True: raise ValueError('Review and confirm the Windows listener setup')
        ident=data['plan_id']
        payload=dict(action='winrm-setup',**plan)
        with manager.db() as con:
            con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'winrm-setup','queued',time.time(),time.time(),manager.encode(payload),'Queued reviewed HTTPS WinRM listener setup.'))
        return jsonify(id=ident),202
