@echo off
REM Starts the app with MADE-UP data so you can explore it safely.
REM It uses its own database in the data_demo folder and never touches real records.
setlocal
cd /d "%~dp0"
title BCI First Timers (demo data)

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/ and try again.
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r requirements.txt

python run.py --demo
pause
