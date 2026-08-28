@echo off
cd /d "%~dp0"

if /I "%~1"=="setup" goto :setup

if exist ".venv\Scripts\pythonw.exe" (
  wscript //nologo "%~dp0start.vbs"
  exit /b 0
)

:setup
if not exist ".venv\Scripts\python.exe" (
  echo Creating venv...
  python -m venv .venv
  if errorlevel 1 pause & exit /b 1
)
if not exist "config.env" if exist "config.env.example" copy /Y config.env.example config.env >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1
".venv\Scripts\python.exe" -c "import pywin32_postinstall as p; p.install()" 2>nul
wscript //nologo "%~dp0start.vbs"
exit /b 0
