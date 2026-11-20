param([string]$Serial,[uint64]$SizeGiB,[string]$Label,[string]$Letter)
$ErrorActionPreference = 'Stop'
Update-HostStorageCache
$matchesBySerial = @(Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim() -ceq $Serial })
if ($matchesBySerial.Count -ne 1) { throw 'New disk serial is missing or ambiguous' }
$disk = $matchesBySerial[0]
if ($disk.IsSystem -or $disk.IsBoot -or $disk.IsReadOnly -or $disk.Size -ne ($SizeGiB * 1GB)) {
    throw 'Disk identity, size or access does not match the reviewed new disk'
}
$marker = '.proxmate-volume-' + $Serial
if ($disk.PartitionStyle -ne 'RAW') {
    $volumes = @(Get-Partition -DiskNumber $disk.Number | Get-Volume | Where-Object { $_.DriveLetter })
    if ($disk.PartitionStyle -eq 'GPT' -and $volumes.Count -eq 1) {
        $v = $volumes[0]
        $proof = Join-Path ($v.DriveLetter + ':\') $marker
        if ($v.FileSystem -eq 'NTFS' -and $v.FileSystemLabel -ceq $Label -and (!$Letter -or $v.DriveLetter -eq $Letter) -and (Test-Path -LiteralPath $proof -PathType Leaf) -and [IO.File]::ReadAllText($proof) -ceq $Serial) {
            $Ansible.Changed = $false
            return
        }
    }
    throw 'The selected disk already contains a partition table. Inspect the retained VM.'
}
if ($Letter -and ((Get-PSDrive -Name $Letter -ErrorAction SilentlyContinue) -or (Get-Volume -DriveLetter $Letter -ErrorAction SilentlyContinue))) { throw 'Requested drive letter is already in use' }
if ($disk.IsOffline) { Set-Disk -InputObject $disk -IsOffline $false }
$initialized = Initialize-Disk -InputObject $disk -PartitionStyle GPT -PassThru
$assign = if ($Letter) { @{DriveLetter=$Letter} } else { @{AssignDriveLetter=$true} }
$partition = New-Partition -InputObject $initialized -UseMaximumSize @assign
$volume = Format-Volume -Partition $partition -FileSystem NTFS -NewFileSystemLabel $Label -Confirm:$false
[IO.File]::WriteAllText((Join-Path ($volume.DriveLetter + ':\') $marker),$Serial)
