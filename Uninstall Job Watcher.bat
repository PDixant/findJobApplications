@echo off
rem Removes the Job Watcher shortcuts, the jobwatcher command and the .venv folder.
rem Your settings, companies list and tracker stay.
setlocal
cd /d "%~dp0"
title Uninstalling Job Watcher
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys; sys.version_info[:2] < (3, 10) or print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
  for /f "delims=" %%i in ('python -c "import sys; sys.version_info[:2] < (3, 10) or print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo Python was not found, so this can't run. Delete the .venv folder and the Job Watcher shortcuts by hand.
  pause
  exit /b 1
)
"%PYEXE%" "%~dp0install\install.py" --uninstall
pause
