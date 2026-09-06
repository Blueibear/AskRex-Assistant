[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [string]$BuildPython = 'python',
    [string]$DiagnosticsPath = ''
)

$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$buildPythonPath = (Get-Command $BuildPython -ErrorAction Stop).Source
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('arx-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$installPath = Join-Path $testRoot 'AskRex'
$localAppData = Join-Path $testRoot 'LocalAppData'
$appData = Join-Path $testRoot 'AppData'
$runtimeRoot = Join-Path $appData 'rex-gui'
$lifecycleRuntimeRoot = Join-Path $testRoot 'Lifecycle Runtime With Spaces'
$backgroundTaskName = 'AskRex Background Runtime'
$smokeOutput = Join-Path $testRoot 'smoke.json'
$script:smokePhase = 'initializing'

function Write-SmokeDiagnostics(
    [string]$Status,
    [string]$Message = ''
) {
    if (-not $DiagnosticsPath) { return }
    $diagnosticDirectory = Split-Path -Parent $DiagnosticsPath
    if ($diagnosticDirectory) {
        New-Item -ItemType Directory -Force -Path $diagnosticDirectory | Out-Null
    }
    $payload = [ordered]@{
        status = $Status
        phase = $script:smokePhase
        message = $Message
        timestamp_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($DiagnosticsPath, $payload, $utf8NoBom)
}

function Set-SmokePhase([string]$Phase) {
    $script:smokePhase = $Phase
    Write-Host "[artifact-smoke] $Phase"
    Write-SmokeDiagnostics 'running'
}

function Stop-ProcessTree([System.Diagnostics.Process]$Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            & $taskkill /PID $Process.Id /T /F 2>$null | Out-Null
            $Process.WaitForExit(10000) | Out-Null
        }
    } catch {
        Write-Verbose "Process tree cleanup skipped for PID $($Process.Id): $($_.Exception.Message)"
    }
}

function Stop-InstalledProcesses([string]$RootPath) {
    $normalizedRoot = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\') + '\'
    Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $processPath = $_.Path
            if ($processPath -and $processPath.StartsWith(
                $normalizedRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                Stop-ProcessTree $_
            }
        } catch {
            # Some system processes do not expose Path. They are unrelated to this install.
        }
    }
}

function Get-BackgroundStartupTaskState {
    try {
        $service = New-Object -ComObject 'Schedule.Service'
        $service.Connect()
        $folder = $service.GetFolder('\')
        $null = $folder.GetTask($backgroundTaskName)
        return 'present'
    } catch {
        if (($_.Exception.HResult -band 0xFFFF) -eq 2) {
            return 'absent'
        }
        throw 'Could not query the AskRex background startup task.'
    }
}

function Assert-BackgroundStartupTaskPresent {
    if ((Get-BackgroundStartupTaskState) -ne 'present') {
        throw 'Installed artifact did not register the AskRex background ONLOGON task.'
    }
}

function Assert-BackgroundStartupTaskAbsent {
    if ((Get-BackgroundStartupTaskState) -ne 'absent') {
        throw 'AskRex background startup task survived uninstall.'
    }
}

function Remove-SmokeBackgroundStartupTask {
    $schtasks = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    try {
        & $schtasks /End /TN $backgroundTaskName *> $null
    } catch {
        Write-Verbose "Background task end cleanup skipped: $($_.Exception.Message)"
    }
    try {
        & $schtasks /Delete /TN $backgroundTaskName /F *> $null
    } catch {
        Write-Verbose "Background task delete cleanup skipped: $($_.Exception.Message)"
    }
}

function Invoke-Installer(
    [string[]]$Arguments,
    [int]$TimeoutSeconds = 600
) {
    $process = Start-Process -FilePath $installerPath -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-ProcessTree $process
        throw "Installer timed out after $TimeoutSeconds seconds."
    }
    if ($process.ExitCode -ne 0) { throw "Installer exited with code $($process.ExitCode)" }
}

function Invoke-Uninstaller(
    [string]$UninstallerPath,
    [string]$InstallRoot,
    [int]$TimeoutSeconds = 300
) {
    if (-not (Test-Path -LiteralPath $UninstallerPath -PathType Leaf)) {
        throw "Uninstaller is missing: $UninstallerPath"
    }

    # Mirror electron-builder's own upgrade path: copy the uninstaller outside
    # the installation directory, then run that copy with _?= so WaitForExit
    # observes the process that actually removes the installed files. Running
    # the installed executable with _?= directly can fail because it is still
    # located inside the directory it is deleting.
    $uninstallCopyRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('askrex-uninstall-' + [guid]::NewGuid().ToString('N'))
    $uninstallCopy = Join-Path $uninstallCopyRoot 'uninstaller.exe'
    New-Item -ItemType Directory -Force -Path $uninstallCopyRoot | Out-Null
    Copy-Item -LiteralPath $UninstallerPath -Destination $uninstallCopy -Force

    try {
        $process = Start-Process -FilePath $uninstallCopy -ArgumentList @('/S', '/currentuser', "_?=$InstallRoot") -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-ProcessTree $process
            throw "Uninstaller timed out after $TimeoutSeconds seconds."
        }
        if ($process.ExitCode -ne 0) { throw "Uninstaller exited with code $($process.ExitCode)" }
    } finally {
        if (Test-Path -LiteralPath $uninstallCopyRoot) {
            Remove-Item -LiteralPath $uninstallCopyRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-Uninstalled([string]$ApplicationPath) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath $ApplicationPath) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $ApplicationPath) {
        throw 'Uninstall left the application executable behind.'
    }
}

function Remove-SmokeTestRoot([string]$RootPath) {
    if (-not (Test-Path -LiteralPath $RootPath)) { return }
    $resolvedRoot = (Resolve-Path -LiteralPath $RootPath).Path
    $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean test path outside the temp directory: $resolvedRoot"
    }

    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 10) {
                Write-Warning "Could not fully remove temporary smoke directory after 10 attempts: $resolvedRoot. $($_.Exception.Message)"
                return
            }
            Start-Sleep -Seconds 2
        }
    }
}

function Invoke-IdentityBridge(
    [string]$PythonExe,
    [string]$BridgeScript,
    [string]$Payload
) {
    # Windows PowerShell 5 can transcode native-command pipeline input and its
    # ProcessStartInfo lacks the modern encoding properties. Use exact UTF-8 file-backed
    # stdin redirection so the packaged bridge receives byte-for-byte valid JSON.
    $callRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('askrex-identity-' + [guid]::NewGuid())
    $stdinPath = Join-Path $callRoot 'stdin.json'
    $stdoutPath = Join-Path $callRoot 'stdout.txt'
    $stderrPath = Join-Path $callRoot 'stderr.txt'
    New-Item -ItemType Directory -Force -Path $callRoot | Out-Null

    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($stdinPath, $Payload, $utf8NoBom)
        $arguments = "-I `"$BridgeScript`""
        $startProcessArguments = @{
            FilePath = $PythonExe
            ArgumentList = $arguments
            RedirectStandardInput = $stdinPath
            RedirectStandardOutput = $stdoutPath
            RedirectStandardError = $stderrPath
            Wait = $true
            PassThru = $true
            NoNewWindow = $true
        }
        $process = Start-Process @startProcessArguments

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = if (Test-Path -LiteralPath $stdoutPath) {
                Get-Content -LiteralPath $stdoutPath -Raw
            } else {
                ''
            }
            Stderr = if (Test-Path -LiteralPath $stderrPath) {
                Get-Content -LiteralPath $stderrPath -Raw
            } else {
                ''
            }
        }
    } finally {
        if (Test-Path -LiteralPath $callRoot) {
            Remove-Item -LiteralPath $callRoot -Recurse -Force
        }
    }
}

function Invoke-ElectronBackgroundSurvivalSmoke(
    [string]$RuntimeRoot,
    [string]$RuntimePython
) {
    # The packaged Electron process has exited. Its detached supervisor must
    # remain independently alive and publish fresh, content-free health.
    $statusArgs = @('-I', '-m', 'rex.background.cli', 'status', '--runtime-root', $RuntimeRoot)
    $statusOutput = & $RuntimePython @statusArgs 2>$null
    $statusExit = $LASTEXITCODE
    if ($statusExit -ne 0 -or -not $statusOutput) {
        throw 'Electron background survival smoke: detached supervisor status is unavailable after GUI exit.'
    }
    $status = ($statusOutput | Select-Object -Last 1) | ConvertFrom-Json
    $supervisorPid = [int]$status.supervisor_pid
    if ($supervisorPid -le 0 -or -not (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)) {
        throw 'Electron background survival smoke: detached supervisor is not alive after GUI exit.'
    }

    Write-Host 'Electron background survival smoke passed (detached supervisor survived GUI exit and remains live for uninstall verification).'
    return $supervisorPid
}

function Invoke-BackgroundLifecycleSmoke(
    [string]$Resources,
    [string]$RuntimeRoot,
    [string]$RuntimePython
) {
    # Deterministic lifecycle proof uses the real installed RuntimeSupervisor
    # from isolated managed Python, with self-contained child fakes copied into
    # the temporary runtime root. No product test hook or source-tree import is used.
    $runtimePythonW = Join-Path $Resources 'python\pythonw.exe'
    if (-not (Test-Path -LiteralPath $runtimePythonW -PathType Leaf)) {
        throw "Installed artifact is missing: $runtimePythonW"
    }
    $harnessSource = Join-Path $PSScriptRoot 'background_lifecycle_harness.py'
    $fakeChildSource = Join-Path $PSScriptRoot 'background_lifecycle_fake_child.py'
    foreach ($required in @($harnessSource, $fakeChildSource)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Lifecycle smoke fixture is missing: $required"
        }
    }

    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    Remove-Item -Recurse -Force (Join-Path $RuntimeRoot 'background') -ErrorAction SilentlyContinue
    $harnessScript = Join-Path $RuntimeRoot 'background_lifecycle_harness.py'
    $fakeChildScript = Join-Path $RuntimeRoot 'background_lifecycle_fake_child.py'
    Copy-Item -LiteralPath $harnessSource -Destination $harnessScript -Force
    Copy-Item -LiteralPath $fakeChildSource -Destination $fakeChildScript -Force

    $importProbe = & $RuntimePython -I -c "import pathlib,sys,rex.background; p=pathlib.Path(rex.background.__file__).resolve(); r=pathlib.Path(sys.executable).resolve().parent/'Lib'/'site-packages'; print(p); raise SystemExit(0 if r in p.parents else 9)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Background lifecycle smoke: managed rex.background import escaped installed resources: $importProbe"
    }

    $statusArgs = @('-I', '-m', 'rex.background.cli', 'status', '--runtime-root', $RuntimeRoot)
    $harnessArgs = @('-I', $harnessScript, $RuntimeRoot, $fakeChildScript)
    $harnessArgumentString = "-I `"$harnessScript`" `"$RuntimeRoot`" `"$fakeChildScript`""
    $supervisorProcess = $null
    try {
        $supervisorProcess = Start-Process -FilePath $runtimePythonW -ArgumentList $harnessArgumentString -PassThru -WindowStyle Hidden

        $statusReady = $false
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ([DateTime]::UtcNow -lt $deadline) {
            $statusOutput = & $RuntimePython @statusArgs 2>$null
            $statusExit = $LASTEXITCODE
            if ($statusExit -eq 0 -and $statusOutput) {
                $status = ($statusOutput | Select-Object -Last 1) | ConvertFrom-Json
                if ($status.core.state -eq 'ready' -and $status.voice_agent.state -eq 'ready') {
                    $statusReady = $true
                    break
                }
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $statusReady) {
            throw 'Background lifecycle smoke: fake-child supervisor never reached ready status.'
        }

        & $RuntimePython @harnessArgs *> $null
        if ($LASTEXITCODE -ne 2) {
            throw "Background lifecycle smoke: duplicate supervisor start was not rejected (exit $LASTEXITCODE)."
        }
        & $RuntimePython @statusArgs *> $null
        if ($LASTEXITCODE -ne 0) {
            throw 'Background lifecycle smoke: status became unreadable after duplicate-start probe.'
        }
        if ($supervisorProcess.HasExited) {
            throw 'Background lifecycle smoke: detached supervisor exited unexpectedly.'
        }

        & $RuntimePython -I -m rex.background.cli stop --runtime-root $RuntimeRoot *> $null
        if ($LASTEXITCODE -ne 0) {
            throw 'Background lifecycle smoke: stop request was rejected.'
        }
        if (-not $supervisorProcess.WaitForExit(15000)) {
            throw 'Background lifecycle smoke: supervisor did not exit after an orderly stop request.'
        }
        & $RuntimePython @statusArgs *> $null
        if ($LASTEXITCODE -eq 0) {
            throw 'Background lifecycle smoke: status still reports live after orderly stop.'
        }
    } finally {
        if ($null -ne $supervisorProcess -and -not $supervisorProcess.HasExited) {
            Stop-ProcessTree $supervisorProcess
        }
    }
    Write-Host 'Background lifecycle smoke passed (managed import, duplicate prevention, ready status, detached liveness, orderly stop) using packaged Windows artifact / deterministic child fakes.'
}

try {
    Write-SmokeDiagnostics 'running'
    New-Item -ItemType Directory -Force -Path $testRoot, $localAppData, $appData, $runtimeRoot | Out-Null

    Set-SmokePhase 'initial-install'
    Invoke-Installer @('/S', "/D=$installPath")

    $appExe = Join-Path $installPath 'AskRex.exe'
    $uninstaller = Join-Path $installPath 'Uninstall AskRex.exe'
    $resources = Join-Path $installPath 'resources'
    $runtimePython = Join-Path $resources 'python\python.exe'
    foreach ($required in @($appExe, $uninstaller, $runtimePython)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Installed artifact is missing: $required"
        }
    }

    Set-SmokePhase 'installed-resource-verification'
    & $buildPythonPath (Join-Path $PSScriptRoot 'verify_electron_package_contents.py') $resources
    if ($LASTEXITCODE -ne 0) { throw 'Installed resource verification failed.' }

    $env:LOCALAPPDATA = $localAppData
    $env:APPDATA = $appData
    $env:ASKREX_RUNTIME_DIR = $runtimeRoot
    & $runtimePython -I -c "from rex.auth import create_user; from rex.identity import set_session_user; from rex.permissions import bootstrap_admin_if_first_user; u=create_user('artifact-smoke-user','artifact-smoke-password'); bootstrap_admin_if_first_user(str(u['id'])); set_session_user('artifact-ci-user')"
    if ($LASTEXITCODE -ne 0) { throw 'Managed runtime could not bootstrap setup state and establish the smoke identity.' }

    Set-SmokePhase 'identity-bridge'
    $identityPayload = '{"action":"resolve_electron_session"}'
    $identityBridge = Join-Path $resources 'bridge\rex_identity_bridge.py'
    $identityResult = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $identityResult = Invoke-IdentityBridge $runtimePython $identityBridge $identityPayload
        if ($identityResult.ExitCode -eq 0) { break }
        if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
    }
    if ($null -eq $identityResult -or $identityResult.ExitCode -ne 0) {
        $exitCode = if ($null -eq $identityResult) { -1 } else { $identityResult.ExitCode }
        $diagnostic = if ($null -eq $identityResult) {
            'identity bridge did not start'
        } else {
            $parts = @($identityResult.Stdout.Trim(), $identityResult.Stderr.Trim()) |
                Where-Object { $_ }
            $parts -join ' '
        }
        throw "Read-only identity bridge action failed after 3 attempts (exit $exitCode): $diagnostic"
    }
    $identityJson = $identityResult.Stdout -split '\r?\n' |
        Where-Object { $_.TrimStart().StartsWith('{') } |
        Select-Object -Last 1
    if (-not $identityJson) {
        throw "Read-only identity bridge returned no JSON: $($identityResult.Stdout) $($identityResult.Stderr)"
    }
    $identity = $identityJson | ConvertFrom-Json
    if (-not $identity.ok -or $identity.user_id -ne 'artifact-ci-user') {
        throw 'Read-only identity bridge returned an unexpected result.'
    }

    Set-SmokePhase 'electron-ipc-smoke'
    $env:ASKREX_ARTIFACT_SMOKE = '1'
    $env:ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT = $runtimeRoot
    $env:ASKREX_ARTIFACT_SMOKE_OUTPUT = $smokeOutput
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $app = Start-Process -FilePath $appExe -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while (-not (Test-Path -LiteralPath $smokeOutput) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Path -LiteralPath $smokeOutput)) {
        Stop-ProcessTree $app
        throw 'Installed app did not produce the IPC smoke result within 90 seconds.'
    }
    $result = Get-Content -LiteralPath $smokeOutput -Raw | ConvertFrom-Json
    if (-not $result.ok -or -not $result.typed_ipc -or
        $result.chat -ne 'AskRex installed artifact chat verified' -or
        $result.memories_count -lt 0 -or -not $result.openclaw_settings -or
        -not $result.openclaw_settings_read_write -or -not $result.settings_sections) {
        throw "Installed app smoke failed: $(Get-Content -LiteralPath $smokeOutput -Raw)"
    }
    Assert-BackgroundStartupTaskPresent

    if (-not $app.WaitForExit(15000)) {
        Stop-ProcessTree $app
        throw 'Electron process did not exit before background survival verification.'
    }

    Set-SmokePhase 'electron-background-survival'
    $survivingSupervisorPid = Invoke-ElectronBackgroundSurvivalSmoke -RuntimeRoot $runtimeRoot -RuntimePython $runtimePython

    Set-SmokePhase 'background-lifecycle-smoke'
    Invoke-BackgroundLifecycleSmoke -Resources $resources -RuntimeRoot $lifecycleRuntimeRoot -RuntimePython $runtimePython

    Set-SmokePhase 'first-uninstall'
    Invoke-Uninstaller $uninstaller $installPath
    Assert-Uninstalled $appExe
    Assert-BackgroundStartupTaskAbsent
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline -and (Get-Process -Id $survivingSupervisorPid -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $survivingSupervisorPid -ErrorAction SilentlyContinue) {
        throw 'Electron background supervisor survived uninstall.'
    }

    Set-SmokePhase 'reinstall'
    Invoke-Installer @('/S', "/D=$installPath")
    foreach ($required in @($appExe, $uninstaller, $runtimePython)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Reinstalled artifact is missing: $required"
        }
    }
    & $buildPythonPath (Join-Path $PSScriptRoot 'verify_electron_package_contents.py') $resources
    if ($LASTEXITCODE -ne 0) { throw 'Reinstalled resource verification failed.' }

    Set-SmokePhase 'final-uninstall'
    Stop-InstalledProcesses $installPath
    Invoke-Uninstaller $uninstaller $installPath
    Assert-Uninstalled $appExe

    Set-SmokePhase 'complete'
    Write-SmokeDiagnostics 'success' 'Installed artifact passed all smoke phases.'
    Write-Host 'Installed AskRex artifact smoke passed (IPC, managed bridge, chat, uninstall, reinstall, final uninstall).'
} catch {
    Write-SmokeDiagnostics 'failure' $_.Exception.Message
    throw
} finally {
    Remove-SmokeBackgroundStartupTask
    Remove-Item Env:ASKREX_ARTIFACT_SMOKE -ErrorAction SilentlyContinue
    Remove-Item Env:ASKREX_ARTIFACT_SMOKE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:ASKREX_RUNTIME_DIR -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $installPath) {
        Stop-InstalledProcesses $installPath
    }
    Remove-SmokeTestRoot $testRoot
}
