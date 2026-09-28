[CmdletBinding()]
param(
    [ValidateSet('Core', 'Voice', 'Full')]
    [Alias('Profile')]
    [string]$RuntimeProfile = 'Voice',
    [string]$PythonVersion = '3.11.9',
    [string]$BuildPython = '',
    [string]$RuntimeRoot
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $repoRoot 'gui\runtime\python'
}
$runtimePath = [System.IO.Path]::GetFullPath($RuntimeRoot)
$expectedParent = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'gui\runtime'))
if (-not $runtimePath.StartsWith($expectedParent + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "RuntimeRoot must stay under $expectedParent"
}
if (-not $BuildPython) {
    $repoPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $BuildPython = if (Test-Path -LiteralPath $repoPython) { $repoPython } else { 'python' }
}

$buildVersion = & $BuildPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $buildVersion.Trim() -ne '3.11') {
    throw 'The managed runtime must be built with Python 3.11.'
}

$runtimeArchive = "python-$PythonVersion-embed-amd64.zip"
$runtimeUrl = "https://www.python.org/ftp/python/$PythonVersion/$runtimeArchive"
$lockedSha256 = '009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B' # pragma: allowlist secret
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("askrex-runtime-" + [guid]::NewGuid())
$archivePath = Join-Path $tempRoot $runtimeArchive
$wheelDir = Join-Path $tempRoot 'wheel'

try {
    New-Item -ItemType Directory -Force -Path $tempRoot, $wheelDir | Out-Null
    Invoke-WebRequest -Uri $runtimeUrl -OutFile $archivePath
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $archiveStream = [System.IO.File]::OpenRead($archivePath)
    try {
        $actualHash = ([System.BitConverter]::ToString($sha256.ComputeHash($archiveStream))).Replace('-', '')
    } finally {
        $archiveStream.Dispose()
        $sha256.Dispose()
    }
    if ($actualHash -ne $lockedSha256) {
        throw "Python runtime SHA256 mismatch. Expected $lockedSha256, got $actualHash"
    }

    & $BuildPython -m build --wheel --outdir $wheelDir $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Python wheel build failed.' }
    $wheel = Get-ChildItem -LiteralPath $wheelDir -Filter 'askrex_assistant-*.whl' | Select-Object -First 1
    if (-not $wheel) { throw 'AskRex wheel was not produced.' }

    if (Test-Path -LiteralPath $runtimePath) {
        $resolvedRuntime = (Resolve-Path -LiteralPath $runtimePath).Path
        if (-not $resolvedRuntime.StartsWith($expectedParent + [System.IO.Path]::DirectorySeparatorChar)) {
            throw "Refusing to remove runtime outside $expectedParent"
        }
        Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimePath

    $pth = Get-ChildItem -LiteralPath $runtimePath -Filter 'python*._pth' | Select-Object -First 1
    if (-not $pth) { throw 'Embedded Python path configuration was not found.' }
    $pthLines = Get-Content -LiteralPath $pth.FullName
    $pthLines = $pthLines | ForEach-Object {
        if ($_ -match '^#\s*import site\s*$') { 'import site' } else { $_ }
    }
    if ($pthLines -notcontains 'Lib\site-packages') { $pthLines += 'Lib\site-packages' }
    Set-Content -LiteralPath $pth.FullName -Value $pthLines -Encoding ascii

    $sitePackages = Join-Path $runtimePath 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    & $BuildPython -m pip install --disable-pip-version-check --no-compile --no-deps --upgrade --target $sitePackages $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw 'AskRex runtime dependency installation failed.' }
    & $BuildPython -m pip install --disable-pip-version-check --no-compile --upgrade --target $sitePackages -r (Join-Path $repoRoot 'requirements-electron-runtime.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Electron core runtime dependency installation failed.' }

    if ($RuntimeProfile -eq 'Voice') {
        & $BuildPython -m pip install --disable-pip-version-check --no-compile --upgrade --target $sitePackages -r (Join-Path $repoRoot 'requirements-electron-voice.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Voice runtime dependency installation failed.' }
    } elseif ($RuntimeProfile -eq 'Full') {
        & $BuildPython -m pip install --disable-pip-version-check --no-compile --upgrade --target $sitePackages ($wheel.FullName + '[full]')
        if ($LASTEXITCODE -ne 0) { throw 'Full runtime dependency installation failed.' }
    }

    $runtimePython = Join-Path $runtimePath 'python.exe'
    & $runtimePython -I -c "import rex, requests; print('managed-runtime-ok')"
    if ($LASTEXITCODE -ne 0) { throw 'Managed runtime import smoke test failed.' }
    if ($RuntimeProfile -eq 'Voice') {
        & $runtimePython -I -c "import torch, whisper, openwakeword, scipy, onnxruntime, sklearn; assert torch.__version__.split('+', 1)[0] == '2.12.1'; print('managed-voice-runtime-ok')"
        if ($LASTEXITCODE -ne 0) { throw 'Managed Voice runtime dependency smoke test failed.' }
    }
    & $runtimePython -I -c "import importlib.util, sys; sys.exit(1 if importlib.util.find_spec('flask') else 0)"
    if ($LASTEXITCODE -ne 0) { throw 'Flask must not be present in the Electron runtime.' }

    $askrexVersion = $wheel.BaseName -replace '^askrex_assistant-([^-]+)-.*$', '$1'
    $metadata = [ordered]@{
        architecture = 'windows-x64-embedded-python'
        python_version = $PythonVersion
        askrex_version = $askrexVersion
        profile = $RuntimeProfile.ToLowerInvariant()
        runtime_sha256 = $lockedSha256
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimePath 'ASKREX_RUNTIME.json') -Encoding utf8
    Write-Host "Managed AskRex runtime built at $runtimePath (profile=$RuntimeProfile)"
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
