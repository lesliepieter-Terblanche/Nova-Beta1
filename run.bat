@echo off
cd /d "%~dp0"
title Nova
if not exist .venv\Scripts\activate.bat (
  echo Run setup.bat first.
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
set "NOVA_LAUNCHER=run.bat"
rem keep the AI model loaded between commands (no 10-30 s reload after a pause)
if not defined OLLAMA_KEEP_ALIVE (
  setx OLLAMA_KEEP_ALIVE 24h >nul 2>nul
  set "OLLAMA_KEEP_ALIVE=24h"
)
rem make sure the local AI engine is running and answering (restart it if it's frozen)
curl -s -m 5 http://127.0.0.1:11434/api/version >nul 2>nul
if errorlevel 1 (
  taskkill /f /im "ollama app.exe" >nul 2>nul
  taskkill /f /im ollama.exe >nul 2>nul
  set "OLLAMA=ollama"
  where ollama >nul 2>nul || set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  call start "" /min "%%OLLAMA%%" serve
  timeout /t 5 /nobreak >nul
)
:loop
python main.py %*
if %errorlevel%==42 (
  echo Restarting Nova...
  timeout /t 2 /nobreak >nul
  goto loop
)
if not %errorlevel%==0 pause
