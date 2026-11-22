@echo off
setlocal EnableExtensions
rem Gets Live Chat Overlay ready. Safe to run again: anything already installed is skipped.
rem   Everyone needs PySide6 (it draws the overlay window).
rem   TikTok chat also needs TikTokLive.
rem Skip the question by passing twitch, tiktok or both as the first argument.
rem "auto" (used by start.bat) installs PySide6 and asks nothing.
cd /d "%~dp0."
title Live Chat Overlay - setup

echo Live Chat Overlay - setup
echo.

rem Run Python to find it, not just "where": Windows ships a "python" stand-in
rem that only opens the Microsoft Store when Python isn't installed.
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY goto :no_python

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :old_python

echo Python is ready.
echo.

%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo Setting up pip...
  %PY% -m ensurepip --upgrade >nul 2>&1
  %PY% -m pip --version >nul 2>&1
  if errorlevel 1 goto :no_pip
)

set "PICK="
if /i "%~1"=="twitch" set "PICK=1"
if /i "%~1"=="tiktok" set "PICK=2"
if /i "%~1"=="both" set "PICK=3"
if /i "%~1"=="auto" set "PICK=1"
if defined PICK goto :picked

echo Which chat do you use?
echo.
echo   1  Twitch only
echo   2  TikTok only
echo   3  Both Twitch and TikTok
echo.
choice /c 123 /n /m "Press 1, 2 or 3: "
set "PICK=%errorlevel%"
echo.

:picked
if not "%PICK%"=="1" if not "%PICK%"=="2" if not "%PICK%"=="3" (
  echo No choice was made, so nothing was installed.
  goto :fail
)

%PY% -c "import PySide6.QtWidgets" >nul 2>&1
if errorlevel 1 (
  echo Installing PySide6, which draws the overlay window.
  echo It is about 80 MB, so this can take a few minutes...
  echo.
  %PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :install_failed
  echo.
) else (
  echo PySide6 is already installed.
)

if "%PICK%"=="1" (
  echo Twitch chat needs nothing else.
  echo If you start using TikTok later, run this file again and pick 2 or 3.
  goto :done
)

%PY% -c "import TikTokLive" >nul 2>&1
if not errorlevel 1 (
  echo TikTokLive is already installed.
  goto :done
)
echo Installing TikTokLive for TikTok chat...
echo.
%PY% -m pip install --disable-pip-version-check -r requirements-tiktok.txt
if errorlevel 1 goto :install_failed

:done
echo.
echo All set. Double-click start.bat to start the overlay.
echo.
if /i "%~1"=="auto" exit /b 0
pause
exit /b 0

:no_python
echo Python was not found.
echo.
echo 1. Go to https://www.python.org/downloads/ and install Python.
echo 2. If the installer shows "Add python.exe to PATH", tick it.
echo 3. Run this file again.
goto :fail

:old_python
echo Your Python is too old. Live Chat Overlay needs Python 3.10 or newer.
echo Install the latest one from https://www.python.org/downloads/ and run this file again.
goto :fail

:no_pip
echo Couldn't set up pip, the tool that installs Python packages.
echo Install Python again from https://www.python.org/downloads/ and run this file again.
goto :fail

:install_failed
echo.
echo Install failed. Check your internet connection and run this file again.
goto :fail

:fail
echo.
pause
exit /b 1
