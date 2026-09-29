@echo off
setlocal
cd /d "%~dp0"
title Nova setup
echo.
echo  ===========================================
echo    NOVA  -  one-time setup (10-20 minutes)
echo  ===========================================
echo.

rem --- Python 3.11 -------------------------------------------------
set "PY=python"
py -3.11 --version >nul 2>nul && set "PY=py -3.11"
%PY% --version >nul 2>nul || (
  echo Python 3.11 was not found. Install it from https://www.python.org/downloads/release/python-3119/
  echo Tick "Add python.exe to PATH" during install, then run setup.bat again.
  pause & exit /b 1
)
echo [1/7] Creating the Python environment...
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul

echo [2/7] Installing Python packages (this is the long step)...
pip install -r requirements.txt || goto :fail

echo [3/7] Installing the controllable browser...
python -m playwright install chromium

echo [4/7] Installing Ollama (local AI engine)...
set "OLLAMA=ollama"
where ollama >nul 2>nul || (
  if not exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
  set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
)
start "" /min "%OLLAMA%" serve
timeout /t 6 /nobreak >nul

echo [5/7] Downloading the local brain (about 2.5 GB)...
"%OLLAMA%" pull qwen2.5:3b || goto :fail
"%OLLAMA%" pull nomic-embed-text || goto :fail
"%OLLAMA%" create nova-qwen -f Modelfile || goto :fail

echo [6/7] Downloading the offline backup voice...
python scripts\download_voice.py

echo [7/7] Your settings and desktop shortcuts...
python scripts\first_run.py
if not exist .env copy .env.example .env >nul
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1

echo.
echo  Setup complete!
echo  Next: fill in your keys in the .env file that is opening now, save it,
echo  then follow README.md steps 3-5 (Telegram ID, Google, GitHub).
echo.
notepad .env
pause
exit /b 0

:fail
echo.
echo  Something failed above. Scroll up for the error, fix it, and run setup.bat again
echo  (it skips anything already done).
pause
exit /b 1
