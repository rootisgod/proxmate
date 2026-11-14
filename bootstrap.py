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
    else:
        storage=value.get('snippet_storage','')
        if not isinstance(storage,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}',storage): raise ValueError('Choose storage with snippets enabled for cloud-init bootstrap')
        result['snippet_storage']=storage
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


def windows_script(config):
    subnet=str(ipaddress.ip_network(config['management_cidr'],strict=False))
    return r'''# Run only inside the intended new Windows guest, elevated.
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\Proxmate'
New-Item -ItemType Directory -Force -Path $root | Out-Null
# The selected, attached VirtIO ISO supplies the signed guest-agent installer.
$msi = Get-PSDrive -PSProvider FileSystem | ForEach-Object { Join-Path $_.Root 'guest-agent\qemu-ga-x86_64.msi' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($msi) {
  $os = Get-CimInstance Win32_OperatingSystem
  $build = [int]$os.BuildNumber
  $flavour = if ($os.ProductType -eq 1) { if ($build -ge 22000) { 'w11' } else { 'w10' } } else { if ($build -ge 26100) { '2k25' } elseif ($build -ge 20348) { '2k22' } else { '2k19' } }
  $inf = Join-Path ([IO.Path]::GetPathRoot($msi)) ('vioserial\'+$flavour+'\amd64\vioser.inf')
  if (-not (Test-Path -LiteralPath $inf)) { throw 'VirtIO ISO lacks the matching x64 serial driver.' }
  & pnputil.exe /add-driver $inf /install | Out-Null
  if ($LASTEXITCODE -notin @(0,3010)) { throw 'VirtIO serial driver installation failed.' }
}
if (-not (Get-Service QEMU-GA -ErrorAction SilentlyContinue)) {
  if (-not $msi) { throw 'Attach the selected VirtIO ISO containing the guest agent MSI.' }
  if ((Get-AuthenticodeSignature -FilePath $msi).Status -ne 'Valid') { throw 'Guest agent MSI signature validation failed.' }
  $install = Start-Process msiexec.exe -ArgumentList @('/i',('"'+$msi+'"'),'/qn','/norestart') -Wait -PassThru -WindowStyle Hidden
  if ($install.ExitCode -notin @(0,3010)) { throw 'Guest agent installation failed.' }
}
Set-Service QEMU-GA -StartupType Automatic
Start-Service QEMU-GA
Enable-PSRemoting -SkipNetworkProfileCheck -Force
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $false
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $false
# Remove HTTP listeners/rules created by Enable-PSRemoting. HTTPS only.
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' } | Remove-Item -Recurse -Force
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
$addresses = @()
for ($attempt=0; $attempt -lt 60; $attempt++) {
  $addresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.AddressState -eq 'Preferred' } | Select-Object -ExpandProperty IPAddress)
  if ($addresses.Count) { break }; Start-Sleep -Seconds 2
}
if (-not $addresses.Count) { throw 'No usable guest address; fix networking and rerun bootstrap.' }
$san = '2.5.29.17={text}DNS=' + $env:COMPUTERNAME
foreach ($ip in $addresses) { $san += '&IPAddress=' + $ip }
$cert = New-SelfSignedCertificate -Subject ('CN='+$env:COMPUTERNAME) -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 2048 -NotAfter (Get-Date).AddYears(1) -TextExtension @($san,'2.5.29.37={text}1.3.6.1.5.5.7.3.1')
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' } | Remove-Item -Recurse -Force
New-Item WSMan:\localhost\Listener -Transport HTTPS -Address '*' -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null
Get-NetFirewallRule -Name 'Proxmate-WinRM-HTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'Proxmate-WinRM-HTTPS' -DisplayName 'Proxmate WinRM HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 -Profile Any -RemoteAddress '__SUBNET__' | Out-Null
Set-Service WinRM -StartupType Automatic
Restart-Service WinRM
$pem = "-----BEGIN CERTIFICATE-----`r`n" + [Convert]::ToBase64String($cert.RawData,[Base64FormattingOptions]::InsertLineBreaks) + "`r`n-----END CERTIFICATE-----`r`n"
[IO.File]::WriteAllText((Join-Path $root 'winrm.pem'),$pem)
[IO.File]::WriteAllText((Join-Path $root 'bootstrap-ready'),'ready')
Write-Output 'Bootstrap complete. Proxmate can read this public TLS certificate through the guest agent and verify HTTPS.'
'''.replace('__SUBNET__',subnet)


WINDOWS_COMMAND = r'''powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-PSDrive -PSProvider FileSystem | %% { $p=Join-Path $_.Root 'ProxmateBootstrap.ps1'; if(Test-Path $p){ & $p; exit } }; throw 'Bootstrap media missing'"'''.replace('%%','%')
