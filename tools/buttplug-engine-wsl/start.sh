#!/bin/bash
# Buttplug fork engine on the Realtek USB Bluetooth adapter (passed to WSL with usbipd). Run as root.
mkdir -p /mnt/wsl/fw/rtl_bt
cp -n /lib/firmware/rtl_bt/rtl8761bu_fw.bin /lib/firmware/rtl_bt/rtl8761bu_config.bin /mnt/wsl/fw/rtl_bt/
echo -n /mnt/wsl/fw > /sys/module/firmware_class/parameters/path
modprobe -r btusb 2>/dev/null; modprobe btusb
for i in $(seq 1 20); do [ -e /sys/class/bluetooth/hci0 ] && break; sleep 0.5; done
pgrep -x dbus-daemon >/dev/null || service dbus start >/dev/null 2>&1
pkill bluetoothd; sleep 0.5
nohup /usr/libexec/bluetooth/bluetoothd -n >/var/log/bluetoothd.log 2>&1 &
sleep 3
bluetoothctl power on
pkill -f "buttplug-engine/intiface-engine"; sleep 0.5
cd /home/lightsaber/buttplug-engine
exec ./intiface-engine --websocket-port 12345 --websocket-use-all-interfaces --use-bluetooth-le \
  --server-name ButtplugFork-Realtek --user-device-config-file /home/lightsaber/buttplug-engine/user-device-config.json --log info
