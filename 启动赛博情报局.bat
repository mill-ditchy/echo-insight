@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please install first: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "desktop.py"
