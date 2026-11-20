# All storage cmdlets used by the production script are replaced with local fixtures.
$ErrorActionPreference='Stop'
$target=Join-Path (Split-Path $PSScriptRoot) 'windows_volume.ps1'
$volumeFixture=@{}
function Update-HostStorageCache {}
function Get-Disk { return $volumeFixture.disks }
function Get-Partition { param($DiskNumber)
    if ($volumeFixture.prepared -and $DiskNumber -eq 1) { return 'fixture-partition' }
}
function Get-Volume {
    param([Parameter(ValueFromPipeline=$true)]$partition, [string]$DriveLetter)
    process { if ($partition -eq 'fixture-partition') { return [pscustomobject]@{DriveLetter='F';FileSystem='NTFS';FileSystemLabel='Data'} } }
}
function Join-Path { param($Path,$ChildPath)
    if ($Path -ne 'F:\' -or $ChildPath -ne ('.proxmate-volume-'+$serialExpected)) { throw 'Unexpected fixture path' }
    return $volumeFixture.marker
}
function Get-PSDrive { if ($volumeFixture.occupied) { return @{Name='F'} } }
function Set-Disk { throw 'Unexpected online request' }
function Initialize-Disk { param($InputObject,[string]$PartitionStyle,[switch]$PassThru)
    if ($InputObject.SerialNumber -ne $serialExpected -or $PartitionStyle -ne 'GPT') { throw 'Wrong initialization target' }
    $volumeFixture.writes+='initialize'
    return $InputObject
}
function New-Partition { param($InputObject,[switch]$UseMaximumSize,[string]$DriveLetter)
    if ($InputObject.SerialNumber -ne $serialExpected -or $DriveLetter -ne 'F') { throw 'Wrong partition target' }
    $volumeFixture.writes+='partition'
    return @{Owner=$InputObject.SerialNumber}
}
function Format-Volume { param($Partition,$FileSystem,$NewFileSystemLabel,$Confirm)
    if ($Partition.Owner -ne $serialExpected -or $FileSystem -ne 'NTFS' -or $NewFileSystemLabel -ne 'Data') { throw 'Wrong format target' }
    $volumeFixture.writes+='format'
    return [pscustomobject]@{DriveLetter='F'}
}
$serialExpected='PM0123456789abcdef'
$volumeFixture.marker=[IO.Path]::GetTempFileName()
try {
foreach ($case in @('missing','ambiguous','system','boot','readonly','size','occupied','partitioned','blank','prepared','foreign-marker')) {
    $disk=[pscustomobject]@{Number=1; UniqueId="shared-qemu-model-id"; SerialNumber=$serialExpected; IsSystem=$false; IsBoot=$false; IsReadOnly=$false; Size=80GB; PartitionStyle='RAW'; IsOffline=$false}
    $volumeFixture.disks=@($disk)
    $volumeFixture.occupied=$false
    $volumeFixture.prepared=$case -in @('prepared','foreign-marker')
    switch ($case) {
        'missing' { $disk.SerialNumber='different' }
        'ambiguous' { $volumeFixture.disks=@($disk,$disk) }
        'system' { $disk.IsSystem=$true }
        'boot' { $disk.IsBoot=$true }
        'readonly' { $disk.IsReadOnly=$true }
        'size' { $disk.Size=40GB }
        'occupied' { $volumeFixture.occupied=$true }
        'partitioned' { $disk.PartitionStyle='GPT' }
    }
    $volumeFixture.writes=@()
    if ($volumeFixture.prepared) { $disk.PartitionStyle='GPT' }
    if ($case -eq 'foreign-marker') { [IO.File]::WriteAllText($volumeFixture.marker,'another-build') }
    if ($case -in @('blank','prepared')) {
        $Ansible=@{Changed=$true}
        & $target -Serial $serialExpected -SizeGiB 80 -Label Data -Letter F
        if ($case -eq 'blank') {
            if (($volumeFixture.writes -join ',') -ne 'initialize,partition,format' -or [IO.File]::ReadAllText($volumeFixture.marker) -cne $serialExpected) { throw 'Failed to prepare the new volume' }
        } elseif ($Ansible.Changed -or $volumeFixture.writes.Count) { throw 'Prepared volume changed on a repeat run' }
        continue
    }
    $failure=$null
    try { & $target -Serial $serialExpected -SizeGiB 80 -Label Data -Letter F }
    catch { $failure=$_.Exception.Message }
    if ($failure -notmatch 'serial is missing or ambiguous|does not match|already in use|already contains a partition' -or $volumeFixture.writes.Count) {
        throw "Unexpected result for $case ($failure)"
    }
}
} finally { Remove-Item -LiteralPath $volumeFixture.marker }
'Eleven disk-format cases passed with mocked storage cmdlets.'
