@echo off
rem Local Flicello for testing changes: http://127.0.0.1:8766 . Its own data (studio-dev), never the live site's.
rem Sign-in emails are not sent: they are written to %LOCALAPPDATA%\VidoSuper\studio-dev\outbox (open the newest file).
cd /d "%~dp0"
set "VIDO_STUDIO_HOME=%LOCALAPPDATA%\VidoSuper\studio-dev"
set "VIDO_STUDIO_MAIL_OUTBOX=1"
set "VIDO_STUDIO_JOBS=local"
set "VIDO_STUDIO_MODAL=off"
set "VIDO_STUDIO_PORT=8766"
set "VIDO_STUDIO_HOST=127.0.0.1"
set "VIDO_STUDIO_SITE_URL=http://127.0.0.1:8766"
set "PY=%LOCALAPPDATA%\VidoSuper\venv\Scripts\python.exe"
if not exist "%VIDO_STUDIO_HOME%\studio.db" (
  echo First run: making your owner account. Open the link below once.
  "%PY%" -m vidosuper studio invite Owner --owner --base http://127.0.0.1:8766
)
"%PY%" -m vidosuper studio serve --reload
pause
