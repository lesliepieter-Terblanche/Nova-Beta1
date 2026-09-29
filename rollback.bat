@echo off
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo Recent versions:
echo.
python -m nova.updater versions
echo.
set "T="
set /p T=Roll back to which version? (tag like v1.0.0, commit id, or just Enter for "the one before the last update"): 
if "%T%"=="" set "T=previous"
python -m nova.updater rollback %T%
pause
