@echo off
setlocal
cd /d "%~dp0"
title Nova setup
echo.
echo  ===========================================
echo    NOVA  -  one-time setup (10-30 minutes)
echo  ===========================================
echo.
echo  Tip: don't click inside this window while it runs - Windows pauses it.
echo  If it ever looks stuck, press Enter once. It is safe to close and run again:
echo  finished steps are skipped and downloads resume.
echo.

rem --- Python 3.11 -------------------------------------------------
set "PY=python"
py -3.11 --version >nul 2>nul && set "PY=py -3.11"
%PY% --version >nul 2>nul || (
  echo Python 3.11 was not found. Install it from https://www.python.org/downloads/release/python-3119/
  echo Tick "Add python.exe to PATH" during install, then run setup.bat again.
  pause & exit /b 1
)
echo [1/7] Python environment...
if not exist .venv\Scripts\activate.bat %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --disable-pip-version-check -q

echo [2/7] Python packages - the first time this is the long step...
pip install -r requirements.txt --disable-pip-version-check || goto :fail

echo [3/7] Browser for Nova to control...
set "HAVEBROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "HAVEBROWSER=Google Chrome"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "HAVEBROWSER=Google Chrome"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "HAVEBROWSER=Google Chrome"
if not defined HAVEBROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "HAVEBROWSER=Microsoft Edge"
if not defined HAVEBROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "HAVEBROWSER=Microsoft Edge"
if defined HAVEBROWSER (
  echo       Using your installed %HAVEBROWSER% - no download needed.
) else (
  python -m playwright install chromium || echo       Browser download failed - browser control is off until you run: .venv\Scripts\python -m playwright install chromium
)

echo [4/7] Ollama, the local AI engine...
set "OLLAMA=ollama"
where ollama >nul 2>nul
if errorlevel 1 (
  if not exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
  set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
)
call :ollama_ok || goto :fail

echo [5/7] Local AI models...
call :model qwen2.5:3b "the local brain, 1.9 GB" || goto :fail
call :model nomic-embed-text "the memory model, 274 MB" || goto :fail
call :has nova-qwen
if errorlevel 1 (
  "%OLLAMA%" create nova-qwen -f Modelfile || goto :fail
) else (
  echo       nova-qwen already built.
)
call :has gemma3:4b
if errorlevel 1 (
  set "VIS=y"
  set /p VIS=      Also download the local vision model - 3.3 GB, lets Nova see offline? [Y/n]: 
  call :askvision
) else (
  echo       gemma3:4b already downloaded.
)

echo [6/7] Local voices - Kokoro + Piper...
python scripts\download_voice.py

echo [7/7] Your settings and desktop shortcuts...
python scripts\first_run.py
if not exist .env copy .env.example .env >nul
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1

echo.
echo  Setup complete!
echo  Next: add your keys - in the .env file opening now, or later in Nova's Settings page
echo  (gear icon on the dashboard). Then see docs\INSTALL.md steps 5-8.
echo.
notepad .env
pause
exit /b 0

rem --- download an Ollama model unless it's already there; retry up to 3 times ---
:model
call :has %~1
if not errorlevel 1 (
  echo       %~1 already downloaded.
  exit /b 0
)
echo       Downloading %~1 - %~2. The last few percent can pause while it is checked.
for /l %%i in (1,1,3) do (
  "%OLLAMA%" pull %~1 && exit /b 0
  echo       Download interrupted - restarting Ollama and retrying...
  call :ollama_restart
)
exit /b 1

rem --- is a model installed? asks Ollama over HTTP with a timeout, so it can never hang ---
:has
curl -s -m 10 http://127.0.0.1:11434/api/tags 2>nul | findstr /i /c:"\"%~1" >nul
exit /b %errorlevel%

rem --- make sure Ollama is running and answering; restart it if it's frozen ---
:ollama_ok
curl -s -m 5 http://127.0.0.1:11434/api/version >nul 2>nul && exit /b 0
echo       Starting Ollama...
call :ollama_restart
curl -s -m 5 http://127.0.0.1:11434/api/version >nul 2>nul && exit /b 0
echo       Ollama isn't answering. Restart your PC, then run setup.bat again.
exit /b 1

:ollama_restart
taskkill /f /im "ollama app.exe" >nul 2>nul
taskkill /f /im ollama.exe >nul 2>nul
timeout /t 2 /nobreak >nul
start "" /min "%OLLAMA%" serve
for /l %%i in (1,1,15) do (
  curl -s -m 2 http://127.0.0.1:11434/api/version >nul 2>nul && exit /b 0
  timeout /t 2 /nobreak >nul
)
exit /b 0

:askvision
if /i "%VIS%"=="n" exit /b 0
call :model gemma3:4b "the vision model, 3.3 GB"
exit /b 0

:fail
echo.
echo  Something failed above. Scroll up for the error, fix it, and run setup.bat again
echo  - it skips anything already done.
pause
exit /b 1
