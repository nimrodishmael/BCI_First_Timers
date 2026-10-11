@echo off
REM Double-click this file to start BCI First Timers.
setlocal
cd /d "%~dp0"
title BCI First Timers

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Setting up for the first time. This takes a minute or two...
  echo.
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
)

call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto pipfail

if not exist ".env" copy ".env.example" ".env" >nul

python run.py %*
echo.
echo  The app has stopped.
pause
exit /b 0

:nopython
echo.
echo  Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during installation. Then run this file again.
pause
exit /b 1

:pipfail
echo.
echo  Couldn't install the app's packages. Check the internet connection and run this file again.
pause
exit /b 1
