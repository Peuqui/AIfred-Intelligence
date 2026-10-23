<#
.SYNOPSIS
    Copies the EssentialPIM database from Aragon to the Mini, where it becomes
    AIfred's read-only mirror.

.DESCRIPTION
    A Firebird database must not be copied while EssentialPIM has it open.
    The script opens the file exclusively (that fails while EPIM holds it) and
    copies it locally inside that lock, so EPIM can be started again as soon
    as the local copy is done. The copy then travels to the Mini by scp
    (Windows' built-in OpenSSH), where install-mirror rebuilds and verifies
    it (backup + restore, record count of every table) and swaps it in.

    Settings come from push-epim-to-mini.json next to this script. With
    -OnlyIfChanged the script does nothing when the database has not changed
    since the last successful push (for a scheduled task).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File push-epim-to-mini.ps1
#>
param(
    [switch]$OnlyIfChanged
)
$ErrorActionPreference = 'Stop'

$configPath = Join-Path $PSScriptRoot 'push-epim-to-mini.json'
$statePath = Join-Path $PSScriptRoot 'push-epim-to-mini.state.json'
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
foreach ($key in 'Database', 'MiniHost', 'MiniPort', 'MiniUser', 'RemoteRepo', 'RemoteIncoming', 'RemotePython') {
    if (-not $config.$key) { throw "push-epim-to-mini.json: '$key' is missing" }
}

$database = Get-Item -LiteralPath $config.Database
if ($OnlyIfChanged -and (Test-Path $statePath)) {
    $state = Get-Content -Raw $statePath | ConvertFrom-Json
    if ($state.LastWriteTimeUtc -eq $database.LastWriteTimeUtc.ToString('o')) {
        Write-Host "EPIM database unchanged since the last push."
        exit 0
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$localCopy = Join-Path $env:TEMP "epim-push-$stamp.epim"
# Exclusive open: throws while EssentialPIM (or anything else) has the file open.
try {
    $source = [System.IO.File]::Open($database.FullName, 'Open', 'Read', 'None')
} catch {
    throw "The EPIM database is in use - close EssentialPIM and run again. ($($_.Exception.Message))"
}
try {
    $target = [System.IO.File]::Create($localCopy)
    try { $source.CopyTo($target) } finally { $target.Dispose() }
} finally {
    $source.Dispose()
}
Write-Host "Local copy done, EssentialPIM may be started again."

$remote = "$($config.MiniUser)@$($config.MiniHost)"
$remoteFile = "$($config.RemoteIncoming)/epim-$stamp.epim"
try {
    & ssh -p $config.MiniPort $remote "mkdir -p '$($config.RemoteIncoming)'"
    if ($LASTEXITCODE -ne 0) { throw "ssh mkdir failed ($LASTEXITCODE)" }
    & scp -P $config.MiniPort $localCopy "${remote}:$remoteFile.part"
    if ($LASTEXITCODE -ne 0) { throw "scp failed ($LASTEXITCODE)" }
    $install = "mv '$remoteFile.part' '$remoteFile' && cd '$($config.RemoteRepo)' && " +
               "AIFRED_CLI_MODE=1 '$($config.RemotePython)' -m aifred.plugins.tools.epim.maintenance install-mirror '$remoteFile'"
    & ssh -p $config.MiniPort $remote $install
    if ($LASTEXITCODE -ne 0) { throw "install-mirror on the Mini failed ($LASTEXITCODE)" }
} finally {
    Remove-Item -LiteralPath $localCopy
}

@{ LastWriteTimeUtc = $database.LastWriteTimeUtc.ToString('o'); PushedAt = (Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content -Encoding UTF8 $statePath
Write-Host "EPIM mirror on the Mini updated."
