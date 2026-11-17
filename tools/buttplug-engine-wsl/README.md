# Buttplug fork engine on a dedicated USB Bluetooth adapter (WSL)

The toys (HISMITH S2, two Lube Launchers "PiuPiu", Lovense Lush) connect through their own USB
Bluetooth dongle, a **Realtek RTL8761BU** (`0bda:a725`). Keyboard, mouse, headphones and the gamepad
stay on the other dongle (BARROT, `33fa:0010`) in Windows.

Windows runs only one Bluetooth adapter at a time ("Only one active Bluetooth adapter is supported at
a time", BTHUSB event 6), so the Realtek is handed to WSL with
[usbipd-win](https://github.com/dorssel/usbipd-win) and the engine from the Buttplug fork
(`buttplugFork`, crate `intiface_engine`) runs there on BlueZ. MultiFunPlayer connects as before to
`127.0.0.1:12345` (WSL forwards localhost).

On the BARROT adapter (Windows btleplug) the S2 and the Lube Launchers kept failing with
`btleplug error: NotConnected`; on the Realtek in WSL they all connect at once.

## Every start (after a reboot)

Run `start-realtek-engine.bat` and keep its window open, then start MultiFunPlayer. Do not start
Intiface Central: it is the stock engine (no S2 / Lube Launcher fork support) and takes port 12345.

* `start-realtek-engine.bat` (Windows): `usbipd attach --wsl --hardware-id 0bda:a725`, then runs
  `start.sh` as root in WSL.
* `start.sh` (WSL, root): loads the firmware, `btusb`, `bluetoothd`, powers the adapter on and runs
  the engine on port 12345. Expects the engine at `/home/lightsaber/buttplug-engine/intiface-engine`.

## One-time setup (done on 2026-11-17)

1. `winget install dorssel.usbipd-win`, then as administrator: `usbipd bind --busid <busid of 0bda:a725>`
   (persists over reboots; `usbipd list` shows the busid).
2. In WSL as root (`wsl.exe -d Ubuntu -u root`): `apt install bluez libdbus-1-dev pkg-config libudev-dev
   linux-firmware zstd cmake build-essential`.
3. Firmware: the WSL kernel cannot read `.zst` firmware, and it does not see the distribution's
   `/lib/firmware` at all. `start.sh` copies the decompressed `rtl_bt/rtl8761bu_fw.bin` and
   `rtl8761bu_config.bin` to `/mnt/wsl/fw` and points `/sys/module/firmware_class/parameters/path`
   there. Decompress once: `zstd -d /lib/firmware/rtl_bt/rtl8761bu_*.bin.zst`.
4. Engine: copy `buttplugFork` into the WSL file system, `cargo build --release -p intiface-engine`,
   copy `target/release/intiface-engine` to `~/buttplug-engine/`.

## Device indices and roles

The engine keeps `~/buttplug-engine/user-device-config.json`, so the indices stay the same across
restarts and the MultiFunPlayer map rows and device roles (bound by name + index) keep working:

| Device | Address | Index | Role in MultiFunPlayer |
|---|---|---|---|
| Lube Launcher (old PiuPiu) | `5A:48:69:61:BB:A8` | 0 | Front (mouth / face) |
| Lube Launcher (new PiuPiu) | `5A:48:69:C2:6A:45` | 1 | Rear (inside) |
| HISMITH S2 | `5A:48:68:F9:CC:98` | 2 | Rear |
| Lovense Lush | | 3 | Front |

## Troubleshooting

* `usbipd list`: the Realtek must be "Attached". If it is only "Shared", run the bat again.
* In WSL: `btmgmt info` shows `hci0` powered; `dmesg | grep hci0` shows `RTL: fw version ...`
  (a "firmware file not found" line means the firmware path step did not run).
* Engine log: `~/buttplug-engine/engine.log` (or the bat window).
* Switch off the Hismith phone app before connecting: a BLE device accepts only one connection.
