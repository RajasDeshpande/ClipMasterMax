@echo off
title ClipMasterMax
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m app.main
pause
