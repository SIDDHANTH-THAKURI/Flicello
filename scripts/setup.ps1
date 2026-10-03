# Creates the VidoSuper Python environment outside OneDrive and installs the package (editable).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $env:LOCALAPPDATA "VidoSuper\venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating Python environment in $venv"
    py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

& $python -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

& $python -m pip install -e "$root[ai,dev]"
if ($LASTEXITCODE -ne 0) { throw "package install failed" }

& $python -m pip freeze --exclude-editable | Out-File -Encoding utf8 (Join-Path $root "requirements.lock.txt")
Write-Host "Done. Run: .\vido.cmd doctor"
