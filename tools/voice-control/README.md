# Polish voice control and keyboard shortcuts for MultiFunPlayer (HISMITH S2, Lube Launcher)

Say the wake word **"kochanie"** and a command ("kochanie, szybciej"). The microphone listens all the
time, but only short pieces of speech are sent to a speech-to-text service in the cloud, and a piece
is only a command if it **starts with the wake word** and is close to a phrase in `commands.json`.
Everything else (film audio, talk, breathing) is ignored. The same actions are on **keyboard
shortcuts** (global: they work while HereSphere has the focus) and on **buttons in MultiFunPlayer**.
Nothing is stored except a text log (`voice_log.jsonl`: transcript, matched command, score).

**The audio of what you say leaves your computer** (to the service in `commands.json`, OpenAI by
default), but only the pieces of speech the detector cut out, not a continuous stream. Use a service
whose data terms you accept. The cost grows with the amount of speech near the microphone: mute
listening (key `Pause`) when you talk to someone else.

## Setup (Windows)

**Quick install:** double-click `install.bat` (installs the packages, stores the key, runs a self-check),
then do steps 1 and 3 below. Start it with `run.bat` (dry run, only shows what it would do) or
`run.bat live` (sends commands). The Buttplug server may run on another machine (e.g. Linux,
`--websocket-use-all-interfaces`); MultiFunPlayer and this tool must run on the same Windows PC (UDP to 127.0.0.1).

1. MultiFunPlayer fork: in the Buttplug.io output, switch on **Remote commands** (127.0.0.1, port 23555).
   It only listens on this computer and only accepts a few action families (`<target>::Hismith::*`,
   `Script::Loop::*`, `Media::PlayPause::*`).
2. `pip install -r requirements.txt` (the tests and `text` mode need nothing).
3. Set the key: `set OPENAI_API_KEY=...` (any OpenAI-compatible `/audio/transcriptions` endpoint works;
   change `stt.base_url` and `stt.model` in `commands.json`). Set your headset microphone as the
   default recording device in Windows; a microphone close to the mouth matters more than anything else.
4. Tune the microphone once: `python voice_control.py mic` shows the level and the detection threshold
   (it follows the background noise). If speech is not detected, or the film triggers it, set
   `listen.threshold_db` in `commands.json` (e.g. -40).

## Use

```
python voice_control.py run --dry-run     # first: only shows what it would do
python voice_control.py run               # continuous listening + keyboard shortcuts
python voice_control.py keys              # the keyboard shortcuts
python voice_control.py text "kochanie szybciej"   # test the matching without audio or cloud
python voice_control.py wav nagranie.wav  # cloud transcription of a recording + matching
python -m unittest discover -s tests      # tests (no network, no microphone)
```

`"listen": {"mode": "ptt"}` in `commands.json` brings back push-to-talk (hold F9, no wake word needed
when `"wake": {"required": false}`). A short tone tells the result: high = command run, low = ignored.

## Commands (all start with "kochanie"; edit `commands.json`)

| Effect | Say | Key |
|---|---|---|
| faster / slower (clearly: +/- 20 points) | szybciej, prędzej, przyspiesz, mocniej, dawaj, więcej / wolniej, zwolnij, spokojniej, delikatniej, mniej, powoli | Num + / Num - |
| deeper / shallower (+/- 15 points) | głębiej, głęboko, do końca, w głąb / płycej, płytko, nie tak głęboko, wyjdź trochę | Num * / Num / |
| built-in modes 1-8 | tryb jeden ... tryb osiem (also "tryb pierwszy", "tryb 3", "program trzy", "trójka") | Num 1 ... Num 8 |
| mode A: fastest, full depth, smallest stroke | tryb a, na maksa, najszybciej, maksymalnie | Num 9 |
| mode B: slowest, full depth, smallest stroke | tryb b, najwolniej, na minimum | Num 0 |
| back to the funscript and no boost | normalnie, zresetuj, bez zmian | Num . |
| back to the funscript (keep boost) | wróć do filmu, wróć do skryptu, wyłącz tryb | (button "Script") |
| Lube Launcher | spuść się na mnie, strzel na mnie, spuść się, dojdź na mnie | F8 |
| Lube Launcher several times | spuść się na mnie kilka razy / dwa razy / trzy razy | |
| play / pause the video | graj, wznów / stop, pauza | |
| script loop on / off | zapętl to, powtórz to / koniec pętli, wyłącz pętlę | |
| mode sequence (2 min per mode) | włącz sekwencję, wstrzymaj tryb, wznów sekwencję, koniec sekwencji | |
| listening on / off | | Pause |

The keys are global and **NumLock must be on**. They are observed, not swallowed: if HereSphere
uses the same key it reacts too. Change them in `commands.json` (`keys`, written as `num1`,
`num_add`, `f8`, `pause`...).

**Boost vs. mode.** With a funscript running, faster / slower / deeper / shallower are **added to the
script's values** (the script keeps playing, the machine is just pushed further); "normalnie" removes
the boost. A built-in mode (1-8, A, B) **replaces the script for the machine** (the Lube Launcher is
not affected); in a mode the same commands set absolute speed / depth. Back to the script with
"wróć do filmu" or "normalnie".

## How mistakes are kept out

* **Wake word first**: speech without "kochanie" at the start is thrown away, so film dialogue and
  talking cannot trigger anything. The word is also given to the speech service as a hint.
* **Speech detector**: only speech clearly above the room noise (default +12 dB) is cut out; clicks
  and coughs shorter than half a second are dropped.
* **Closed list**: after the wake word, every word of a configured phrase has to be in the transcript
  (small spelling differences allowed), with at most one extra word; one-word commands must be said
  alone; long sentences are ignored. Ambiguous matches are dropped (a test checks that every
  configured phrase matches only its own command).
* **Per-command threshold and cooldown** (`commands.json`): the Lube Launcher needs a 0.90 match and
  has a 10 s cooldown. Set `"confirm": true` on a command to require a spoken "tak" first.
* **Dry run** and a log of everything heard, to tune the phrases before using it for real.

The matcher was tested with dialogue, hallucinations of the speech service ("Dzięki za oglądanie")
and every phrase against every other. How well the **cloud service hears you** (your voice, the
microphone, the noise of the machine) is not covered by this and has to be measured: record a few
phrases and run `wav` on them.

## Hardware checks still open

* After a built-in mode, does a speed command ("szybciej" inside the mode) end the mode? If it does,
  "szybciej" in modes 1-8 leaves the mode. The way back to the script goes through speed 0 first
  for this reason.
* Mode A / B: the app never lets the stroke be narrower than 10 (so "1%" is 10 here); the real
  machine may accept less, and may not move at speed 1 (mode B).
* Does the keyboard hook see the numpad while HereSphere / SteamVR has the focus?
