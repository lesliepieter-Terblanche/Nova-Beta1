@echo off
rem Opens Nova's 3D brain. Starts Nova first if it isn't running.
cd /d "%~dp0"
curl -s -m 3 http://127.0.0.1:8765/api/stats >nul 2>nul
if not errorlevel 1 (
  start "" http://localhost:8765
  exit /b 0
)
echo Starting Nova... the dashboard opens by itself in a moment.
start "Nova" /min "%~dp0run.bat"
for /l %%i in (1,1,45) do (
  timeout /t 2 /nobreak >nul
  curl -s -m 2 http://127.0.0.1:8765/api/stats >nul 2>nul && exit /b 0
)
echo.
echo Nova didn't start. Open the Nova window on the taskbar to see why,
echo or send data\logs\nova.log to whoever is helping you.
pause
