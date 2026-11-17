@echo off
rem Buttplug fork engine on the Realtek USB Bluetooth adapter (in WSL). MultiFunPlayer: 127.0.0.1:12345
rem Keyboard and mouse stay on the BARROT adapter in Windows.
"C:\Program Files\usbipd-win\usbipd.exe" attach --wsl --hardware-id 0bda:a725
timeout /t 3 /nobreak >nul
wsl.exe -d Ubuntu -u root -- /home/lightsaber/buttplug-engine/start.sh
pause
