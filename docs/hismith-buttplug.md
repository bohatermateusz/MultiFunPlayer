# Buttplug.io output with the HISMITH S2 fork

The Buttplug.io output target now shows each actuator's feature description next to its index
(for example `3 - Position (move to depth now)`), both in the actuator picker and in the list of
configured mappings. Without it, devices with several actuators of the same type (such as the four
`Position` actuators of the HISMITH S2 in the Buttplug fork) cannot be told apart.

Saved mappings gain an `ActuatorDescription` field; older settings without it keep working and
just show no description.

Not compiled or run: this change was written without a .NET SDK. Build it on Windows first.

## Quick controls (HISMITH S2)

When a connected Buttplug device has the "Run mode" and/or "Preset mode" Position actuators (the
HISMITH S2 in the Buttplug fork), the expander of the Buttplug.io output shows a **HISMITH S2** row:

* **Auto / Manual switch**: Auto = the machine strokes on its own (speed and preset modes),
  Manual = it follows the position axis (`L0` -> `Position/3` "Position (move to depth now)").
* **Preset mode**: the S2's 8 built-in modes (0 = none). Only meaningful in auto mode.

Both controls are also registered as shortcut actions, so they can be bound to a key, gamepad
button or MIDI control: `<target>::Hismith::ManualMode::Set` (bool) and
`<target>::Hismith::PresetMode::Set` (0-8).

The controls send a value when changed; the machine's current state is not read back, so after a
reconnect the row may not match the machine until you touch it again.

## Lube Launcher (PiuPiu)

With the Buttplug fork, the Hismith Lube Launcher shows up as a `Position/0` actuator described
"Spray (0 = off, any value above 0 = squirt)". (The `Spray` actuator type is not offered to
spec v3 clients such as this one, because Buttplug.Net rejects a device that has an unknown
actuator type.) When it is connected, the Buttplug.io output shows a **LUBE LAUNCHER** row:

* **Spray**: one squirt.
* **Auto**: squirt after a random number of seconds between the min and max you enter (the
  official app's random timer).

Shortcut actions: `<target>::Hismith::LubeLauncher::Fire` and
`<target>::Hismith::LubeLauncher::AutoSpray::Set` (bool).

You can also map any axis to `Position/0`; every value above 0 squirts, at most once per 100 ms
(enforced by the Buttplug fork), so prefer the buttons or shortcuts.
