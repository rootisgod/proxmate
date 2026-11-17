# Only the orchestration is executed. All OS/file/service operations it calls
# are mocked. The real generated helper is separately parsed and ISO-tested.
$source = Get-Content (Join-Path $PSScriptRoot '..\windows_bootstrap.ps1') -Raw
$tokens = $null; $errors = $null
$null = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$definitions = $source.Substring(0, $source.LastIndexOf('Invoke-ProxmateBootstrap'))
. ([scriptblock]::Create($definitions))

Describe 'Windows bootstrap without a user login' {
    BeforeEach {
        $script:events = New-Object 'System.Collections.Generic.List[string]'
        $script:failWinRM = $false; $script:failAgent = $false
        $script:setupComplete = $true; $script:attempt = 1; $script:locked = $false
        Mock Initialize-ProxmateBootstrap {}
        Mock Open-ProxmateBootstrapLock {
            if ($script:locked) { return $null }
            $lock = New-Object psobject
            $lock | Add-Member ScriptMethod Dispose { $script:events.Add('unlock') }
            return $lock
        }
        Mock Register-ProxmateRetry { $script:events.Add('schedule') }
        Mock Remove-ProxmateRetry { $script:events.Add('unschedule') }
        Mock Get-ProxmateAttempt { return $script:attempt }
        Mock Write-ProxmateStatus { param($stage) $script:events.Add($stage) }
        Mock Write-ProxmateFailure { param($stage,$failure) $script:events.Add('error-'+$stage) }
        Mock Test-ProxmateWindowsSetupComplete { return $script:setupComplete }
        Mock Enable-ProxmateWinRM { $script:events.Add('winrm'); if ($script:failWinRM) { throw 'WinRM not ready' } }
        Mock Install-ProxmateGuestTools { $script:events.Add('agent'); if ($script:failAgent) { throw 'MSI temporarily busy' } }
        Mock Set-ProxmateReady { $script:events.Add('marker') }
    }
    It 'configures WinRM first and completes only after both stages succeed' {
        Invoke-ProxmateBootstrap
        ($script:events -join ',') | Should Be 'schedule,configuring-winrm,winrm,installing-guest-tools,agent,marker,ready,unschedule,unlock'
    }
    It 'keeps WinRM configured and retries when guest tools fail' {
        $script:failAgent = $true
        Invoke-ProxmateBootstrap
        $script:events.Contains('winrm') | Should Be $true
        $script:events.Contains('retry-guest-tools') | Should Be $true
        $script:events.Contains('marker') | Should Be $false
        $script:events.Contains('unschedule') | Should Be $false
        $script:events[-1] | Should Be 'unlock'
    }
    It 'still installs the agent for diagnostics when WinRM needs a retry' {
        $script:failWinRM = $true
        Invoke-ProxmateBootstrap
        $script:events.Contains('agent') | Should Be $true
        $script:events.Contains('retry-winrm') | Should Be $true
        $script:events.Contains('marker') | Should Be $false
    }
    It 'defers MSI during Windows Setup and completes on a later SYSTEM invocation' {
        $script:setupComplete = $false
        Invoke-ProxmateBootstrap
        $script:events.Contains('winrm') | Should Be $true
        $script:events.Contains('agent') | Should Be $false
        $script:events.Contains('waiting-windows-setup') | Should Be $true
        $script:setupComplete = $true
        Invoke-ProxmateBootstrap
        $script:events.Contains('ready') | Should Be $true
    }
    It 'completes a later retry after a transient MSI failure' {
        $script:failAgent = $true
        Invoke-ProxmateBootstrap
        $script:failAgent = $false
        Invoke-ProxmateBootstrap
        $script:events.Contains('ready') | Should Be $true
        $script:events.Contains('unschedule') | Should Be $true
    }
    It 'bounds retries without marking the VM ready' {
        $script:attempt = 61
        Invoke-ProxmateBootstrap
        ($script:events -join ',') | Should Be 'schedule,failed-retry-limit,unschedule,unlock'
    }
    It 'does not run overlapping setup attempts' {
        $script:locked = $true
        Invoke-ProxmateBootstrap
        $script:events.Count | Should Be 0
    }
}

Describe 'Stable WinRM certificate across retries' {
    BeforeEach {
        $script:Root = $TestDrive
        $script:ip = '192.0.2.20'
        $script:thumb = 'A' * 40
        $script:newThumb = 'B' * 40
        $script:listenerThumb = $script:thumb
        $script:created = 0; $script:listenerWrites = 0
        $state = @{identity=($env:COMPUTERNAME+'|192.0.2.20');thumbprint=$script:thumb} | ConvertTo-Json
        [IO.File]::WriteAllText((Join-Path $TestDrive 'winrm-state.json'),$state)
        Mock Set-Service {}
        Mock Start-Service {}
        Mock Get-PSSessionConfiguration { return [pscustomobject]@{Name='Microsoft.PowerShell'} }
        Mock Enable-PSRemoting { throw 'Unexpected session reset' }
        Mock Set-Item {}
        Mock Get-ChildItem { return [pscustomobject]@{Keys=@('Transport=HTTPS');PSPath='WSMan:\localhost\Listener\test'} }
        Mock Remove-Item {}
        Mock Get-NetFirewallRule {}
        Mock Disable-NetFirewallRule {}
        Mock Remove-NetFirewallRule {}
        Mock New-NetFirewallRule {}
        Mock Get-NetIPAddress { return [pscustomobject]@{IPAddress=$script:ip;AddressState='Preferred'} }
        Mock New-Item { $script:listenerWrites++ }
        Mock Get-Item {
            param($LiteralPath)
            if ($LiteralPath -like 'Cert:*') { return [pscustomobject]@{Thumbprint=$script:thumb;HasPrivateKey=$true;NotAfter=(Get-Date).AddMonths(6);RawData=[byte[]](1,2,3)} }
            return [pscustomobject]@{Value=$script:listenerThumb}
        }
        Mock New-SelfSignedCertificate {
            $script:created++
            return [pscustomobject]@{Thumbprint=$script:newThumb;HasPrivateKey=$true;NotAfter=(Get-Date).AddYears(1);RawData=[byte[]](4,5,6)}
        }
    }
    It 'preserves the trusted certificate and working listener on a retry' {
        Enable-ProxmateWinRM
        $script:created | Should Be 0
        $script:listenerWrites | Should Be 0
        [IO.File]::ReadAllText((Join-Path $TestDrive 'winrm-ready')) | Should Be 'ready'
    }
    It 'issues a new certificate if DHCP changes the guest IP' {
        $script:ip = '192.0.2.21'
        Enable-ProxmateWinRM
        $script:created | Should Be 1
        $script:listenerWrites | Should Be 1
        $state = [IO.File]::ReadAllText((Join-Path $TestDrive 'winrm-state.json')) | ConvertFrom-Json
        $state.identity | Should Be ($env:COMPUTERNAME+'|192.0.2.21')
    }
}

Describe 'Selected ISO installer integrity' {
    BeforeEach {
        $script:signature = 'NotSigned'; $script:hash = 'a' * 64
        Mock Get-FileHash { return [pscustomobject]@{Hash=$script:hash} }
        Mock Get-AuthenticodeSignature { return [pscustomobject]@{Status=$script:signature} }
    }
    It 'accepts an unsigned community MSI only with a matching reviewed hash' {
        { Assert-ProxmateInstaller 'test.msi' ('a'*64) } | Should Not Throw
    }
    It 'rejects an unsigned MSI without a reviewed hash' {
        { Assert-ProxmateInstaller 'test.msi' '' } | Should Throw
    }
    It 'rejects changed bytes even if Authenticode is valid' {
        $script:signature='Valid'
        { Assert-ProxmateInstaller 'test.msi' ('b'*64) } | Should Throw
    }
    It 'does not override a broken signature with a checksum' {
        $script:signature='HashMismatch'
        { Assert-ProxmateInstaller 'test.msi' ('a'*64) } | Should Throw
    }
}
