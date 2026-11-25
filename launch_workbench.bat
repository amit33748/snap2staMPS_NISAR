@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Independent virtual environment .venv\Scripts\python.exe not found!
    echo Create it with standalone Python 3.11: python -m venv .venv
    pause
    exit /b 1
)
call "gui\run_dashboard.bat"
