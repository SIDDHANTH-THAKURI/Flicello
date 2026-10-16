@echo off
rem Renders flicello.com's videos on this PC (see src/vidosuper/studio/remote.py). Keep this window open: while it
rem runs, videos made on the site are rendered here and sent back. Needs VIDO_STUDIO_WORKER_TOKEN in .env.
title Flicello renderer
cd /d "%~dp0"
:again
"%LOCALAPPDATA%\VidoSuper\venv\Scripts\python.exe" -m vidosuper studio remote-worker --server https://flicello.com %*
echo The renderer stopped. Starting it again in 15 seconds (close this window to stop it for good).
timeout /t 15 /nobreak >nul
goto again
