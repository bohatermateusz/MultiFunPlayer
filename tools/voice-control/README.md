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
| **hold**: the machine keeps exactly what it does now for **5 minutes**; everything else (modes, boosts, surprise, scenes, calm, sequence, keys) is ignored. Only the **Lube Launcher** and the safe word **"trąbka" (End)** still work; nothing else ends it early | utrzymaj to, rób tak dalej, tak trzymaj, zostaw tak, nie zmieniaj, tak ma być, **jeszcze, nie przestawaj, właśnie tak** | PgUp |
| **cooldown** (the end of a session): over **5 minutes** both machines slow down to the slowest speed, go to full depth and narrow the stroke to 10 % (slow, even micro strokes over the prostate); **one** Lube Launcher shot at a random moment (front, rear or both); then they stay like that until another command. Started ONLY by the password **"pomidor uspakajający"**, said alone like the safe word (no "kochanie" needed, at any moment; in English mode also "calming tomato"); "pomidor uspakajający od tyłu" winds down only that machine. Never decided by dice - she only comments it tenderly, her narration stays tender meanwhile and her program stops. Faster / deeper / a mode / "trąbka" ends it | **pomidor uspakajający** (EN also: calming tomato) | Scroll Lock |
| back to the funscript and no boost | normalnie, zresetuj, bez zmian | Num . |
| back to the funscript (keep boost) | wróć do filmu, wróć do skryptu, wyłącz tryb | (button "Script") |
| Lube Launcher | spuść się na mnie, strzel na mnie, spuść się, dojdź na mnie | F8 |
| Lube Launcher several times | spuść się na mnie kilka razy / dwa razy / trzy razy | |
| play / pause the video | graj, wznów / pauza, zatrzymaj film ("stop" is calm, not pause) | |
| script loop on / off | zapętl to, powtórz to / koniec pętli, wyłącz pętlę | |
| mode sequence (2 min per mode) | włącz sekwencję, wstrzymaj tryb, wznów sekwencję, koniec sekwencji | |
| calm (slowest, medium depth) / stop everything | dość, wystarczy, starczy / trąbka | Home / End |
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
| **trąbka, trąbko, wyłącz maszynę, zatrzymaj maszynę** (key **End**, button **Stop**) | **stop everything**: speed 0 at once; the script is held back until you pick a mode or Script |

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
speed at medium depth so you can catch your breath; only **"trąbka"** (or key **End**) stops
everything. Both work offline (no model); the model may also pick them with a low bar (0.6 / 0.7). Keep
**End** in mind for when you cannot speak. Plain "stop" only pauses the video.

**Hold ("utrzymaj to").** Starts a 5-minute hold (the minutes are the argument of the `lock` command in
`commands.json`, and the field next to the **Hold** button in MultiFunPlayer). While it lasts the tool answers
`locked` to every machine command without sending it, and MultiFunPlayer itself ignores its machine controls
(buttons, keys, remote commands), so the hold also covers the keyboard. Always allowed: the Lube Launcher and
the safe word **"trąbka" / End** (stop, the only thing that ends the hold early). "Dość" does **not** get through the hold
(that is what you asked for); if you want it to, add `calm` to `lock.allow`. Film play / pause and the script
loop are not machine controls: pause / play still work. A pending surprise draw is dropped when the hold starts, the
mode sequence freezes while it lasts, and saying it again restarts the 5 minutes.

While the **film's funscript runs the machine** (no built-in mode), the hold also **loops the last 20 seconds of the
script** (the existing script loop; the seconds are set in the second box next to Hold in MultiFunPlayer), so that
"jeszcze" / "utrzymaj to" keeps the movement that was just playing; the loop ends with the hold. In a built-in mode
(1-8, A, B, Calm) the hold only freezes the mode. "Jeszcze" used to mean "a bit faster"; it is now a hold (use
"szybciej" for faster).

## "Pani": she talks back and decides

With `"persona": {"enabled": true}` (default) a dominant female character answers you **aloud** (OpenAI
text-to-speech, voice `sage`, Polish, firm) and the things you ask for go through **her**:

* **What is hers to decide** (everything but two commands): you ask, she demands ("Poproś mnie.", "Powiedz,
  kim jesteś.", "Podziękuj."), the **dice** decide: first ask 25 % (50 % for mild requests: enough, slower,
  shallower, pause, play), +20 % per earlier refusal, +30 % when you confess ("jestem Twoją dziwką", "jestem
  Twoja"), the 5th ask >= 95 %; when she demanded something and you do it, it is yours. That includes **"dość" /
  "wystarczy"** (she may refuse), slower / shallower, the modes, the surprise, the film (pause / play) and the loop.
  While she refuses she may tease (slower, shallower, a crawling mode). The odds are the tool's, not the model's
  (`base_p`, `base_p_overrides`, `step_p`, `confession_p`, `max_asks`), every decision is logged with `p` and `roll`.
* **What she cannot touch** (`persona.never_gated`): the safe word **"trąbka"**, the hold **"utrzymaj to"**
  (and friends) and the cooldown password **"pomidor uspakajający"** (no wake word needed, like "trąbka"; she only comments it: `persona.cooldown.lines`). They run at once, also in her reply window without "kochanie". **Every keyboard shortcut
  bypasses her** (End = trąbka; when you cannot speak, the key is the way).
* **If she cannot be reached** (no key / network): the mild commands in `persona.fallback_plain` (enough, slower,
  shallower, back to the script, normal, pause, play, loop) run plainly; everything else (the Lube Launcher,
  faster, deeper, the scenes, the modes, the surprise) is ignored until she is back.
* **Answering her**: for 60 s after her line (`reply_window`) you speak without "kochanie" ("proszę, Pani",
  "jestem Twoją małą dziwką"). The safe word and the ungated commands work in that window too.
* **Running commentary** (`persona.narration`): while a session is on she comments at random moments whether you talk
  or not - explicit, degrading lines on a rotating theme (sucking like a little girl, a bitch to be spread, belonging
  to them, the only job, forever, feminisation, taken from behind, praise, ownership, tenderness in humiliation). The
  gap is drawn from `gap_seconds[stage]` (90-150 s at stage 0 down to 20-40 s at stage 4), `sharp_p[stage]` picks
  vulgar vs tender-in-humiliation, `examples` only set the register - the model invents its own lines, never a
  question, never a command, never right after her last line. `--no-narration` turns it off; the log tags it
  `[narration]` with the theme.
* **Wishes, not phrases** (`shots`, `program` in her answer): she understands quantity ("zalej mnie" = 3-4 shots, "całą" = 5;
  `persona.lube.max_shots`, one roll for all of them, run through `LubeLauncher::FireMany`) and timing: "zmieniaj tryby co
  3 minuty" (a random mode every 180 s, `Hismith::Mode::Random`), "za pięć minut zwolnij", "na minutę szybciej" (and back),
  "zalewaj mnie co pięć minut" (one shot per firing, `program.lube_every_min` apart). A granted wish with timing becomes her
  program, run by `Controller.tick()` from the 1 s thread (no model calls); bounds in `persona.program` (every 30-900 s,
  start <= 900 s, duration <= 3600 s); one program at a time, a new one replaces it; "przestań zmieniać" cancels it without
  dice; a hold makes it wait; "trąbka"/End ends it. Her own unasked moves keep the one-shot limit.
* **Her identity** (`persona.identity`): five texts rendered in front of every prompt - `ona` (who she is: Wiktoria,
  calm, tender and merciless by turns), `ty` (who you are: his girl, princess, teddy bear; what he reacts to), `repertuar`
  (how she teases, rewards, satisfies, punishes), `narzedzia` (what each control means as a gesture) and `po_sesji`
  (warm aftercare once "trąbka" ends it). Edit `ty` to taste; `character` stays as the short core.
* **Last-second refusal** (`persona.last_second`): sometimes (18 %, from stage 1) a Lube Launcher request that the dice
  granted is held back at the last second ("prawie... jeszcze nie"): no shot, no punishment, one ask is given back, the
  next ask is likelier (+0.25). Never twice in a row, never on the sure ask.
* **Unasked shot** (`spontaneous.lube_surprise`): on her own (a silence nudge, or after a confession / thanks, 1.5x) she may
  shoot the Lube Launcher once, from stage 2 (6/12/20 % by stage 2/3/4), at most 3 per 30 min and 300 s apart, never while
  you are punished. Works in a hold as well.
* **Manners** (`persona.manners`): asking without "proszę"/"błagam"/"Pani" cuts the odds (x0.6); ignoring her demand
  (not doing what she asked) cuts them further (x0.4). An open refusal ("nie chcę", "nie powiem", "nie będę prosić", or
  the model flags defiance) wipes your asks and nothing is granted for 90 s, or until you apologise ("przepraszam,
  Pani"). Not even the 8th ask beats an active punishment. "trąbka"/End and "utrzymaj to" are never punished.
* **Safe word "trąbka"**: "trąbka" (also "trąbko", "trąbki", alone or after the wake word, also in English mode) stops
  every machine at once, releases the hold, cuts her voice. "koniec" is no longer the safe word (it is free for
  role-play; "koniec pętli" etc. stay commands). The End key does the same.
* **Two machines, two Lube Launchers** (`devices.sides`): in MultiFunPlayer, Buttplug.io output -> **Device roles**, set
  each connected device to Rear (from behind), Front (her mouth) or Both (every command, the default). MFP keeps a separate
  state per side (mode, speed, depth, boosts, Lube Launcher burst) and has side actions (`Hismith::Rear::Speed::Faster`,
  `Hismith::Front::LubeLauncher::FireMany`, ...). The voice tool picks the side from your words ("spuść mi się do ust" ->
  front launcher, "bierz mnie szybciej od tyłu" -> rear machine, "do środka" -> rear launcher), else she picks it from the
  context (the `side` field of her answer), else the side used last; nothing ever said -> every side. Keys and the safe
  word always reach every side. Her programs keep the side they were made with.
* **Another woman every 10 minutes** (`tts.rotate_minutes`): the next voice of the list takes over and introduces herself
  with a line from `tts.handover_lines` ("Teraz ja się tobą zajmę, misiaczku."), never the same twice in a row; F9 still
  switches by hand.
* **No endless "ask nicer"**: what she demanded is remembered; doing it roughly is enough (a polite word after "ask
  nicer" adds `manners.beg_met_bonus` 0.35 and continues the same ask even if you do not repeat it; "dziękuję" after
  "thank me", a "jestem ..." after "say what you are", a close repetition after "repeat" - she gives it). Her refusal is
  never the same sentence twice in a row (`manners.denials` variants), plain talk without a pending ask gets her answer,
  not "Jeszcze nie", and the fifth ask is (almost) sure (`max_asks` 5).
* **Her lines are not cut** (classic mode): a new line waits until the one playing has ended (a queue; a comment
  waiting is dropped when an answer comes), your voice does not cut her (`listen.barge_in`: `safe_word` by default,
  `always` for the old behaviour), only the safe word / End does - at once, also when it starts a longer utterance
  ("trąbka szybciej"). Narration keeps quiet for 12 s after you speak (`narration.quiet_after_user`) and while she waits
  for what she demanded. Whisper's hallucinations (walls of "tryb pięć, tryb pięć...") are dropped (`listen.max_words`),
  the recognition prompt is short, a dropped connection is retried once. A repetition with a misheard word counts as
  obedience; the model's "defiance" counts only when you actually said no.
* **Conversation modes, F11** (`conversation.mode`, `--mode`): **classic** is the old loop (record the whole utterance,
  transcribe, answer, speak: ~2-4 s of silence before her first word). **live** streams everything: OpenAI Realtime
  transcription as you speak (`gpt-4o-mini-transcribe`, server VAD, `silence_ms` 500), the safe word and plain commands
  run from the *partial* transcript, a request to her starts her thinking early (speculative; used when the final
  transcript says the same), her answer is streamed (the schema puts the lines last, so the dice roll as soon as the
  head is in and the winning line is spoken sentence by sentence through the ElevenLabs websocket voice while it is
  still being written), and your voice cuts her off the moment you start speaking. Target: her first word ~1-1.5 s
  after your last; if the realtime session drops, the local detector and the batch model take over. Needs
  `websocket-client` (in requirements). **agents** is the experimental ElevenLabs Agents mode: one audio stream both
  ways, the most fluent turn-taking, paid per minute (Starter: ~50 min/month, then 0.08 USD/min); our persona stays
  the agent's brain through a local OpenAI-compatible proxy (the agent's "Custom LLM"), so the dice, the steps, the
  programs and the narration (the proxy answers a periodic text turn with a pool line) are still ours; the safe word is
  also caught locally from the agent's transcripts. Setup: create an agent in the ElevenLabs dashboard (Polish, a
  female voice, "v3 Conversational" for turn-taking, LLM = Custom LLM), expose the proxy (`cloudflared tunnel --url
  http://localhost:8787`) and put `https://<tunnel>/v1` as the Custom LLM server URL, then `conversation.agents.agent_id`
  in the config. Without an `agent_id` the mode is skipped. F11 cycles the available modes; she confirms
  ("Teraz na żywo." / "Klasycznie.").
* **How she reads a line** (`persona.tts.delivery`): a delivery preset per line - `whisper`, `tender`, `tease`, `sharp`,
  `afterglow` - picked by the kind of line (`by_kind`: an order -> sharp, a "powtórz za mną" affirmation -> whisper,
  aftercare -> afterglow) or by the tone of the turn (`by_tone`: tender -> whisper/tender, sharp -> tease/sharp, the
  second one from stage 3). With the v2 models the text gets `<break>` pauses after ellipses and between sentences;
  with `eleven_v3` it gets audio tags in front (`[whispers]`, `[mischievously]`...) and v3 paces by punctuation. Tuning:
  too flat -> raise `style`, lower `stability`; too theatrical -> the opposite; too fast -> lower `speed`.
* **Narration synthesized ahead** (`tts.narration_model`, `tts.audio_cache`): pool lines are rendered in the background
  with the expressive `eleven_v3` (v2 when v3 refuses) into `audio_cache/`, so a narration line plays instantly with
  full expression (`Pani [3 cache]:` in the console); answers keep the streamed live model. 200 MB cap, a file is
  played once.
* **Hear it before you choose**: `voices --test "Odpuść… ja to trzymam." --preset whisper` vs `--preset sharp`, or
  `--model eleven_v3 --tags "[whispers]"`; `voices --audition` plays the same line with every listed voice and every
  preset, one after another, naming each - pick by ear, then `voices --use <name>`.
* **Voices and languages by key**: **F9** = the next voice of the current language from `persona.tts.voices` (Hanna,
  Jagna, Asia, Jola, Arleta for Polish; Jessica, Lily, Sarah, Alice for English - resolved by name on the account, or
  added from the library), saved to the config, confirmed in her new voice ("Teraz brzmię tak." / "This is my voice
  now."). **F10** = Polish/English, and *everything* switches: the command phrases (`intents[].phrases_en`: "faster",
  "cum on me", "mode three", "hold it"...), the wake words (`wake.words_en`: darling, mistress, lady, honey), the safe
  word ("stop" / "red" / "end" - like "trąbka" it needs no wake word), speech recognition language, her addresses,
  confessions, manners, narration themes / orders / seeds, the line pool (`lines_pool.en.json`), the voice and the
  OpenAI fallback instructions - all from the overlay `persona.languages.en`, merged over the Polish persona. Start in
  English with `--language en` or `"language": "en"` in the config. `voices --use <name> --lang en` saves an English voice.
* **Her voice needs no setup**: with the ElevenLabs key set, the first line she says tries the voices of
  `persona.tts.voices` for the current language in order (Polish: Hanna, Jagna, Asia, Jola, Arleta; English: Jessica,
  Lily, Sarah, Alice), adds a library voice to your account when it is not there yet, falls back to a female voice from
  your account, keeps the first one that speaks and writes it into the config - nothing to run by hand. F9 switches to
  the next one any time. (The `voices` command below is optional, for auditioning.)
* **Her voice is picked for you** (older notes): with `persona.tts.voice_id` empty, the first run looks for a girlish female voice on your
  ElevenLabs account (`voice_prefer.names` - Hanna, Jagna, Asia, Jola, Arleta from the ASMR app first, then Jessica / Lily /
  Sarah / Alice / Matilda - then `voice_prefer.ids` (your own picks by id, added from the library when missing), then any female young/middle-aged by
  labels, then any female, then - `allow_library` - a young female Polish voice from the shared library, added to the
  account) and writes the id back into `commands.json`. `python voice_control.py voices --female --young` lists candidates
  with `preview_url` (listen in a browser), `voices --library` searches the shared library, `voices --use <id|name>` saves
  one, `voices --test "Jesteś moja."` plays a sample. Model `eleven_multilingual_v2` (the most natural in Polish; flash is
  cheaper and flatter), lively `stage_settings` (style 0.35-0.65, stability 0.55-0.35, speaker boost). The OpenAI fallback
  uses `coral` with "young, girlish, natural" instructions. Too mature? `voices --use` another one; too flat? raise `style`.
* **Her orders** (`persona.narration.tasks`): physical instructions mixed into the narration (`share[stage]` 15-40 %),
  adapted from the session-controller list - gentle early (squeeze, breathe, hands on thighs), harder late (clamps, the
  oral dildo, "deeper, almost gag", "repeat: jestem posłuszną sissi"), never the same order twice in a session, `care`
  lines (breathe, water, take the clamps off) when the list is spent. Logged as `source: task`.
* **Nothing repeats** (`persona.narration.pool`): a pool of pre-generated lines per theme (`lines_pool.json`) is refilled in
  the background by the persona model when a theme runs low (20 at a time, with the last 40 spoken lines as "do not repeat"),
  filtered against everything ever said (`said.jsonl`, exact and near duplicates, no questions, `max_words`). Narration takes
  from the pool first (instant, `source: pool` in the log), the live model only when it is empty - and a live line that
  still repeats something said before is retried once, then dropped; answers and nudges that repeat are spoken but
  tagged `repeat: true` in the log. The pool starts from `pool.seed` (lines taken or adapted from the ASMR scripts:
  "sama się zgłosiłaś", "wymazana", "zaprogramowana", "oba końce", "jedyny orgazm", "zbrukana i dumna", "rajstopy",
  "złam mnie", "bez granic", "puść się", "prostata", "klub"...), 36 themes, 30 fresh lines per refill, 5000 lines of
  history. `narration.style` asks for whisper-like delivery (ellipses as breaths, "powtórz za mną" affirmations).
  Skipped on purpose from those repos: the substance timeline, and the blackmail / prison / trafficking scripts.
* **The world is VR, the gear is never named**: in her words the machines are the men who take her ("on", "oni", "ten,
  który cię bierze", "jego rytm", "jego sperma"), never "maszyna", "dildo", "silnik", "launcher", "tryb", "skrypt". The
  prompts say so, `pool.banned_words` keeps such lines out of the pool, a live line naming the gear is retried once and
  dropped, and an answer that slips through is tagged `names_gear: true` in the log.
* **Her voice: ElevenLabs, streamed** (the first sound after ~0.5 s, not after the whole sentence), with OpenAI TTS as a
  fallback when ElevenLabs is down or refuses a text. Set `ELEVENLABS_API_KEY`, run
  `python voice_control.py voices`, put the id of a voice you like into `persona.tts.voice_id`. Model
  `eleven_flash_v2_5` (fastest; try `eleven_multilingual_v2` for quality). The voice settings follow the stage.
* **Fallback voice**: if ElevenLabs cannot or will not speak a line (no key / voice_id, HTTP error, rate limit, a text it
  refuses, an empty answer) the **same line is spoken by OpenAI TTS** (`persona.tts.fallback_provider`, voice `sage`,
  with the firm-voice instructions in `persona.tts.openai`). If a stream breaks after the first sound the line is not
  repeated; if both fail, the line is only printed and the tool goes on.
* **Forms of address**: she does not have to be "Pani". Both **"Pani"** and **"kochanie"** start a command (`wake.words`; add
  more, e.g. "moja Pani"). She may call you whatever `persona.address.titles` lists (Pani, kochanie, moja Pani...), and she
  calls the user by the varied `persona.address.names` (dziewczynko, mała, suko, kochanie, słoneczko, ...). Edit both lists freely.
* **A game of tenderness and sharpness**: she is not just a ramp of vulgarity. Every turn the tool rolls its tone: **tender**
  (care, love, letting go: "Możesz już odpuścić.", "Nie kontrolujesz tego.", "Odpuść.", "Jesteś moja.") or **sharp** (orders,
  humiliation, vulgar). Sharp is rare early and grows with the stage (`persona.tone.sharp_p` 0 / 8 / 22 / 38 / 50 %), never more than
  two sharp turns in a row, tenderness often follows it ("ulga"), the first two turns are always tender, and a **twist** (8 %)
  can reverse the roll: now and then sharp comes out of nowhere, or tenderness at the peak, so you are never sure. At the top
  stage a sharp turn has a 30 % chance of the **harshest bank** (`bardzo_ostre`): the really hard words come somewhere
  late in the game and not often. The voice follows the tone (soft and slow when tender, cold and fast when sharp).
* **Her own moves (surprises)**: besides answering she sometimes acts unasked - a reward (faster / deeper), a tease (slower /
  shallower) or a twist (a mode), after a plain talk turn or when she speaks first (`persona.spontaneous`, more often at higher
  stages). The Lube Launcher unasked only from stage 3, with a 12 % chance per move and not twice within 5 minutes; in a hold only
  the Lube Launcher; never stop / calm / hold / surprise. Every unasked move is logged (`unsolicited`).
* **Harder to get**: she makes you ask. The first chance is low (Lube Launcher 6 %, "pieprz mnie" and friends 10 %, mild requests such
  as "dość" or "wolniej" 35 %), grows slowly with every refusal (+8 % / +12 %), +20 % for a confession, and only the 8th ask is
  (almost) sure: about 5 asks to the Lube Launcher on average. `persona.difficulty` scales every chance (0.5 = harder, 2 = easier).
* **Variety of words (the vocabulary bank)**: `persona.vocabulary` holds ~100 phrases in six categories (orders: "klęknij",
  "ssij", "sssij ładnie", "połykaj"...; ownership: "należysz do mnie", "jesteś moją własnością"...; humiliations: "spójrz na
  siebie"...; taunts, praise, demands for confessions), each tagged with the stage it fits (gentle at 0-1, sharp and vulgar
  from 2). Every turn the tool draws **3 phrases from different categories for the current stage, none used in the last 14**,
  and hands them to the model as inspiration: it weaves one or two in, literally or varied (stretched like "sssij"),
  and invents its own in the same style. Add your own phrases to the lists: one string per entry, under the stage digit.
* **Your side**: say what you like, it is conversation. Confessions ("należę do Ciebie", "jestem Twoją dziwką", "nie mam
  własnej woli", "zrób ze mną co chcesz"...) and obedience ("dziękuję, Pani", "ssę", "połykam", "klęczę", "tak Pani") are
  also **recognised locally** (`confession_phrases`, `obey_phrases`), so they count for the dice (+30 % for a confession; a
  demanded thanks / task pays what she owed) even if the model does not notice them.
* **Escalation**: the tool counts the heat of the session (+1 per turn, +1 per refusal, +2 per confession, +2 per grant;
  thresholds 0 / 5 / 12 / 22 / 35; cooling down after 10 min of silence) and gives the model the **stage 0-4** and its tone
  (`persona.stages`, Polish, edit freely): cool and gentle -> firm -> sharp and degrading -> vulgar and merciless ->
  all out. Higher stages also tease more often when she refuses. `escalation.max_stage` is your ceiling.
* **She speaks first**: when you stay silent for 25 s during a session she says something herself ("Czemu
  milczysz? Poproś mnie.", questions, orders), up to 3 times in a row (`persona.initiative`, `--no-nudge`).
* **Safe word while she talks**: `persona.output = "headphones"` (default; a VR headset): the microphone stays on and
  **your voice cuts her off at once**, so "trąbka" is always heard. With `"speakers"` the microphone is deaf while she
  speaks (and 0.6 s after, against echo) - then the safe word is the **End key**.
* **Bounds**: her steps are validated like an improvised scene (allowed ids, ≤ 3 faster / 3 deeper, ≤ 1 shot);
  she can never stop or calm the machine, start a hold or a surprise. In a hold she only talks (and the Lube
  Launcher still goes through her dice).
* `--no-tts` prints her lines instead of speaking; `--no-persona` (or `enabled: false`) brings back plain
  commands; `text` mode prints her line. Her character sheet is `persona.character` (Polish, edit freely); the
  voice and its instructions are under `persona.tts`.

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
