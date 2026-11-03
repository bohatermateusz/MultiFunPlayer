#!/usr/bin/env python3
"""Polish voice commands for MultiFunPlayer: say the wake word ("kochanie") and a command.

    python voice_control.py run                      # continuous listening + keyboard shortcuts
    python voice_control.py run --dry-run            # same, but only logs what it would do
    python voice_control.py keys                     # the keyboard shortcuts
    python voice_control.py mic                      # microphone level (for tuning listen.threshold_db)
    python voice_control.py text "spuść się na mnie" # test the matching without audio / cloud
    python voice_control.py wav nagranie.wav         # cloud transcription of a recording + matching
"""
import argparse
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from controller import Controller  # noqa: E402
from executor import Executor  # noqa: E402
from matcher import Intent  # noqa: E402
from llm import IntentLlm  # noqa: E402
from persona import Persona, render_character  # noqa: E402
from stt import CloudStt, SttError, pcm_to_wav  # noqa: E402


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    d = cfg.get("defaults", {})
    intents = []
    for i in cfg["intents"]:
        intents.append(Intent(
            id=i["id"], phrases=i["phrases"], steps=i["steps"],
            threshold=i.get("threshold", d.get("threshold", 0.88)),
            cooldown=i.get("cooldown", d.get("cooldown", 2.0)),
            max_extra_words=i.get("max_extra_words", d.get("max_extra_words", 2)),
            confirm=i.get("confirm", d.get("confirm", False)),
            description=i.get("description", ""), repeatable=i.get("repeatable", False),
            llm_min_confidence=i.get("llm_min_confidence"), improvise=i.get("improvise", False)))
    return cfg, intents


def stt_prompt(intents, wake_words=None):
    # the cloud model is nudged towards the vocabulary of the commands (a hint, not a guarantee)
    wake = ", ".join(w for w in wake_words) + ". " if wake_words else ""
    return "Komendy: " + wake + ", ".join(i.phrases[0] for i in intents if i.phrases)[:220]


def save_voice_id(config_path, voice_id):
    """writes persona.tts.voice_id into commands.json without reformatting the file (text replacement of that one line)"""
    with open(config_path, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(r'("voice_id"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(voice_id), text, count=1)
    if n != 1:
        raise ValueError("persona.tts.voice_id not found in the config")
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, config_path)


def voices_command(args, cfg):
    """`voices`: list / filter / pick / test ElevenLabs voices"""
    from tts import PCM_RATE, ElevenLabsSpeaker, TtsError
    from audio import play_stream
    t = cfg.get("persona", {}).get("tts", {})
    sp = ElevenLabsSpeaker(t.get("voice_id", ""), t.get("model", "eleven_multilingual_v2"), base_url=t.get("base_url", "https://api.elevenlabs.io"),
                           timeout=t.get("timeout", 15), stage_settings=t.get("stage_settings"), language=t.get("language", "pl"))
    try:
        voices = sp.library_voices(language=t.get("language", "pl")) if args.library else sp.voices()
        if args.female:
            voices = [v for v in voices if str(v[2].get("gender", "")).lower() == "female"]
        if args.young:
            voices = [v for v in voices if str(v[2].get("age", "")).lower() in ("young", "middle_aged", "middle aged")]
        if args.use:
            want = args.use.lower()
            hit = next((v for v in voices if v[0] == args.use or v[1].lower() == want), None)
            if hit is None:
                return f"voices: no voice {args.use!r} in this list (try without filters, or --library)"
            vid, name, labels = hit
            if labels.get("library"):
                vid = sp.add_library_voice(labels["public_owner_id"], vid, name)
            save_voice_id(args.config, vid)
            sp.voice_id = vid
            print(f"voice: {name} ({vid}) saved to {args.config}")
        if args.test:
            if not sp.voice_id:
                return "voices: no voice_id yet (use --use <id|name>)"
            play_stream(sp.stream(args.test, 7), PCM_RATE)
            return 0
    except TtsError as e:
        return f"voices: {e}"
    if not args.use:
        print("Pick one with: python voice_control.py voices --use <id|name>   (listen first: open preview_url)")
        for vid, name, labels in voices:
            labels = {k: v for k, v in labels.items() if k not in ("public_owner_id", "library")}
            print(f"  {vid}  {name}  {', '.join(f'{k}={v}' for k, v in labels.items())}")
    return 0


def build_pool(args, pc, persona, log_path):
    """the never-repeating narration pool (lines_pool.json + said.jsonl next to the log), filled by the persona model"""
    n = pc.get("narration", {})
    p = n.get("pool", {})
    if persona is None or not n.get("enabled") or not p.get("enabled", True) or getattr(args, "no_narration", False):
        return None
    from lines import LinePool
    folder = os.path.dirname(os.path.abspath(args.config))
    examples, max_words = list(n.get("examples", [])), int(n.get("max_words", 16))
    style = str(n.get("style", ""))
    pool = LinePool(lambda theme, avoid, count: persona.fresh_lines(theme, examples, avoid, count, max_words, style=style),
                    pool_path=None if args.no_log else os.path.join(folder, "lines_pool.json"),
                    said_path=None if args.no_log else os.path.join(folder, "said.jsonl"),
                    min_pool=p.get("min_pool", 8), batch=p.get("batch", 20), max_said=p.get("max_said", 2000),
                    similarity=p.get("similarity", 0.8), max_words=max_words, seed=p.get("seed"), banned_words=p.get("banned_words"))
    return pool


def build(args):
    cfg, intents = load_config(args.config)
    udp = cfg.get("udp", {})
    executor = Executor(udp.get("host", "127.0.0.1"), udp.get("port", 23555), cfg.get("target", "Buttplug.io/0"))
    log_path = None if args.no_log else os.path.join(os.path.dirname(os.path.abspath(args.config)), cfg.get("log_file", "voice_log.jsonl"))
    wake = cfg.get("wake", {})
    wake_words = wake.get("words", []) if wake.get("required", True) else []
    lc = cfg.get("llm", {})
    llm = None
    if lc.get("enabled", False) and not args.no_llm:
        llm = IntentLlm(lc.get("base_url", "https://api.openai.com/v1"), lc.get("model", "gpt-4.1-mini"),
                        api_key_env=lc.get("api_key_env", "OPENAI_API_KEY"), timeout=lc.get("timeout", 6),
                        temperature=lc.get("temperature", 0), max_steps=lc.get("max_steps", 4))
    pc = cfg.get("persona", {})
    persona = None
    if pc.get("enabled", False) and not args.no_persona and not args.no_llm:
        persona = Persona(render_character(pc), pc.get("base_url", lc.get("base_url", "https://api.openai.com/v1")),
                          pc.get("model", lc.get("model", "gpt-4.1-mini")), api_key_env=pc.get("api_key_env", lc.get("api_key_env", "OPENAI_API_KEY")),
                          timeout=pc.get("timeout", 8), temperature=pc.get("temperature", 0.8), history_turns=pc.get("history_turns", 12))
    controller = Controller(intents, executor.run_steps, dry_run=args.dry_run, log_path=log_path,
                            wake_words=wake_words, wake_min_similarity=wake.get("min_similarity", 0.8), llm=llm, llm_cfg=lc,
                            lock_cfg=cfg.get("lock"), persona=persona, persona_cfg=pc)
    controller.speak = lambda line: print(f"  Pani: {line}")
    controller.pool = build_pool(args, pc, persona, log_path)
    s = cfg.get("stt", {})
    stt = CloudStt(s.get("base_url", "https://api.openai.com/v1"), s.get("model", "whisper-1"), s.get("language", "pl"),
                   api_key_env=s.get("api_key_env", "OPENAI_API_KEY"), timeout=s.get("timeout", 15), prompt=stt_prompt(intents, wake_words))
    return cfg, controller, stt


def report(outcome, transcript):
    via = f" {outcome.via}" if outcome.via in ("llm", "persona") else ""
    print(f'  "{transcript}" -> {outcome.status}' + (f" [{outcome.intent}{via} {outcome.score:.2f}]" if outcome.intent else "")
          + (f" ({outcome.detail})" if outcome.detail else ""))


def main():
    def add_options(parser, suppress):
        # the options work before and after the command name: "--dry-run run" and "run --dry-run"
        d = {"default": argparse.SUPPRESS} if suppress else {}
        parser.add_argument("--config", **({"default": argparse.SUPPRESS} if suppress else {"default": os.path.join(HERE, "commands.json")}))
        parser.add_argument("--dry-run", action="store_true", help="match and log, but send nothing", **d)
        parser.add_argument("--no-log", action="store_true", help="do not write voice_log.jsonl", **d)
        parser.add_argument("--no-llm", action="store_true", help="strict matching only (offline, no guessing)", **d)
        parser.add_argument("--no-persona", action="store_true", help="no talking persona (plain commands)", **d)
        parser.add_argument("--no-tts", action="store_true", help="print the persona's lines instead of speaking them", **d)
        parser.add_argument("--no-nudge", action="store_true", help="the persona never speaks first", **d)
        parser.add_argument("--no-narration", action="store_true", help="no running commentary at random moments", **d)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_options(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_options(sub.add_parser("run"), suppress=True)
    t = sub.add_parser("text"); t.add_argument("transcript"); add_options(t, suppress=True)
    w = sub.add_parser("wav"); w.add_argument("file"); add_options(w, suppress=True)
    e = sub.add_parser("eval"); e.add_argument("file", nargs="?"); add_options(e, suppress=True)
    add_options(sub.add_parser("keys"), suppress=True)
    v = sub.add_parser("voices")
    v.add_argument("--female", action="store_true", help="only female voices")
    v.add_argument("--young", action="store_true", help="only young / middle-aged voices")
    v.add_argument("--library", action="store_true", help="search the shared library (female, young, Polish) instead of your account")
    v.add_argument("--use", metavar="ID_OR_NAME", help="put this voice into commands.json -> persona.tts.voice_id (a library voice is added to the account)")
    v.add_argument("--test", metavar="TEXT", help="play this text with the configured (or --use) voice")
    add_options(v, suppress=True)
    add_options(sub.add_parser("mic"), suppress=True)
    args = ap.parse_args()

    cfg, controller, stt = build(args)

    if args.cmd == "eval":
        from llm_eval import run_eval
        sys.exit(run_eval(controller, args.file))
    if args.cmd == "voices":
        sys.exit(voices_command(args, cfg))
    if args.cmd == "keys":
        from hotkeys import cheat_sheet
        print("Keyboard shortcuts (global, NumLock on):")
        print(cheat_sheet(cfg.get("keys", {}), controller.intents))
        return
    if args.cmd == "mic":
        from audio import meter
        a, lis = cfg.get("audio", {}), cfg.get("listen", {})
        print("Speak normally, then stay quiet; Ctrl+C to stop. The threshold follows the background noise.")
        meter(a.get("sample_rate", 16000), a.get("device"), threshold_db=lis.get("threshold_db"), margin_db=lis.get("margin_db", 12))
        return
    if args.cmd == "text":
        report(controller.handle(args.transcript), args.transcript)
        return
    if args.cmd == "wav":
        with open(args.file, "rb") as f:
            text = stt.transcribe_wav(f.read())
        report(controller.handle(text), text)
        return

    from audio import PushToTalk, listen_segments, record_while_pressed
    from audio import beep as play_tone
    p, a, lis = cfg.get("ptt", {}), cfg.get("audio", {}), cfg.get("listen", {})
    sounds = cfg.get("sounds", "off")   # "off" (silent, the default), "errors" (only problems) or "all"

    def beep(kind):
        if sounds == "all" or (sounds == "errors" and kind == "error"):
            play_tone(kind)

    rate = a.get("sample_rate", 16000)
    always = lis.get("mode", "always") == "always"
    wake = cfg.get("wake", {})
    if always and wake.get("required", True) and not wake.get("words"):
        sys.exit("listen.mode 'always' needs a wake word (commands.json: wake.words)")

    def process(pcm):
        try:
            text = stt.transcribe_wav(pcm_to_wav(pcm, rate))
        except SttError as e:
            print("  speech-to-text failed:", e)
            beep("error")
            return
        outcome = controller.handle(text)
        report(outcome, text)
        beep({"executed": "ok", "dry-run": "ok", "confirm": "confirm"}.get(outcome.status, "ignored"))

    muted = {"value": False}

    # the persona speaks (ElevenLabs, streamed; OpenAI as a fallback). With headphones the microphone stays on and
    # your voice cuts her off, so the safe word is always heard; with speakers it is deaf while she speaks.
    from audio import SpeechGate, play_stream
    from speech import Voice
    from tts import PCM_RATE, ElevenLabsSpeaker, Speaker, TtsError, stream_openai
    pcfg = cfg.get("persona", {})
    tcfg = pcfg.get("tts", {})
    headphones = pcfg.get("output", "headphones") == "headphones"
    gate = SpeechGate(tail=pcfg.get("echo_tail", 0.6))
    talk = {"t": None, "stop": False}

    def build_speakers():
        out = {}
        if tcfg.get("provider", "elevenlabs") == "elevenlabs" or tcfg.get("fallback_provider") == "elevenlabs":
            el = ElevenLabsSpeaker(tcfg.get("voice_id", ""), tcfg.get("model", "eleven_multilingual_v2"),
                                   base_url=tcfg.get("base_url", "https://api.elevenlabs.io"), timeout=tcfg.get("timeout", 15),
                                   stage_settings=tcfg.get("stage_settings"), language=tcfg.get("language", "pl"))
            if not el.voice_id and el.api_key:
                # no voice chosen yet: pick a girlish female voice once and remember it in the config
                try:
                    picked = el.pick_voice(tcfg.get("voice_prefer", {}))
                    if picked:
                        print(f"  voice: {picked[1]} ({picked[0]})")
                        try:
                            save_voice_id(args.config, picked[0])
                        except (OSError, ValueError) as e:
                            print(f"  voice: could not save voice_id: {e}")
                except TtsError as e:
                    print(f"  voice: no ElevenLabs voice picked ({e}); OpenAI voice is used")
            out["elevenlabs"] = el
        o = tcfg.get("openai", {})
        out["openai"] = Speaker(o.get("base_url", "https://api.openai.com/v1"), o.get("model", "gpt-4o-mini-tts"), o.get("voice", "coral"),
                                o.get("instructions", ""), api_key_env=o.get("api_key_env", "OPENAI_API_KEY"), speed=o.get("speed", 1.0))
        return out

    speakers = build_speakers() if controller.persona is not None and not args.no_tts else {}

    def provider(name):
        if name == "elevenlabs":
            return lambda line, stage: speakers["elevenlabs"].stream(line, stage)
        o = tcfg.get("openai", {})
        return lambda line, stage: stream_openai(speakers["openai"], line, stage, o.get("instructions_soft"), o.get("instructions_sharp"))

    order = [tcfg.get("provider", "elevenlabs"), tcfg.get("fallback_provider", "openai")]
    voice = Voice({n: provider(n) for n in speakers}, order, lambda stream, stop: play_stream(stream, PCM_RATE, stop=stop),
                  gate=gate, muted=lambda: muted["value"])

    def speak(line, stage=0):
        print(f"  Pani [{stage}]: {line}")
        voice.say(line, stage)

    controller.speak_ex = speak
    controller.busy = lambda: muted["value"] or voice.speaking() or gate.speaking()

    def barge_in():
        if headphones:
            voice.interrupt()

    def toggle_mute():
        muted["value"] = not muted["value"]
        print("  listening", "OFF (muted)" if muted["value"] else "ON")
        beep("error" if muted["value"] else "ok")

    def on_key(intent_id):
        outcome = controller.press(intent_id)
        report(outcome, f"[key] {intent_id}")
        beep("ok" if outcome.status in ("executed", "dry-run") else "ignored")

    keys = cfg.get("keys", {})
    if keys:
        try:
            from hotkeys import Hotkeys, cheat_sheet
            Hotkeys(keys, on_key, toggle_mute)
            print("Keyboard shortcuts (global, NumLock on):")
            print(cheat_sheet(keys, controller.intents))
        except ImportError:
            print("pynput is not installed: no keyboard shortcuts")

    if always:
        print(f"Listening continuously. Say \"{', '.join(wake.get('words', []))}\" and a command. "
              f"{'DRY RUN. ' if args.dry_run else ''}Ctrl+C to quit.")
        if controller.persona is not None:
            if controller.pool is not None:
                controller.pool.warm_up(cfg.get("persona", {}).get("narration", {}).get("themes", []))
            def nudger():
                while True:
                    time.sleep(1.0)
                    try:
                        controller.tick()            # her program (e.g. a new mode every 3 minutes)
                        if not args.no_narration:
                            controller.idle_narration()
                        if not args.no_nudge:
                            controller.idle_nudge()
                    except Exception:
                        pass
            threading.Thread(target=nudger, daemon=True).start()
        for pcm in listen_segments(rate, a.get("device"), muted=lambda: muted["value"] or (not headphones and gate.speaking()),
                                   on_speech_start=barge_in,
                                   threshold_db=lis.get("threshold_db"), margin_db=lis.get("margin_db", 12),
                                   end_silence=lis.get("end_silence", 0.7), pre_roll=lis.get("pre_roll", 0.3),
                                   min_seconds=lis.get("min_seconds", 0.5), max_seconds=lis.get("max_seconds", 6.0)):
            process(pcm)
        return

    ptt = PushToTalk(p.get("keyboard"), p.get("gamepad_button"))
    print(f"Push-to-talk: {p.get('keyboard') or p.get('gamepad_button')}. {'DRY RUN. ' if args.dry_run else ''}Ctrl+C to quit.")
    while True:
        pcm = record_while_pressed(ptt, rate, a.get("min_seconds", 0.4), a.get("max_seconds", 8), a.get("device"))
        if pcm and not muted["value"]:
            process(pcm)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
