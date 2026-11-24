@echo off
chcp 65001 >nul 2>nul
rem GEO Insight local launcher. Double-click this file.
cd /d "%~dp0backend"

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if defined PY goto run

echo.
echo   Python 3.10+ was not found on this computer.
echo   Install it from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:run
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
%PY% run.py %*
if errorlevel 1 pause
exit /b 0
