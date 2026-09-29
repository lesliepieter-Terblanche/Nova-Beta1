@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
rem Save your VS Code edits as a new version on GitHub.
git status --short
echo.
set "MSG="
set /p MSG=Describe what you changed: 
if "!MSG!"=="" set "MSG=Update"
git add -A
git commit -m "!MSG!"
set "LAST=none"
for /f "delims=" %%t in ('git describe --tags --abbrev^=0 2^>nul') do set "LAST=%%t"
set "TAG="
set /p TAG=Version number (last was !LAST!, e.g. v1.0.1) - Enter to skip tagging: 
if not "!TAG!"=="" git tag -a !TAG! -m "!MSG!"
git push
if not "!TAG!"=="" git push origin !TAG!
echo.
echo Saved to GitHub.
pause
