@echo off
title ClipMasterMax setup
cd /d "%~dp0"
echo.
echo  === ClipMasterMax setup ===
echo.
where python >nul 2>nul || (echo [!] Python 3.10+ not found. Install it from https://www.python.org/downloads/ & pause & exit /b 1)
where ffmpeg >nul 2>nul || (
  echo [i] FFmpeg not found - installing with winget...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo [i] Close this window and run setup.bat again so FFmpeg is on PATH.
  pause & exit /b 1
)
if not exist .venv (python -m venv .venv)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || (echo [!] Install failed & pause & exit /b 1)
echo.
echo  Setup complete. Double-click start.bat to launch ClipMasterMax.
pause
