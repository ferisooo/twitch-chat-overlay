@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Starts Live Chat Overlay. Just double-click it.
rem If this folder still has the old version 1, it first updates it to
rem version 2 and installs what's missing. After that it only starts the overlay.
rem To get the newest version later, use update.bat.
set "BRANCH=main"
title Live Chat Overlay

rem Switching versions can replace this very file while it runs, which
rem confuses Windows. So run a copy from the temp folder instead.
if /i "%~1"=="--run" goto :start
copy /y "%~f0" "%TEMP%\live-chat-overlay-start.bat" >nul 2>&1
if errorlevel 1 goto :start
"%TEMP%\live-chat-overlay-start.bat" --run "%~dp0."

:start
if /i "%~1"=="--run" (cd /d "%~2") else (cd /d "%~dp0.")

rem Already on version 2? Then just start it.
set "CUR="
for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CUR=%%B"
if exist "overlay\app.py" if /i "!CUR!"=="%BRANCH%" goto :launch
rem A downloaded copy of version 2 (without git) can start right away too.
if not defined CUR if exist "overlay\app.py" goto :launch

where git >nul 2>&1
if errorlevel 1 goto :no_git
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :not_git_folder

echo Updating this folder to Live Chat Overlay version 2...
echo.
git fetch origin %BRANCH%
if errorlevel 1 goto :fail

rem Close the old overlay if it is open.
tasklist /FI "WINDOWTITLE eq Live Chat Overlay" /NH 2>nul | find /I "python" >nul
if not errorlevel 1 (
  taskkill /F /T /FI "WINDOWTITLE eq Live Chat Overlay" >nul 2>&1
  timeout /t 2 >nul
)

rem Python cache files are rebuilt by themselves; never let them block the switch.
git checkout -- __pycache__ >nul 2>&1
rem If this file was dropped in by hand, version 2 brings its own copy.
git ls-files --error-unmatch start.bat >nul 2>&1
if errorlevel 1 if exist "start.bat" del /q "start.bat"

set "STASHED=0"
set "DIRTY="
for /f "delims=" %%I in ('git status --porcelain --untracked-files=no') do set "DIRTY=1"
if defined DIRTY (
  git stash push -m "start-bat"
  if errorlevel 1 goto :fail
  set "STASHED=1"
)

git show-ref --verify --quiet refs/heads/%BRANCH%
if errorlevel 1 (
  git checkout -b %BRANCH% --track origin/%BRANCH%
) else (
  git checkout %BRANCH%
  if not errorlevel 1 git pull --ff-only origin %BRANCH%
)
if errorlevel 1 goto :restore

if "!STASHED!"=="1" (
  git stash pop
  if errorlevel 1 echo Your own edits clashed with version 2. They are saved in git stash under "start-bat".
)

echo.
echo Done! This folder now has version 2.
echo.

:launch
if not exist "run.bat" (
  echo run.bat is missing, so the overlay can't start.
  pause
  exit /b 1
)
call "run.bat"
exit /b %errorlevel%

:restore
if "!STASHED!"=="1" git stash pop

:fail
echo.
echo Couldn't update to version 2. Your old version is still here and works as before.
pause
exit /b 1

:no_git
echo This folder has the old version, and Git isn't installed to switch it.
echo Install Git for Windows from https://git-scm.com/download/win and run this again.
pause
exit /b 1

:not_git_folder
echo This folder isn't a git checkout, so it can't switch versions by itself:
echo   %CD%
echo Download version 2 from GitHub instead.
pause
exit /b 1
