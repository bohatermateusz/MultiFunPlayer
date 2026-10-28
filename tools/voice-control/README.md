# Polish voice control and keyboard shortcuts for MultiFunPlayer (HISMITH S2, Lube Launcher)

**Ściąga po polsku, co mówić podczas sesji: [`docs/KOMENDY.md`](../../docs/KOMENDY.md).**

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
when `"wake": {"required": false}`). The tool is silent by default; the result is printed in the window. `"sounds": "all"` in `commands.json` brings back the short tones (high = command run, low = ignored, two low = error), `"errors"` keeps only the error tone.

## Commands (all start with "kochanie"; edit `commands.json`)

| Effect | Say | Key |
|---|---|---|
| faster / slower (clearly: +/- 20 points) | szybciej, prędzej, przyspiesz, mocniej, dawaj, więcej / wolniej, zwolnij, spokojniej, delikatniej, mniej, powoli | Num + / Num - |
| deeper / shallower (+/- 15 points) | głębiej, głęboko, do końca, w głąb / płycej, płytko, nie tak głęboko, wyjdź trochę | Num * / Num / |
| built-in modes 1-8 | tryb jeden ... tryb osiem (also "tryb pierwszy", "tryb 3", "program trzy", "trójka") | Num 1 ... Num 8 |
| mode A: fastest, full depth, smallest stroke | tryb a, na maksa, najszybciej, maksymalnie | Num 9 |
| mode B: slowest, full depth, smallest stroke | tryb b, najwolniej, na minimum | Num 0 |
| **surprise**: two separate draws. Machine: 70 % a random mode 1-8 / A / B (never the one that runs), else unchanged. Lube Launcher: nothing / one shot / three shots, 1/3 each. At least one of them always happens | zaskocz mnie, niespodzianka, wybierz za mnie, losowo, zdaj się na los | Insert |
| **again**: the NEXT draw (mode + Lube Launcher, as above) is made and put into effect **120 s after the command**; the current surprise stays until then, shots that have not been fired yet are dropped at once; saying it again restarts the 120 s; choosing a mode / Script / Calm / Stop cancels it; **while mode A runs there is no waiting: the next draw is made at once** | nieee coś innego, zmień, inaczej, jeszcze raz, losuj ponownie, nie to | Del |
| **hold**: the machine keeps exactly what it does now for **5 minutes**; everything else (modes, boosts, surprise, scenes, calm, sequence, keys) is ignored. Only the **Lube Launcher** and the safe word **"koniec" (End)** still work; nothing else ends it early | utrzymaj to, rób tak dalej, tak trzymaj, zostaw tak, nie zmieniaj, tak ma być, **jeszcze, nie przestawaj, właśnie tak** | PgUp |
| back to the funscript and no boost | normalnie, zresetuj, bez zmian | Num . |
| back to the funscript (keep boost) | wróć do filmu, wróć do skryptu, wyłącz tryb | (button "Script") |
| Lube Launcher | spuść się na mnie, strzel na mnie, spuść się, dojdź na mnie | F8 |
| Lube Launcher several times | spuść się na mnie kilka razy / dwa razy / trzy razy | |
| play / pause the video | graj, wznów / pauza, zatrzymaj film ("stop" is calm, not pause) | |
| script loop on / off | zapętl to, powtórz to / koniec pętli, wyłącz pętlę | |
| mode sequence (2 min per mode) | włącz sekwencję, wstrzymaj tryb, wznów sekwencję, koniec sekwencji | |
| calm (slowest, medium depth) / stop everything | dość, wystarczy, starczy / koniec | Home / End |
| listening on / off | | Pause |

The keys are global and **NumLock must be on**. They are observed, not swallowed: if HereSphere
uses the same key it reacts too. Change them in `commands.json` (`keys`, written as `num1`,
`num_add`, `f8`, `pause`...).

**Boost vs. mode.** With a funscript running, faster / slower / deeper / shallower are **added to the
script's values** (the script keeps playing, the machine is just pushed further); "normalnie" removes
the boost. A built-in mode (1-8, A, B) **replaces the script for the machine** (the Lube Launcher is
not affected); in a mode the same commands set absolute speed / depth. Back to the script with
"wróć do filmu" or "normalnie".

## Guessing unclear commands (LLM)

When the strict list finds nothing, a small language model (default `gpt-4.1-mini`, the same
`OPENAI_API_KEY` as the speech service) may guess which of **your configured commands** was meant:

* "trwanie, spuścić się" (the speech service distorted "kochanie") -> Lube Launcher
* "kochanie, mógłbyś przyspieszyć" / "zdecydowanie szybciej" -> faster (a strong word = 2-3 steps)
* "kochanie, daj mi tryb szósty", "kochanie, strzel cztery razy" -> mode 6, four shots

The model is only a **classifier**: it sees the command ids and short descriptions and answers with one
id (or "none") and a confidence; it never produces an action. The tool checks the answer (known id,
"said to me", confidence at least 0.8, Lube Launcher 0.9), applies the usual cool-downs and runs the
configured steps itself. A wrong command is treated as much worse than a missed one, so the model is told
to say "none" when unsure (dialogue, questions, negations like "nie spuszczaj się").

* **When it is called**: only when the strict matcher failed **and** the utterance is at most 14 words
  **and** one of its first two words looks like the wake word (so film dialogue never leaves the computer
  as text). Normal commands never touch the model: they stay offline, free and instant.
* **Cost / privacy**: a few tokens of text per call (fractions of a cent). Only the recognised *text*
  is sent, not audio.
* **No network or key**: the utterance is ignored, nothing else breaks. `--no-llm` (or
  `"llm": {"enabled": false}`) turns it off.
* **Measure it on your voice**: `python voice_control.py eval` runs ~60 utterances
  (`tests/llm_cases.json`, add your own) in dry-run and prints the hit rate and, separately, the
  **false triggers** (the number that must be 0). The strict matcher alone scores 70 % on that list
  with 0 false triggers (`eval --no-llm`).
* Settings: `llm` in `commands.json` (`model`, `min_confidence`, `max_words`, `prefilter_similarity`,
  `timeout`); per command `description` (what the model reads) and `llm_min_confidence`.

## Spicy talk: scene commands and improvising (adult use, your own machine)

Short phrases (all after "kochanie", offline, no model needed) that change the pace like a scene, not
a single step; the same words work for the model when they are said loosely:

| Say | Effect |
|---|---|
| pieprz mnie, zerżnij mnie, rżnij mnie, wypieprz mnie, weź mnie ostro, jak sukę | **hard**: clearly faster and deeper |
| rozepchaj mnie, wypełnij mnie, głęboko w dupcię, do samego dna | **fill**: deeper, twice |
| drażnij mnie, pieść mnie, delikatnie mnie, powoli mnie, zmysłowo, pomęcz mnie | **tease**: slower and shallower |
| użyj mnie, jestem twoją dziwką / suką / suczką / dziewczynką / sissy, zrób ze mnie dziewczynkę | **use me**: builds up (faster + deeper) |
| zalej mnie, obryzgaj mnie, wytrysnij na mnie, oznacz mnie (+ "kilka razy") | Lube Launcher |
| **dość, wystarczy, starczy, przestań, stój, spokojnie** (key **Home**, button **Calm**) | **calm**: the machine **keeps going** at the slowest speed, medium depth (not a stop) |
| **koniec, koniec wszystkiego, wyłącz maszynę, koniec zabawy** (key **End**, button **Stop**) | **stop everything**: speed 0 at once; the script is held back until you pick a mode or Script |

**Improvising.** When you say something erotic (sissy / submissive talk, begging, praise, teasing)
that matches none of the phrases, the model composes a **short plan** from a fixed, harmless menu:
faster, slower, deeper, shallower, built-in modes 1-8, A, B, one Lube Launcher shot, back to the script, normal. It reads the
mood (begging and "use me" build up, "fill me" goes deep, teasing slows down and gets shallow), sees
your last commands and varies the plan a little (temperature 0.3).

The scene may also end with **one shot of the Lube Launcher** and may use **modes A / B** (all out, or
crawling) when the mood calls for it. What it can **not** do, whatever it says: stop or calm the machine,
pause anything, fire more than one shot, start a hold or a surprise, or run the scene commands; at most 4
steps, at most 3 "faster" and 3 "deeper" steps per utterance, confidence at least 0.75, a 5 s cooldown. The Output range limits you set in
MultiFunPlayer still apply on top. Every improvised plan is printed and written to
`voice_log.jsonl` (`plan`). Switch the model off with `--no-llm` (the scene phrases above keep working).

**"Enough" is not "stop".** "dość / wystarczy / starczy" never stops the machine, it drops to the slowest
speed at medium depth so you can catch your breath; only **"koniec"** (or key **End**) stops
everything. Both work offline (no model); the model may also pick them with a low bar (0.6 / 0.7). Keep
**End** in mind for when you cannot speak. Plain "stop" only pauses the video.

**Hold ("utrzymaj to").** Starts a 5-minute hold (the minutes are the argument of the `lock` command in
`commands.json`, and the field next to the **Hold** button in MultiFunPlayer). While it lasts the tool answers
`locked` to every machine command without sending it, and MultiFunPlayer itself ignores its machine controls
(buttons, keys, remote commands), so the hold also covers the keyboard. Always allowed: the Lube Launcher and
the safe word **"koniec" / End** (stop, the only thing that ends the hold early). "Dość" does **not** get through the hold
(that is what you asked for); if you want it to, add `calm` to `lock.allow`. Film play / pause and the script
loop are not machine controls: pause / play still work. A pending surprise draw is dropped when the hold starts, the
mode sequence freezes while it lasts, and saying it again restarts the 5 minutes.

While the **film's funscript runs the machine** (no built-in mode), the hold also **loops the last 20 seconds of the
script** (the existing script loop; the seconds are set in the second box next to Hold in MultiFunPlayer), so that
"jeszcze" / "utrzymaj to" keeps the movement that was just playing; the loop ends with the hold. In a built-in mode
(1-8, A, B, Calm) the hold only freezes the mode. "Jeszcze" used to mean "a bit faster"; it is now a hold (use
"szybciej" for faster).

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
