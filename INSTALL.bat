@echo off
setlocal
cd /d "%~dp0"
title The Other Timeline - Installer
echo.
echo  The Other Timeline - installer
echo  ==============================
echo  This takes 10-20 minutes. Keep this window open.
echo.

where winget >nul 2>nul
if errorlevel 1 (
  echo  "winget" is missing on this PC.
  echo  Open the Microsoft Store, search for "App Installer", install it,
  echo  then double-click INSTALL.bat again.
  pause
  exit /b 1
)

echo  Step 1 of 4: installing Python, Node.js, FFmpeg and Chrome...
echo  If one says "already installed", that is fine.
call :get Python.Python.3.12
call :get OpenJS.NodeJS.LTS
call :get Gyan.FFmpeg
call :get Google.Chrome

rem New tools are not on PATH in this window yet, so find them directly.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
rem Last try: the Python launcher knows every 3.12 install, wherever it is.
if not defined PY for /f "delims=" %%i in ('py -3.12 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY (
  echo.
  echo  ERROR: Python 3.12 was not found after installing it.
  echo  Restart the PC and double-click INSTALL.bat again.
  pause
  exit /b 1
)
set "PATH=%ProgramFiles%\nodejs;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

echo.
echo  Step 2 of 4: preparing the app...
if not exist ".venv\Scripts\python.exe" "%PY%" -m venv ".venv"
if not exist ".venv\Scripts\python.exe" (
  echo  ERROR: could not create the app's Python folder ^(.venv^).
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "app\requirements.txt"
if errorlevel 1 (
  echo.
  echo  ERROR: downloading the app's parts failed. Check the internet
  echo  connection, then double-click INSTALL.bat again.
  pause
  exit /b 1
)

echo.
echo  Step 3 of 4: downloading the speech model and render browser...
".venv\Scripts\python.exe" "app\ot.py" --setup
if errorlevel 1 (
  echo.
  echo  ERROR: setup did not finish. Check the internet connection,
  echo  then double-click INSTALL.bat again.
  pause
  exit /b 1
)

echo.
echo  Step 4 of 4: optional PC tune-up.
choice /c YN /m "Optimize this PC for video building now? (recommended)"
if errorlevel 2 goto start
echo  Windows will ask for permission. Click Yes.
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0optimize_pc.ps1\"'"
if errorlevel 1 echo  The tune-up was skipped. You can run optimize_pc.ps1 later.

:start
start "" ".venv\Scripts\pythonw.exe" "app\ot.py"
echo.
echo  Done! The dashboard opened in your browser.
echo  Next time, use the 'The Other Timeline' icon on your desktop.
pause
exit /b 0

:get
winget install -e --id %1 --silent --accept-package-agreements --accept-source-agreements
exit /b 0
