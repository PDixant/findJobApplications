@echo off
rem Double-click to set up Job Watcher: Start menu and Desktop shortcuts, and a "jobwatcher"
rem command for any terminal. Safe to run again to repair or update. Your data is not touched.
setlocal
cd /d "%~dp0"
title Installing Job Watcher
echo Setting up Job Watcher. The first time takes a few minutes.
echo.

set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys; sys.version_info[:2] < (3, 10) or print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
  for /f "delims=" %%i in ('python -c "import sys; sys.version_info[:2] < (3, 10) or print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo Python 3.10 or newer was not found. Installing Python 3.12 with Windows' package manager...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
  echo.
  echo Python could not be installed automatically. Opening the download page:
  echo install Python, tick "Add python.exe to PATH", then double-click this file again.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

"%PYEXE%" "%~dp0install\install.py" %*
if errorlevel 1 (
  echo.
  echo Setup did not finish. The messages above say why. If Job Watcher is open, close it and try again.
  pause
  exit /b 1
)
echo.
pause
