@echo off
rem Double-click to set up Job Watcher: Start menu and Desktop shortcuts, and a "jobwatcher"
rem command for any terminal. Safe to run again to repair or update. Your data is not touched.
setlocal
cd /d "%~dp0"
title Installing Job Watcher
echo Setting up Job Watcher. The first time takes a few minutes.
echo.

rem A python.org Python 3.10 or newer. install\find_python.py prints nothing for the Microsoft
rem Store build, which hides the files this sets up from the rest of Windows.
set "PYEXE="
set "FINDPY=%~dp0install\find_python.py"
for /f "delims=" %%i in ('py -3 "%FINDPY%" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('python "%FINDPY%" 2^>nul') do set "PYEXE=%%i"
for %%v in (313 312 311 310) do (
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
  if not defined PYEXE if exist "%ProgramFiles%\Python%%v\python.exe" set "PYEXE=%ProgramFiles%\Python%%v\python.exe"
)
if not defined PYEXE (
  echo Python 3.10 or newer from python.org was not found. The Microsoft Store version can't be
  echo used: it hides the files Job Watcher sets up from the rest of Windows.
  echo Installing Python 3.12 from python.org with Windows' package manager...
  echo.
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
  echo.
  echo Python could not be installed automatically. Opening the download page:
  echo install Python from python.org, then double-click this file again.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)
echo Using %PYEXE%
echo.

"%PYEXE%" "%~dp0install\install.py" %*
if errorlevel 1 (
  echo.
  echo Setup did not finish. The messages above say why. If Job Watcher is open, close it and try again.
  pause
  exit /b 1
)
echo.
pause
