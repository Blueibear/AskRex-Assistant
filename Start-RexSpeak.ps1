$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:REX_SPEAK_API_KEY)) {
    throw "REX_SPEAK_API_KEY must be set before starting RexSpeak."
}
$env:REX_SPEAK_STORAGE_URI = 'redis://127.0.0.1:6379/0'   # optional

$RepoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "RexSpeak Python interpreter was not found at the expected absolute path: $VenvPython"
}

$env:PYTHONPATH = $RepoRoot
& $VenvPython -m waitress --listen=127.0.0.1:8000 wsgi:application
exit $LASTEXITCODE
