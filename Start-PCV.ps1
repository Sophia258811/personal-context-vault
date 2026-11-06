param([string]$ChromeExtensionId)

$ErrorActionPreference = 'Stop'
$candidates = @(
    $env:PCV_PYTHON,
    (Join-Path $HOME '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$pcvPython = $candidates | Select-Object -First 1
if (-not $pcvPython) { throw 'Install Python 3.11+ with tkinter, or set PCV_PYTHON.' }
& $pcvPython -c "import sys, tkinter; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ with tkinter is required.' }
if ($ChromeExtensionId) {
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $pcvPython -m pcv.native_host --install $ChromeExtensionId
        if ($LASTEXITCODE -ne 0) { throw 'Chrome native host registration failed.' }
    } finally {
        Pop-Location
    }
    return
}
$windowed = Join-Path (Split-Path $pcvPython) 'pythonw.exe'
if (Test-Path -LiteralPath $windowed) { $pcvPython = $windowed }
Start-Process -FilePath $pcvPython -ArgumentList @('-m', 'pcv.desktop') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
