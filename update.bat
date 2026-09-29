@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo Checking GitHub for updates...
python -m nova.updater update
pause
