@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo === MultiFunPlayer voice control: installation (Windows) ===

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH"^) or run: winget install Python.Python.3.12
  echo Then run this file again.
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  pause & exit /b 1
)

echo Installing packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )

if "%OPENAI_API_KEY%"=="" (
  echo.
  echo Paste your OpenAI API key ^(starts with sk-^) and press Enter, or just Enter to skip.
  echo It is stored only in your Windows user environment, not in any file here.
  set /p KEY=Key: 
  if not "!KEY!"=="" (
    setx OPENAI_API_KEY "!KEY!" >nul
    set "OPENAI_API_KEY=!KEY!"
    echo Key saved. Open a NEW terminal for it to be visible there.
  )
) else (
  echo OPENAI_API_KEY already set.
)

echo.
echo Self-check ^(no microphone, no network^):
python voice_control.py text "spuść się na mnie" --dry-run --no-log
if errorlevel 1 ( echo Self-check failed. & pause & exit /b 1 )

echo.
echo Done. Next:
echo  1. MultiFunPlayer ^> Buttplug.io output ^> switch on "Remote commands" ^(port 23555^).
echo     Buttplug server may run on another machine ^(e.g. Linux^): use ws://IP:12345 in MFP.
echo  2. Set your headset microphone as the default recording device in Windows.
echo  3. python voice_control.py run --dry-run    ^(hold F9 and speak^)
echo  4. python voice_control.py run
pause
