param(
    [Parameter(Mandatory=$true)][string]$CoordinationRoot,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [int]$MaxAgeSeconds = 180
)
$ErrorActionPreference = 'Stop'
$heartbeatPath = Join-Path $CoordinationRoot 'supervisor-heartbeat.json'
$activeRoot = Join-Path $CoordinationRoot 'active-agents'
if (-not ('AskRexNativeFileIdentity' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class AskRexNativeFileIdentity
{
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string fileName,
        uint desiredAccess,
        FileShare shareMode,
        IntPtr securityAttributes,
        FileMode creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out BY_HANDLE_FILE_INFORMATION information);

    public static string GetDirectoryIdentity(string path)
    {
        SafeFileHandle handle = CreateFileW(
            path,
            0,
            FileShare.Read | FileShare.Write | FileShare.Delete,
            IntPtr.Zero,
            FileMode.Open,
            FILE_FLAG_BACKUP_SEMANTICS,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, "Cannot open directory for stable identity");
        }
        try
        {
            BY_HANDLE_FILE_INFORMATION information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "Cannot read stable directory identity");
            }
            return String.Format(
                "{0:X8}:{1:X8}{2:X8}",
                information.VolumeSerialNumber,
                information.FileIndexHigh,
                information.FileIndexLow);
        }
        finally
        {
            handle.Dispose();
        }
    }
}
"@
}
function Get-StableDirectoryIdentity {
    param([string]$Path)
    $canonical = [System.IO.Path]::GetFullPath($Path)
    return [AskRexNativeFileIdentity]::GetDirectoryIdentity($canonical)
}
function Assert-NoReparseTree {
    param([string]$Path)
    $reparse = [System.IO.FileAttributes]::ReparsePoint
    $rootItem = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($rootItem.Attributes -band $reparse) -ne 0) { throw "Scratch quarantine root is a reparse point." }
    $found = Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction Stop | Select-Object -First 1
    if ($found) { throw "Scratch quarantine contains a descendant reparse point." }
}
function Remove-TreeWithoutFollowingReparse {
    param([string]$Path)
    $reparse = [System.IO.FileAttributes]::ReparsePoint
    $rootItem = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($rootItem.Attributes -band $reparse) -ne 0) { throw "Refusing to delete a reparse-point directory." }
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
        $fresh = Get-Item -LiteralPath $child.FullName -Force -ErrorAction Stop
        if (($fresh.Attributes -band $reparse) -ne 0) { throw "Refusing to delete a reparse-point descendant." }
        if ($fresh.PSIsContainer) { Remove-TreeWithoutFollowingReparse $fresh.FullName }
        else {
            if (($fresh.Attributes -band [System.IO.FileAttributes]::ReadOnly) -ne 0) {
                [System.IO.File]::SetAttributes($fresh.FullName, ($fresh.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
            }
            [System.IO.File]::Delete($fresh.FullName)
        }
    }
    [System.IO.Directory]::Delete($Path, $false)
}
function Convert-HeartbeatTimestamp {
    param($Value)
    if ($Value -is [DateTime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            throw "Heartbeat timestamp must include an explicit UTC offset."
        }
        return [DateTimeOffset]$Value.ToUniversalTime()
    }
    $text = [string]$Value
    if ($text -notmatch '(Z|[+-]\d{2}:\d{2})$') {
        throw "Heartbeat timestamp must include an explicit UTC offset."
    }
    return [DateTimeOffset]::Parse($text).ToUniversalTime()
}
function Remove-StaleScratchClone {
    param(
        [string]$ScratchPath,
        [string]$ScratchNonce,
        [string]$InvocationId,
        [string]$Role,
        [string]$PreHead
    )
    if (-not $ScratchPath) { return }
    if (-not $ScratchNonce -or -not $InvocationId -or -not $Role -or -not $PreHead) {
        throw "Scratch ownership metadata is incomplete."
    }
    $full = [System.IO.Path]::GetFullPath($ScratchPath)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $tempPrefix = $tempRoot.TrimEnd([char[]]@([char]92, [char]47)) + [System.IO.Path]::DirectorySeparatorChar
    $coordParent = Split-Path -Parent ([System.IO.Path]::GetFullPath($CoordinationRoot))
    $dedicatedScratchRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $coordParent '.askrex-agent-scratch')
    )
    $parent = Split-Path -Parent $full
    $parentParent = Split-Path -Parent $parent
    $parentLeaf = Split-Path -Leaf $parent
    $leaf = Split-Path -Leaf $full
    $withinTemp = $full.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    $withinDedicatedCodex = (
        $parentParent.Equals($dedicatedScratchRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        $parentLeaf.StartsWith("askrex-$Role-codex-", [System.StringComparison]::OrdinalIgnoreCase)
    )
    if ((-not $withinTemp -and -not $withinDedicatedCodex) -or $leaf -ne 'repo') {
        throw "Scratch path is outside an AskRex managed clone boundary: $ScratchPath"
    }
    if ($withinDedicatedCodex) {
        $dedicatedItem = Get-Item -LiteralPath $dedicatedScratchRoot -Force -ErrorAction Stop
        $reparse = [System.IO.FileAttributes]::ReparsePoint
        if (($dedicatedItem.Attributes -band $reparse) -ne 0) {
            throw "Dedicated scratch boundary is a reparse point."
        }
    }
    $parentItem = Get-Item -LiteralPath $parent -Force -ErrorAction Stop
    $scratchItem = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    $reparse = [System.IO.FileAttributes]::ReparsePoint
    if (($parentItem.Attributes -band $reparse) -ne 0 -or ($scratchItem.Attributes -band $reparse) -ne 0) {
        throw "Scratch path contains a reparse point."
    }
    $ownerPath = Join-Path $parent '.askrex-scratch-owner.json'
    $ownerItem = Get-Item -LiteralPath $ownerPath -Force -ErrorAction Stop
    if (($ownerItem.Attributes -band $reparse) -ne 0) { throw "Scratch ownership record is a reparse point." }
    $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
    $ownerScratch = [System.IO.Path]::GetFullPath([string]$owner.scratch_path)
    if (
        [string]$owner.nonce -ne $ScratchNonce -or
        [string]$owner.invocation_id -ne $InvocationId -or
        [string]$owner.role -ne $Role -or
        [string]$owner.pre_head -ne $PreHead -or
        -not $ownerScratch.Equals($full, [System.StringComparison]::OrdinalIgnoreCase)
    ) { throw "Scratch ownership record does not match the activity marker." }
    $top = (& git -C $full rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "Scratch clone Git identity cannot be verified." }
    $topFull = [System.IO.Path]::GetFullPath(([string]$top).Trim())
    if (-not $topFull.Equals($full, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Scratch clone Git top-level does not match its recorded path."
    }
    $head = (& git -C $full rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or ([string]$head).Trim() -ne $PreHead) {
        throw "Scratch clone HEAD does not match its recorded provenance."
    }
    $dirty = (& git -C $full status --porcelain --untracked-files=all 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "Scratch clone worktree state cannot be verified." }
    if ([string]($dirty | Out-String)) { throw "Scratch clone contains unpublished work; preserving it for recovery." }
    Assert-NoReparseTree $parent
    $sourceIdentity = Get-StableDirectoryIdentity $parent
    $quarantine = "$parent.askrex-quarantine-$([Guid]::NewGuid().ToString('N'))"
    [System.IO.Directory]::Move($parent, $quarantine)
    try {
        $quarantineIdentity = Get-StableDirectoryIdentity $quarantine
        if ($quarantineIdentity -ne $sourceIdentity) {
            throw "Quarantined scratch directory identity changed during rename."
        }
        Assert-NoReparseTree $quarantine
        $quarantinedScratch = Join-Path $quarantine 'repo'
        $quarantinedOwnerPath = Join-Path $quarantine '.askrex-scratch-owner.json'
        $quarantinedOwner = Get-Content -LiteralPath $quarantinedOwnerPath -Raw | ConvertFrom-Json
        $quarantinedOwnerScratch = [System.IO.Path]::GetFullPath([string]$quarantinedOwner.scratch_path)
        if ([string]$quarantinedOwner.nonce -ne $ScratchNonce -or [string]$quarantinedOwner.invocation_id -ne $InvocationId -or [string]$quarantinedOwner.role -ne $Role -or [string]$quarantinedOwner.pre_head -ne $PreHead -or -not $quarantinedOwnerScratch.Equals($full, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Quarantined scratch ownership record changed during rename." }
        $quarantinedTop = (& git -C $quarantinedScratch rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "Quarantined scratch Git identity cannot be verified." }
        $quarantinedTopFull = [System.IO.Path]::GetFullPath(([string]$quarantinedTop).Trim())
        $quarantinedScratchFull = [System.IO.Path]::GetFullPath($quarantinedScratch)
        if (-not $quarantinedTopFull.Equals($quarantinedScratchFull, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Quarantined scratch Git top-level does not match its renamed path." }
        $quarantinedHead = (& git -C $quarantinedScratch rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or ([string]$quarantinedHead).Trim() -ne $PreHead) { throw "Quarantined scratch HEAD does not match its recorded provenance." }
        Assert-NoReparseTree $quarantine
        Remove-TreeWithoutFollowingReparse $quarantine
    } catch {
        throw "Quarantined scratch clone retained after safety verification failed: $($_.Exception.Message)"
    }
    Write-Output "Removed stale scratch clone $parent after quarantine verification."
}
$stale = $true
$heartbeatPid = $null
$heartbeatIdentityMatches = $false
$maxFutureSkewSeconds = 5
if (Test-Path -LiteralPath $heartbeatPath) {
    try {
        $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
        $heartbeatFields = @($heartbeat.PSObject.Properties.Name)
        if (
            $heartbeatFields.Count -ne 3 -or
            $heartbeatFields -notcontains 'pid' -or
            $heartbeatFields -notcontains 'timestamp' -or
            $heartbeatFields -notcontains 'process_started_filetime'
        ) { throw "Heartbeat schema is invalid." }
        if (-not (($heartbeat.pid -is [int]) -or ($heartbeat.pid -is [long]))) {
            throw "Heartbeat PID must be a JSON integer."
        }
        if (-not (($heartbeat.process_started_filetime -is [int]) -or ($heartbeat.process_started_filetime -is [long]))) {
            throw "Heartbeat process FILETIME must be a JSON integer."
        }
        $heartbeatPid = [long]$heartbeat.pid
        $recordedStartFiletime = [long]$heartbeat.process_started_filetime
        if ($heartbeatPid -le 0 -or $heartbeatPid -gt [int]::MaxValue) { throw "Heartbeat PID is invalid." }
        if ($recordedStartFiletime -le 0) { throw "Heartbeat process FILETIME is invalid." }
        $stamp = Convert-HeartbeatTimestamp $heartbeat.timestamp
        $heartbeatProcess = Get-Process -Id ([int]$heartbeatPid) -ErrorAction SilentlyContinue
        if ($heartbeatProcess) {
            $actualStartFiletime = $heartbeatProcess.StartTime.ToUniversalTime().ToFileTimeUtc()
            $heartbeatIdentityMatches = $actualStartFiletime -eq $recordedStartFiletime
        }
        $nowUtc = [DateTimeOffset]::UtcNow
        $age = ($nowUtc - $stamp).TotalSeconds
        if ($age -lt (-1 * $maxFutureSkewSeconds)) { throw "Heartbeat timestamp is too far in the future." }
        $stale = $age -gt $MaxAgeSeconds -or -not $heartbeatIdentityMatches
    } catch { $stale = $true }
}
if (-not $stale -and $heartbeatIdentityMatches) { exit 0 }
$blockingMarker = $false
if (Test-Path -LiteralPath $activeRoot) {
    $markers = @(Get-ChildItem -LiteralPath $activeRoot -Filter '*.json' -File)
    foreach ($marker in $markers) {
        try {
            $activity = Get-Content -LiteralPath $marker.FullName -Raw | ConvertFrom-Json
            $status = [string]$activity.status
            $invocationId = [string]$activity.invocation_id
            $containerName = [string]$activity.container_name
            $scratchPath = [string]$activity.scratch_path
            $scratchNonce = [string]$activity.scratch_nonce
            $role = [string]$activity.role
            $preHead = [string]$activity.pre_head
            if ($status -eq 'postprocessing') {
                Write-Output "Agent marker is in postprocessing; preserving scratch and refusing restart."
                $blockingMarker = $true
                continue
            }
            if ($containerName) {
                & docker info --format '{{.ServerVersion}}' *> $null
                if ($LASTEXITCODE -ne 0) {
                    throw "Docker state is unavailable for active Claude container marker."
                }
                $containerState = & docker container inspect --format '{{.State.Running}}' $containerName 2>&1
                $containerInspectExit = $LASTEXITCODE
                if ($containerInspectExit -eq 0) {
                    $containerExists = $true
                } else {
                    $containerDetail = ([string]($containerState | Out-String)).ToLowerInvariant()
                    if ($containerDetail.Contains('no such container') -or $containerDetail.Contains('no such object')) {
                        $containerExists = $false
                    } else {
                        throw "Docker container state is ambiguous for $containerName."
                    }
                }
                if ($containerExists) {
                    $markerPid = if ($null -ne $activity.pid) { [int]$activity.pid } else { 0 }
                    $clientAlive = $markerPid -gt 0 -and [bool](Get-Process -Id $markerPid -ErrorAction SilentlyContinue)
                    if (-not $clientAlive) {
                        & docker rm -f $containerName *> $null
                        if ($LASTEXITCODE -ne 0) { throw "Could not remove orphaned Claude container $containerName." }
                        Remove-StaleScratchClone $scratchPath $scratchNonce $invocationId $role $preHead
                        Remove-Item -LiteralPath $marker.FullName -Force
                        Write-Output "Removed orphaned Claude container $containerName and stale marker $($marker.Name)."
                        continue
                    }
                }
            }
            if ($status -eq 'launching' -or $null -eq $activity.pid) {
                $launcherPid = [int]$activity.launcher_pid
                $launcherAlive = [bool](Get-Process -Id $launcherPid -ErrorAction SilentlyContinue)
                $matchingChild = $null
                if ($invocationId) {
                    $matchingChild = Get-CimInstance Win32_Process | Where-Object {
                        $_.CommandLine -and $_.CommandLine.Contains($invocationId)
                    } | Select-Object -First 1
                }
                if ($launcherAlive -or $matchingChild) {
                    Write-Output "Launching-agent marker is still attributable to a live process; refusing restart."
                    $blockingMarker = $true
                    continue
                }
                Remove-StaleScratchClone $scratchPath $scratchNonce $invocationId $role $preHead
                Remove-Item -LiteralPath $marker.FullName -Force
                Write-Output "Removed stale launching-agent marker $($marker.Name)."
                continue
            }
            $agentPid = [int]$activity.pid
            $process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
            if (-not $process) {
                Remove-StaleScratchClone $scratchPath $scratchNonce $invocationId $role $preHead
                Remove-Item -LiteralPath $marker.FullName -Force
                Write-Output "Removed stale active-agent marker $($marker.Name); PID $agentPid is absent."
                continue
            }
            if ($status -eq 'delegated') {
                Write-Output "Delegated executor marker remains for live PID $agentPid; refusing restart."
                $blockingMarker = $true
                continue
            }
            $markerStart = [DateTimeOffset]::Parse([string]$activity.process_started_at)
            $actualStart = [DateTimeOffset]$process.StartTime.ToUniversalTime()
            $sameStart = [Math]::Abs(($actualStart - $markerStart).TotalSeconds) -le 10
            $commandMatches = $false
            try {
                $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $agentPid"
                $commandMatches = (-not $invocationId) -or ($cim.CommandLine -and $cim.CommandLine.Contains($invocationId))
            } catch { $commandMatches = $false }
            if (-not $sameStart -or -not $commandMatches) {
                Remove-StaleScratchClone $scratchPath $scratchNonce $invocationId $role $preHead
                Remove-Item -LiteralPath $marker.FullName -Force
                Write-Output "Removed stale active-agent marker $($marker.Name); PID identity no longer matches."
                continue
            }
            Write-Output "Active agent PID $agentPid still matches marker identity; refusing restart."
            $blockingMarker = $true
        } catch {
            Write-Output "Active-agent marker $($marker.FullName) failed closed: $($_.Exception.Message); refusing automatic restart."
            $blockingMarker = $true
        }
    }
}
if ($blockingMarker) { exit 3 }
if ($heartbeatPid -and $heartbeatIdentityMatches) {
    Write-Output "Supervisor PID $heartbeatPid still matches heartbeat process identity despite stale heartbeat; refusing duplicate restart."
    exit 2
}
$argsList = @('-m', 'scripts.dev_orchestrator.cli', 'run', '--coordination-root', $CoordinationRoot)
Start-Process -FilePath $PythonExe -ArgumentList $argsList -WorkingDirectory $RepoRoot -WindowStyle Hidden
