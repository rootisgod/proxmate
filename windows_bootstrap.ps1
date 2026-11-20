# Runs inside the new guest as SYSTEM during specialize and on scheduled retries.
# Contains no login password. Do not add interactive logon or disable TLS checks.
$ErrorActionPreference = 'Stop'
$script:Root = 'C:\ProgramData\Proxmate'
$script:RetryTask = 'Proxmate-Bootstrap'
$script:CloneToken = '__CLONE_TOKEN__'

function Initialize-ProxmateBootstrap {
    New-Item -ItemType Directory -Force -Path $script:Root | Out-Null
    # SYSTEM executes the persisted script: ordinary users must not replace it.
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $script:Root -AclObject $acl
    $destination = Join-Path $script:Root 'Bootstrap.ps1'
    if ($PSCommandPath -and $PSCommandPath -ne $destination) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $destination -Force
    }
    & icacls.exe $destination /reset | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect persisted bootstrap script' }
}

function Open-ProxmateBootstrapLock {
    try {
        return [IO.File]::Open((Join-Path $script:Root 'bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch [IO.IOException] { return $null }
}

function Register-ProxmateRetry {
    if (Get-ScheduledTask -TaskName $script:RetryTask -ErrorAction SilentlyContinue) { return }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\ProgramData\Proxmate\Bootstrap.ps1'
    $startup = New-ScheduledTaskTrigger -AtStartup
    $timer = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $script:RetryTask -Action $action -Trigger @($startup,$timer) -Principal $principal -Settings $settings -Force | Out-Null
}

function Remove-ProxmateRetry {
    foreach ($task in @($script:RetryTask, 'Proxmate-CloneBootstrap')) {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Get-ProxmateAttempt {
    $file = Join-Path $script:Root 'bootstrap-attempt'
    $attempt = 0
    if (Test-Path -LiteralPath $file) { $attempt = [int]([IO.File]::ReadAllText($file)) }
    $attempt++
    [IO.File]::WriteAllText($file, [string]$attempt)
    return $attempt
}

function Write-ProxmateStatus([string]$stage) {
    $data = @{stage=$stage; updated=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $script:Root 'bootstrap-status.json'), $data)
}

function Write-ProxmateFailure([string]$stage, $failure) {
    # Local administrator-only diagnostic log; never sent to the web UI verbatim.
    $line = [DateTime]::UtcNow.ToString('o') + ' ' + $stage + ': ' + $failure.Exception.Message + "`r`n"
    [IO.File]::AppendAllText((Join-Path $script:Root 'bootstrap.log'), $line)
}

function Test-ProxmateWindowsSetupComplete {
    $setup = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\Setup'
    return $setup.SystemSetupInProgress -eq 0
}

function Enable-ProxmateWinRM {
    Set-Service WinRM -StartupType Automatic
    Start-Service WinRM
    if (-not (Test-Path -LiteralPath (Join-Path $script:Root 'winrm-state.json')) -or -not (Get-PSSessionConfiguration -Name Microsoft.PowerShell -ErrorAction SilentlyContinue)) {
        Enable-PSRemoting -SkipNetworkProfileCheck -Force
    }
    Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $false
    Set-Item WSMan:\localhost\Service\Auth\Basic -Value $false
    Set-Item WSMan:\localhost\Service\Auth\Negotiate -Value $true
    Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' } | Remove-Item -Recurse -Force
    Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule

    $addresses = @()
    for ($attempt=0; $attempt -lt 10; $attempt++) {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.AddressState -eq 'Preferred' } | Select-Object -ExpandProperty IPAddress | Sort-Object -Unique)
        if ($addresses.Count) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $addresses.Count) { throw 'Waiting for DHCP/usable guest IPv4 address' }

    # Retry without rotating the certificate or dropping working WinRM sessions.
    $identity = $env:COMPUTERNAME + '|' + ($addresses -join ',')
    if ($script:CloneToken) { $identity += '|' + $script:CloneToken }
    $stateFile = Join-Path $script:Root 'winrm-state.json'
    $cert = $null
    if (Test-Path -LiteralPath $stateFile) {
        $state = [IO.File]::ReadAllText($stateFile) | ConvertFrom-Json
        if ($state.identity -eq $identity -and $state.thumbprint -match '^[A-Fa-f0-9]{40}$') {
            $cert = Get-Item -LiteralPath ('Cert:\LocalMachine\My\'+$state.thumbprint) -ErrorAction SilentlyContinue
            if ($cert -and (-not $cert.HasPrivateKey -or $cert.NotAfter -lt (Get-Date).AddDays(1))) { $cert = $null }
        }
    }
    if (-not $cert) {
        $san = '2.5.29.17={text}DNS=' + $env:COMPUTERNAME
        foreach ($ip in $addresses) { $san += '&IPAddress=' + $ip }
        $cert = New-SelfSignedCertificate -Subject ('CN='+$env:COMPUTERNAME) -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 2048 -NotAfter (Get-Date).AddYears(1) -TextExtension @($san,'2.5.29.37={text}1.3.6.1.5.5.7.3.1')
        [IO.File]::WriteAllText($stateFile, (@{identity=$identity;thumbprint=$cert.Thumbprint} | ConvertTo-Json -Compress))
    }
    $listeners = @(Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' })
    $matching = @($listeners | Where-Object { (Get-Item -LiteralPath ($_.PSPath+'\CertificateThumbprint')).Value -eq $cert.Thumbprint })
    if ($listeners.Count -ne 1 -or $matching.Count -ne 1) {
        $listeners | Remove-Item -Recurse -Force
        New-Item WSMan:\localhost\Listener -Transport HTTPS -Address '*' -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null
    }
    Get-NetFirewallRule -Name 'Proxmate-WinRM-HTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name 'Proxmate-WinRM-HTTPS' -DisplayName 'Proxmate WinRM HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 -Profile Any -RemoteAddress '__SUBNET__' | Out-Null
    $pem = "-----BEGIN CERTIFICATE-----`r`n" + [Convert]::ToBase64String($cert.RawData,[Base64FormattingOptions]::InsertLineBreaks) + "`r`n-----END CERTIFICATE-----`r`n"
    [IO.File]::WriteAllText((Join-Path $script:Root 'winrm.pem'), $pem)
    [IO.File]::WriteAllText((Join-Path $script:Root 'winrm-ready'), 'ready')
}

function Test-ProxmateSerialDriver {
    return @(Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -eq 'VirtIO Serial Driver' -and $_.Status -eq 'OK' }).Count -gt 0
}

function Assert-ProxmateInstaller([string]$msi, [string]$expected) {
    if ($expected) {
        if ((Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash -ne $expected) { throw 'Guest agent MSI does not match the reviewed ISO checksum' }
    }
    $signature = (Get-AuthenticodeSignature -FilePath $msi).Status.ToString()
    # Community MSI wrappers can be unsigned even though their drivers are signed.
    # Permit only exact bytes pinned from the selected ISO; never accept a bad signature.
    if ($signature -ne 'Valid' -and -not ($signature -eq 'NotSigned' -and $expected)) {
        throw 'Guest agent MSI signature validation failed and no matching unsigned-media pin is available'
    }
}

function Install-ProxmateGuestTools {
    $msi = $null
    if ($script:CloneToken) {
        if (-not (Get-Service QEMU-GA -ErrorAction SilentlyContinue)) { throw 'Guest agent is missing from the selected template' }
    } else {
        $msi = Get-CimInstance Win32_CDROMDrive | Where-Object Drive | ForEach-Object { Join-Path ($_.Drive+'\') 'guest-agent\qemu-ga-x86_64.msi' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if ($msi -and -not (Test-ProxmateSerialDriver)) {
        $os = Get-CimInstance Win32_OperatingSystem
        $build = [int]$os.BuildNumber
        $flavour = if ($os.ProductType -eq 1) { if ($build -ge 22000) { 'w11' } else { 'w10' } } else { if ($build -ge 26100) { '2k25' } elseif ($build -ge 20348) { '2k22' } else { '2k19' } }
        $inf = Join-Path ([IO.Path]::GetPathRoot($msi)) ('vioserial\'+$flavour+'\amd64\vioser.inf')
        if (-not (Test-Path -LiteralPath $inf)) { throw 'VirtIO ISO lacks the matching x64 serial driver' }
        & pnputil.exe /add-driver $inf /install | Out-Null
        if ($LASTEXITCODE -notin @(0,3010) -and -not (Test-ProxmateSerialDriver)) { throw 'VirtIO serial driver installation failed' }
    }
    if (-not (Get-Service QEMU-GA -ErrorAction SilentlyContinue)) {
        if (-not $msi) { throw 'Waiting for attached VirtIO guest-agent MSI' }
        Assert-ProxmateInstaller $msi '__AGENT_SHA256__'
        $install = Start-Process msiexec.exe -ArgumentList @('/i',('"'+$msi+'"'),'/qn','/norestart','/L*v',('"'+(Join-Path $script:Root 'guest-agent-install.log')+'"')) -PassThru -WindowStyle Hidden
        if (-not $install.WaitForExit(120000)) { throw 'Guest agent MSI still running; waiting for next retry' }
        if ($install.ExitCode -notin @(0,3010)) { throw ('Guest agent MSI exit code '+$install.ExitCode) }
    }
    Set-Service QEMU-GA -StartupType Automatic
    Start-Service QEMU-GA
    if ((Get-Service QEMU-GA).Status -ne 'Running') { throw 'Guest agent service is not running' }
}

function Set-ProxmateReady {
    if ($script:CloneToken) { [IO.File]::WriteAllText((Join-Path $script:Root 'clone-ready'), ($script:CloneToken + '|' + $env:COMPUTERNAME)) }
    [IO.File]::WriteAllText((Join-Path $script:Root 'bootstrap-ready'), 'ready')
}

function Invoke-ProxmateBootstrap {
    Initialize-ProxmateBootstrap
    $lock = Open-ProxmateBootstrapLock
    if (-not $lock) { return }
    try {
        Register-ProxmateRetry
        if ((Get-ProxmateAttempt) -gt 60) {
            Write-ProxmateStatus 'failed-retry-limit'
            Remove-ProxmateRetry
            return
        }
        $winrmReady = $false
        $agentReady = $false
        Write-ProxmateStatus 'configuring-winrm'
        try { Enable-ProxmateWinRM; $winrmReady = $true }
        catch { Write-ProxmateFailure 'winrm' $_ }

        # MSI installation during specialize can clash with Windows Setup.
        # The SYSTEM startup/timer task continues without any user signing in.
        if (-not (Test-ProxmateWindowsSetupComplete)) {
            Write-ProxmateStatus 'waiting-windows-setup'
            return
        }
        Write-ProxmateStatus 'installing-guest-tools'
        try { Install-ProxmateGuestTools; $agentReady = $true }
        catch { Write-ProxmateFailure 'guest-tools' $_ }
        if ($winrmReady -and $agentReady) {
            Set-ProxmateReady
            Write-ProxmateStatus 'ready'
            Remove-ProxmateRetry
        } elseif ($winrmReady) { Write-ProxmateStatus 'retry-guest-tools' }
        elseif ($agentReady) { Write-ProxmateStatus 'retry-winrm' }
        else { Write-ProxmateStatus 'retry-winrm-and-guest-tools' }
    } finally { $lock.Dispose() }
}

Invoke-ProxmateBootstrap
