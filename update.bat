@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Updates this folder to the newest version.
rem Double-click it any time. It works no matter which folder it was started from.
set "BRANCH=main"

rem Updating may replace this very file while it runs, which confuses Windows.
rem So run a copy from the temp folder instead.
if /i "%~1"=="--run" goto :start
copy /y "%~f0" "%TEMP%\live-chat-overlay-update.bat" >nul 2>&1
if errorlevel 1 goto :start
"%TEMP%\live-chat-overlay-update.bat" --run "%~dp0."
exit /b

:start
if /i "%~1"=="--run" (cd /d "%~2") else (cd /d "%~dp0.")

rem A folder made with git is updated with git. A downloaded zip copy
rem (or a PC without Git) gets the newest zip from GitHub instead.
set "ZIP_URL=https://github.com/ferisooo/twitch-chat-overlay/archive/refs/heads/%BRANCH%.zip"
set "MODE=git"
where git >nul 2>&1
if errorlevel 1 set "MODE=zip"
if not exist ".git" set "MODE=zip"

echo Updating:
echo   %CD%
echo from the %BRANCH% branch
echo.

if "!MODE!"=="zip" goto :zip_update

git fetch origin %BRANCH%
if errorlevel 1 goto :fail

call :close_overlay

rem Python cache files are rebuilt by themselves; never let them block an update.
git checkout -- __pycache__ >nul 2>&1

set "STASHED=0"
set "DIRTY="
for /f "delims=" %%I in ('git status --porcelain --untracked-files=no') do set "DIRTY=1"
if defined DIRTY (
  git stash push -m "update-bat"
  if errorlevel 1 goto :fail
  set "STASHED=1"
)

git show-ref --verify --quiet refs/heads/%BRANCH%
if errorlevel 1 (
  git checkout -b %BRANCH% --track origin/%BRANCH%
) else (
  git checkout %BRANCH%
)
if errorlevel 1 goto :restore

git pull --ff-only origin %BRANCH%
if errorlevel 1 goto :restore

if "!STASHED!"=="1" (
  git stash pop
  if errorlevel 1 (
    echo.
    echo The update worked, but your own edits clashed with it.
    echo They are saved in git stash under "update-bat".
    if "!WAS_RUNNING!"=="1" if exist "start.bat" start "" "start.bat"
    pause
    exit /b 1
  )
)

:updated
echo.
echo Updated to the newest version.
if "!WAS_RUNNING!"=="1" if exist "start.bat" start "" "start.bat"
timeout /t 3 >nul
exit /b 0

:zip_update
set "ZIP_FILE=%TEMP%\live-chat-overlay-update.zip"
set "ZIP_DIR=%TEMP%\live-chat-overlay-update"
if exist "%ZIP_DIR%" rd /s /q "%ZIP_DIR%"
echo Downloading the newest version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; Invoke-WebRequest -UseBasicParsing -Uri $env:ZIP_URL -OutFile $env:ZIP_FILE; Expand-Archive -LiteralPath $env:ZIP_FILE -DestinationPath $env:ZIP_DIR -Force"
if errorlevel 1 goto :zip_fail

rem The zip holds one folder with everything inside. Make sure it's really the overlay.
set "SRC="
for /d %%D in ("%ZIP_DIR%\*") do set "SRC=%%D"
if not defined SRC goto :zip_fail
if not exist "!SRC!\overlay\app.py" goto :zip_fail

call :close_overlay

rem Copy the new files over the old ones. Files only you have are left alone.
set "DEST=%CD%"
if "!DEST:~-1!"=="\" set "DEST=!DEST!."
robocopy "!SRC!" "!DEST!" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :zip_copy_fail
rem run.bat was merged into start.bat; remove the old copy.
if exist "run.bat" del /q "run.bat"
rd /s /q "%ZIP_DIR%" >nul 2>&1
del /q "%ZIP_FILE%" >nul 2>&1
goto :updated

:zip_fail
rd /s /q "%ZIP_DIR%" >nul 2>&1
del /q "%ZIP_FILE%" >nul 2>&1
echo.
echo Couldn't download the newest version from GitHub.
echo Check your internet connection and try again.
goto :fail

:zip_copy_fail
rd /s /q "%ZIP_DIR%" >nul 2>&1
del /q "%ZIP_FILE%" >nul 2>&1
echo.
echo Some files couldn't be replaced. Close anything that is using this folder and try again.
goto :restore

:restore
if "!STASHED!"=="1" git stash pop
if "!WAS_RUNNING!"=="1" if exist "start.bat" start "" "start.bat"

:fail
echo.
echo Update failed.
pause
exit /b 1

:close_overlay
rem Close the overlay if it is running (Windows locks files it is using).
set "WAS_RUNNING=0"
set "PIDFILE=%APPDATA%\LiveChatOverlay\overlay.pid"
if exist "%PIDFILE%" (
  set /p OPID=<"%PIDFILE%"
  tasklist /FI "PID eq !OPID!" /NH 2>nul | find /I "python" >nul
  if not errorlevel 1 (
    set "WAS_RUNNING=1"
    taskkill /F /T /PID !OPID! >nul 2>&1
  )
)
rem Also look for its window by title, in case the pid file is missing.
tasklist /FI "WINDOWTITLE eq Live Chat Overlay" /NH 2>nul | find /I "python" >nul
if not errorlevel 1 (
  set "WAS_RUNNING=1"
  taskkill /F /T /FI "WINDOWTITLE eq Live Chat Overlay" >nul 2>&1
)
if "!WAS_RUNNING!"=="1" timeout /t 2 >nul
exit /b 0
