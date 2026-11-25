@echo off
setlocal enabledelayedexpansion

title snap2stamps_nisar Workbench Launcher

echo ===============================================================================
echo                 snap2stamps_nisar Daylight Survey Workbench
echo ===============================================================================
echo.

cd /d "%~dp0"
set "PYTHONUTF8=1"

set "PY_CMD="
if exist "..\.venv\Scripts\python.exe" (
    set "PY_CMD=..\.venv\Scripts\python.exe"
)

if "%PY_CMD%"=="" (
    echo [ERROR] Independent virtual environment ..\.venv\Scripts\python.exe was not found!
    echo Create it using standalone Python ^(not ArcGIS Pro^):
    echo   cd ..
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [INFO] Using independent Python: %PY_CMD%
echo [INFO] Starting Workbench Web App at http://127.0.0.1:8501
echo.

"%PY_CMD%" server.py --port 8501 --open

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Workbench server closed or encountered an issue.
    pause
)
