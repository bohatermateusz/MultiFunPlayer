# Polish voice control for MultiFunPlayer (push-to-talk, cloud speech-to-text)

Hold a key (or a gamepad button), say a command, let go. The recording is sent to a speech-to-text
service in the cloud **only while the button is held**; the text is then matched against a closed
list of phrases and, if it is close enough, a MultiFunPlayer action is run. Nothing is stored except
a text log (`voice_log.jsonl`: transcript, matched command, score). Audio is not saved.

**The audio of what you say leaves your computer** (to the service in `commands.json`, OpenAI by
default). Use a service whose data terms you accept.

## Setup

**Quick install (Windows):** double-click `install.bat` (installs the packages, stores the key, runs a self-check), then do steps 1 and 4 below. Start it with `run.bat` (dry run, only shows what it would do) or `run.bat live` (sends commands).
The Buttplug engine may run on another machine (e.g. Linux, `--websocket-use-all-interfaces`); MultiFunPlayer and this tool must run on the same Windows PC (UDP to 127.0.0.1).

1. MultiFunPlayer fork: in the Buttplug.io output, switch on **Remote commands** (127.0.0.1, port 23555).
   It only listens on this computer and only accepts a few action families (`<target>::Hismith::*`,
   `Script::Loop::*`, `Media::PlayPause::*`).
2. `pip install -r requirements.txt` (the tests and `text` mode need nothing).
3. Set the key: `set OPENAI_API_KEY=...` (any OpenAI-compatible `/audio/transcriptions` endpoint works;
   change `stt.base_url` and `stt.model` in `commands.json`).
4. Check the microphone Windows uses (for a headset such as the Pimax Crystal, set it as the default
   recording device). A microphone close to the mouth matters more than anything else.

## Use

```
python voice_control.py run --dry-run     # first: only shows what it would do
python voice_control.py run               # push-to-talk (default key F9, see "ptt" in commands.json)
python voice_control.py text "spuść się na mnie"   # test the matching without audio or cloud
python voice_control.py wav nagranie.wav  # cloud transcription of a recording + matching
python -m unittest discover -s tests      # tests (no network, no microphone)
```

A short tone tells the result: high = command run, low = ignored, two low = error.

## How mistakes are kept out

* **Push-to-talk**: nothing is listened to or sent unless you hold the button, so film audio and
  your own noises cannot trigger anything.
* **Closed list**: a transcript is only a command if every word of a configured phrase is in it
  (small spelling differences allowed), with at most one extra word; one-word commands must be said
  alone; long sentences are ignored. Ambiguous matches are dropped.
* **Per-command threshold and cooldown** (`commands.json`): the Lube Launcher needs a 0.92 match and
  has a 10 s cooldown. Set `"confirm": true` on a command to require a spoken "tak" first.
* **Dry run** and a log of everything heard, to tune the phrases before using it for real.

Measured on the matcher (not the cloud service): 0 of 39 ordinary Polish sentences and 0 of 20 000
random word combinations triggered the Lube Launcher; 92% of phrases with one dropped character
were still recognised, and none turned into a wrong command. How well the **cloud service hears
you** (your voice, the microphone, the noise of the machine) is not covered by this and has to be
measured: record a few phrases and run `wav` on them.

## Commands (edit `commands.json`)

spuść się na mnie / strzel na mnie (Lube Launcher), stop / pauza / wstrzymaj film, graj / wznów /
kontynuuj, zapętl / zapętl to / powtórz to, koniec pętli / wyłącz pętlę, włącz sekwencję,
wróć do skryptu, wstrzymaj tryb / wznów sekwencję, tryb ręczny / tryb automatyczny,
tryb jeden ... tryb osiem (also "tryb pierwszy", "tryb 3"), szybciej / wolniej (auto mode).
