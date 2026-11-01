param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PcvArgs
)

$ErrorActionPreference = "Stop"
$candidates = @(
    $env:PCV_PYTHON,
    (Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

$python = $candidates | Select-Object -First 1
if (-not $python) {
    throw "Python 3.11+ was not found. Set PCV_PYTHON to the python.exe path."
}

Push-Location -LiteralPath $PSScriptRoot
try {
    & $python -m pcv @PcvArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
