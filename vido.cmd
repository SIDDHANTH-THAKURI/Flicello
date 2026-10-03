@echo off
setlocal
set "VIDO_EXE=%LOCALAPPDATA%\VidoSuper\venv\Scripts\vido.exe"
if not exist "%VIDO_EXE%" (
  echo VidoSuper is not installed yet. Run: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 1>&2
  exit /b 1
)
"%VIDO_EXE%" %*
