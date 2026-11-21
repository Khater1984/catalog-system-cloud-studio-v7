@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.10 or newer from python.org and tick "Add python.exe to PATH", then run this file again.
  pause
  exit /b
)
if not exist venv ( python -m venv venv )
call venv\Scripts\activate
if not exist venv\.installed (
  echo Installing libraries, first run only...
  pip install -r requirements.txt
  if errorlevel 1 ( echo Install failed. Check your internet connection. & pause & exit /b )
  echo ok> venv\.installed
)
set AUTH_DISABLED=1
set STORAGE_BACKEND=local
python app.py
pause
