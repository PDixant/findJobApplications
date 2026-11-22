@echo off
rem Removes the Job Watcher shortcuts, the jobwatcher command and the .venv folder.
rem Your settings, companies list and tracker stay.
setlocal
cd /d "%~dp0"
title Uninstalling Job Watcher
set "PYEXE="
set "FINDPY=%~dp0install\find_python.py"
for /f "delims=" %%i in ('py -3 "%FINDPY%" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('python "%FINDPY%" 2^>nul') do set "PYEXE=%%i"
for %%v in (313 312 311 310) do (
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
  if not defined PYEXE if exist "%ProgramFiles%\Python%%v\python.exe" set "PYEXE=%ProgramFiles%\Python%%v\python.exe"
)
rem Any Python will do for removing things, the Store one included.
if not defined PYEXE for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
  echo Python was not found, so this can't run. Delete the .venv folder and the Job Watcher shortcuts by hand.
  pause
  exit /b 1
)
"%PYEXE%" "%~dp0install\install.py" --uninstall
pause
