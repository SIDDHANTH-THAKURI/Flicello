@echo off
rem Starts VidoSuper Studio (web app + worker) on http://127.0.0.1:8765. Keep this window open while friends use it.
cd /d "%~dp0"
"%LOCALAPPDATA%\VidoSuper\venv\Scripts\python.exe" -m vidosuper studio serve %*
pause
