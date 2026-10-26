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

## Script loop (hold a movement you like)

While the video keeps playing, the scripts replay the **last N seconds** (default 5) over and over.
Switching it off makes the scripts continue from wherever the video is *now*. It never seeks the
player, unlike the existing "Media::Loop" actions, which loop the video together with the script.

Actions (Settings -> Shortcuts -> add a shortcut, pick a gesture such as a key, gamepad or MIDI
button, then the action):

* `Script::Loop::Toggle`: first press starts the loop from the current position, second press ends it.
* `Script::Loop::Start` / `Script::Loop::Clear`: separate on/off.
* `Script::Loop::Duration::Set`: length in seconds (0.5-600, saved with the settings).

Property for other shortcuts and displays: `Script::Loop::IsActive`, `Script::Loop::Duration`.

Notes:

* All axes loop together, so a multi-axis script stays in sync, **except axes with "Ignore script loop"
  switched on** (axis settings, "Bypass" section). That is on by default for axis V1 (`.lube` / `.pump`
  scripts), so the Lube Launcher keeps following the film and its script is never replayed. Turn it on
  for any other axis that drives something you do not want looped. Axes without a script are not affected.
* The loop keeps running while the video is paused (the scripts replay on a paused film). It ends by
  itself when the media changes.
* The end of the loop jumps back to its start without smoothing, so a script whose value at the loop
  end differs a lot from the value at the loop start gives a sudden move. Use the axis speed limit.
* If the key is also the play/pause key of your video player (space usually is), the player will
  pause too when the key reaches it; use another key or a controller button.
* Output targets that are set to "polled update" may not follow the loop; the Buttplug.io target
  uses "fixed update" by default and does.

## One key for script loop, mode sequence and funscripts (HISMITH S2)

Two state-aware actions on the Buttplug.io output (`<target>` is its name, e.g. `Buttplug.io/0`):

| State | `<target>::Hismith::Key::SinglePress` | `<target>::Hismith::Key::DoublePress` |
|---|---|---|
| Funscripts playing | start/stop the script loop (see above) | start the mode sequence |
| Mode sequence running | **hold** the current mode (its countdown stops) | back to the funscripts |
| Mode sequence held | **release**: the queue goes on | back to the funscripts |

Bind them in Settings -> Shortcuts: add a shortcut of type **Button Click** with your key (e.g. Space),
**Click count = 1** and action `...::Key::SinglePress`; add a second one with **Click count = 2** and
action `...::Key::DoublePress`. A single click therefore fires about 200 ms after you release the key
(the shortcut's "maximum click interval"), because MultiFunPlayer has to wait for a possible second click.

The mode sequence plays the machine's built-in modes 1-8 in turn, **2 minutes each** (changeable in
the HISMITH S2 row, 5-3600 s), and wraps around after mode 8:

1. Start: the script loop is cleared, the machine goes to auto mode and mode 1 starts.
2. While it runs, the funscript output to that machine is paused (other devices, e.g. the Lube
   Launcher, keep following their scripts). The film and the scripts' clock keep running.
3. Back to the funscripts: manual mode is sent first, then the script values are sent again, so the
   machine follows the film's current position.

The same things are available as buttons in the HISMITH S2 row (Sequence switch, seconds per mode,
Hold switch, status text) and as actions `...::Hismith::Sequence::Start`, `::Stop`,
`::Hold::Toggle`. If the key is also play/pause in the player, the player will react too.

The mode sequence only touches the HISMITH S2 itself (the device with the "Preset mode" actuator):
the Lube Launcher is neither paused during the sequence nor sent anything when the sequence ends.

## Remote commands and voice control

The Buttplug.io output has a **Remote commands** switch (off by default): a UDP listener on
127.0.0.1 (port 23555) that runs one action per message, `{"action": "<name>", "args": [<value>]}`.
Only the actions `<target>::Hismith::*`, `Script::Loop::*` and `Media::PlayPause::*` are accepted.
New S2 actions for it: `...::Hismith::Sequence::Hold::Set` (bool) and `...::Hismith::Speed::Set` /
`::Speed::Offset` (auto-mode speed, 0-100).

`tools/voice-control` is a Polish push-to-talk voice control built on it; see its README.

## Known behaviour and fixes (verification pass)

* **Repeated commands.** The Buttplug server drops a command identical to the previous one for the
  same feature. The Spray button, auto-spray and the voice command therefore send `1` then `0`
  (150 ms later); a preset mode is sent as `0` then `n`. Without this a second squirt or a
  re-selected mode never reached the machine.
* **Auto-spray switch** used to stop the UDP listener and the mode sequence (a misplaced edit), and
  the UDP port stayed taken after removing the Buttplug.io output. Both fixed.
* **Remote commands at start-up.** Settings load before the actions are registered, so a command
  arriving in the first moments after start is rejected with a warning in the log; later ones work.
* **Sequence restart.** A double press right after leaving the sequence waits for the manual-mode
  command (300 ms) before starting again.

## Checklist on Windows with the machine

1. Engine from the Buttplug fork: `intiface_engine.exe --websocket-port 12345 --use-bluetooth-le --log info`;
   after switching the S2 on, the log shows `Hismith Device Identifier: XXXX`. If it is not `1101`,
   add that code to the servo entry in `hismith-mini.yml` (one line).
2. **Without the machine** (fake S2 from the Buttplug fork, `tools/e2e/README.md`): MultiFunPlayer
   shows "Hismith Servo Sex Machine", the HISMITH S2 row appears, and the fake device prints bytes
   for every slider, key and voice command.
3. With the machine, from the engine's own test page or any v3 client: `Position/5 = 1` (manual),
   `Position/3 = 0.3` and `0.7` (does `CC 0A` move it; does the position scale to the stroke
   window), `Position/5 = 0`, `Oscillate/0 = 0.2`, `Position/6 = 0.125` (mode 1).
4. MultiFunPlayer: build from a clean clone; two Button Click shortcuts (click count 1 and 2) on a
   gamepad button; the four transitions of the table above; status text `mode N/8, m:ss (held)`;
   toggle Auto-spray *while* the sequence runs; remove and re-add the Buttplug.io output with
   Remote commands on; click Spray twice (two squirts).
5. `python voice_control.py text "tryb ręczny"` with Remote commands on: the Auto/Manual switch in
   MultiFunPlayer flips.
6. Lube Launcher: Spray = one squirt, second click = second squirt, Stop in MultiFunPlayer = none.
7. Voice: `run --dry-run`, 10x "spuść się na mnie" and 10 non-commands; check `voice_log.jsonl`;
   then `run`.
8. HereSphere: film + `Film.funscript`, `L0 -> Position/3`, manual mode, small stroke window, axis
   speed limit.
