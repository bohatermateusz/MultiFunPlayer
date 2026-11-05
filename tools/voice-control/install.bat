@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
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
python -m pip install --user -r requirements.txt
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )
python -c "import websocket" >nul 2>nul
if errorlevel 1 ( echo websocket-client missing: the live mode ^(F11^) will not be available. & echo Run: python -m pip install --user websocket-client )

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

if "%ELEVENLABS_API_KEY%"=="" (
  echo.
  echo Paste your ElevenLabs API key ^(her voice; without it the OpenAI voice is used^) and press Enter, or just Enter to skip.
  set /p EKEY=ElevenLabs key: 
  if not "!EKEY!"=="" (
    setx ELEVENLABS_API_KEY "!EKEY!" >nul
    set "ELEVENLABS_API_KEY=!EKEY!"
    echo Key saved. Open a NEW terminal for it to be visible there.
  )
) else (
  echo ELEVENLABS_API_KEY already set.
)

echo.
echo Self-check ^(no microphone, no network^):
python voice_control.py text "kochanie spuść się na mnie" --dry-run --no-log
if errorlevel 1 ( echo Self-check failed. & pause & exit /b 1 )

echo.
echo Done. Next:
echo  1. MultiFunPlayer ^> Buttplug.io output ^> switch on "Remote commands" ^(port 23555^).
echo     Buttplug server may run on another machine ^(e.g. Linux^): use ws://IP:12345 in MFP.
echo  2. Set your headset microphone as the default recording device in Windows.
echo  3. run.bat           ^(dry run: say "kochanie, ..." - shows what it would do; her voice is chosen automatically^)
echo  4. run.bat live      ^(for real, conversation without pauses; F9 voice, F10 language, F11 conversation mode^)
pause
