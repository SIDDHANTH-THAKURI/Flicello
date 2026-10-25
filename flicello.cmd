@echo off
rem Flicello on this computer. The first run sets up its Python environment, builds the app and the motion engine,
rem then opens http://127.0.0.1:8765/connect, where everything else (models, keys, Blender) is checked and connected.
rem FLICELLO_VENV: where the Python environment lives (default: .venv here; outside a synced folder like OneDrive
rem is better).
setlocal
cd /d "%~dp0"
if not defined FLICELLO_VENV set "FLICELLO_VENV=%~dp0.venv"
set "PY=%FLICELLO_VENV%\Scripts\python.exe"

if not exist "%PY%" (
  echo Setting up Flicello's Python environment in %FLICELLO_VENV% ...
  py -3.12 -m venv "%FLICELLO_VENV%" 2>nul || python -m venv "%FLICELLO_VENV%"
  if not exist "%PY%" (
    echo Flicello needs Python 3.11 or newer: https://www.python.org/downloads/
    exit /b 1
  )
)
"%PY%" -c "import vidosuper.studio.web.app, keyring" 2>nul
if errorlevel 1 (
  echo Installing Flicello's Python packages ...
  "%PY%" -m pip install --upgrade pip --quiet
  "%PY%" -m pip install -e ".[ai,studio]"
  if errorlevel 1 exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Flicello needs Node.js 22 or newer: https://nodejs.org
  exit /b 1
)
if not exist "src\vidosuper\studio\web\static\dist\manifest.json" (
  echo Building Flicello's pages ...
  pushd frontend
  call npm ci --no-audit --no-fund
  call npm run build
  popd
)
if not exist "motion\node_modules\.bin\hyperframes.cmd" (
  echo Installing the motion engine ...
  pushd motion
  call npm ci --no-audit --no-fund
  call npx hyperframes browser ensure
  popd
)

"%PY%" -m vidosuper flicello %*
