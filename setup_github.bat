@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Nova - connect to GitHub

where git >nul 2>nul
if errorlevel 1 (
  echo Installing Git...
  winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
  echo.
  echo Git installed. Close this window and double-click setup_github.bat again.
  pause & exit /b
)

if not exist .git git init -b main

git config user.name >nul 2>nul
if errorlevel 1 (
  set /p GN=Your name for version history: 
  git config user.name "!GN!"
)
git config user.email >nul 2>nul
if errorlevel 1 (
  set /p GE=Your GitHub email: 
  git config user.email "!GE!"
)

git add -A
git commit -m "Nova v1.0.0" >nul 2>nul
git tag -a v1.0.0 -m "First version" >nul 2>nul

git remote get-url origin >nul 2>nul
if not errorlevel 1 goto push

where gh >nul 2>nul
if not errorlevel 1 (
  gh auth status >nul 2>nul || gh auth login
  gh repo create nova-agent --private --source . --remote origin
  goto push
)

echo.
echo  1. Open https://github.com/new
echo  2. Repository name: e.g. Nova   -   choose PRIVATE
echo  3. Do NOT tick "Add a README"  -  click Create repository
echo  4. Copy the HTTPS address it shows (ends in .git)
echo.
start https://github.com/new
set /p URL=Paste the address here: 
git remote add origin !URL!

:push
echo Uploading (a GitHub sign-in window may pop up the first time)...
git push -u origin main
if errorlevel 1 (
  echo The GitHub repo already has files ^(e.g. a README^) - combining them with Nova, keeping Nova's versions...
  git pull origin main --allow-unrelated-histories --no-edit -X ours
  git push -u origin main
)
git push origin --tags
echo.
echo Done. Your code and version history are on GitHub (secrets and personal data are NOT uploaded).
pause
