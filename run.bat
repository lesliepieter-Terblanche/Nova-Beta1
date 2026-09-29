@echo off
cd /d "%~dp0"
title Nova
if not exist .venv\Scripts\activate.bat (
  echo Run setup.bat first.
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
rem make sure the local AI engine is running
tasklist /fi "imagename eq ollama.exe" | find /i "ollama.exe" >nul || start "" /min ollama serve
:loop
python main.py %*
if %errorlevel%==42 (
  echo Restarting Nova...
  timeout /t 2 /nobreak >nul
  goto loop
)
if not %errorlevel%==0 pause
