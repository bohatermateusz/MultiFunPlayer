@echo off
setlocal
cd /d "%~dp0"
echo === MultiFunPlayer voice control ===
echo Hold F9, speak, release. Close this window to stop.
echo.
python -c "import pynput, sounddevice, numpy" >nul 2>nul
if errorlevel 1 (
  echo Missing packages, installing them for this Python...
  python -m pip install --user -r requirements.txt
  if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )
)
if /i "%~1"=="live" (
  python voice_control.py run
) else (
  echo DRY RUN: only shows what it would do. Start "run.bat live" to send commands for real.
  echo.
  python voice_control.py run --dry-run
)
pause
