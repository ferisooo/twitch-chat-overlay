@echo off
setlocal EnableExtensions
cd /d "%~dp0."

rem Find Python. Run it to be sure: Windows ships a "python" stand-in that
rem only opens the Microsoft Store when Python isn't really installed.
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY goto :no_python

rem The overlay window needs PySide6. Install it the first time.
%PY% -c "import PySide6.QtWidgets" >nul 2>&1
if errorlevel 1 (
  echo The overlay needs one more thing installed first.
  echo.
  call "%~dp0install requirements.bat" auto
  %PY% -c "import PySide6.QtWidgets" >nul 2>&1
  if errorlevel 1 exit /b 1
)

rem pythonw starts the overlay without a black console window.
set "PYW="
for /f "delims=" %%P in ('%PY% -c "import os,sys; p=os.path.join(os.path.dirname(sys.executable),'pythonw.exe'); print(p if os.path.exists(p) else '')"') do set "PYW=%%P"
if defined PYW (
  start "" "%PYW%" "%~dp0main.py"
  exit /b 0
)
start "" /min %PY% "%~dp0main.py"
exit /b 0

:no_python
echo Python was not found, so the overlay can't start.
echo Run "install requirements.bat" first. It explains how to get Python.
echo.
pause
exit /b 1
