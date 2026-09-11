param(
    [Parameter(Mandatory=$true)][string]$CoordinationRoot,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [int]$MaxAgeSeconds = 180
)
$ErrorActionPreference = 'Stop'
$heartbeatPath = Join-Path $CoordinationRoot 'supervisor-heartbeat.json'
$stale = $true
$heartbeatPid = $null
if (Test-Path -LiteralPath $heartbeatPath) {
    try {
        $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
        $heartbeatPid = [int]$heartbeat.pid
        $stamp = [DateTimeOffset]::Parse([string]$heartbeat.timestamp)
        $age = ([DateTimeOffset]::UtcNow - $stamp.ToUniversalTime()).TotalSeconds
        $stale = $age -gt $MaxAgeSeconds
    } catch {
        $stale = $true
    }
}
if (-not $stale) { exit 0 }
if ($heartbeatPid) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$heartbeatPid" -ErrorAction SilentlyContinue
    if ($proc) {
        $command = [string]$proc.CommandLine
        if ($command -like '*scripts.dev_orchestrator.cli*' -and $command -like '* run *') {
            Stop-Process -Id $heartbeatPid -Force -ErrorAction Stop
            Start-Sleep -Seconds 1
        } else {
            throw "Stale heartbeat PID $heartbeatPid does not belong to the AskRex development supervisor."
        }
    }
}
$argsList = @(
    '-m', 'scripts.dev_orchestrator.cli', 'run',
    '--coordination-root', $CoordinationRoot
)
Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $argsList `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden
