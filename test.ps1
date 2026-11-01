$ErrorActionPreference = "Stop"
$python = if ($env:PCV_PYTHON) {
    $env:PCV_PYTHON
} else {
    Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

Push-Location -LiteralPath $PSScriptRoot
try {
    & $python -m unittest discover -s tests -v
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
