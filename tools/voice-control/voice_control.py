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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from controller import Controller  # noqa: E402
from executor import Executor  # noqa: E402
from matcher import Intent  # noqa: E402
from llm import IntentLlm  # noqa: E402
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
            llm_min_confidence=i.get("llm_min_confidence")))
    return cfg, intents


def stt_prompt(intents, wake_words=None):
    # the cloud model is nudged towards the vocabulary of the commands (a hint, not a guarantee)
    wake = ", ".join(w for w in wake_words) + ". " if wake_words else ""
    return "Komendy: " + wake + ", ".join(i.phrases[0] for i in intents)[:220]


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
                        api_key_env=lc.get("api_key_env", "OPENAI_API_KEY"), timeout=lc.get("timeout", 6))
    controller = Controller(intents, executor.run_steps, dry_run=args.dry_run, log_path=log_path,
                            wake_words=wake_words, wake_min_similarity=wake.get("min_similarity", 0.8), llm=llm, llm_cfg=lc)
    s = cfg.get("stt", {})
    stt = CloudStt(s.get("base_url", "https://api.openai.com/v1"), s.get("model", "whisper-1"), s.get("language", "pl"),
                   api_key_env=s.get("api_key_env", "OPENAI_API_KEY"), timeout=s.get("timeout", 15), prompt=stt_prompt(intents, wake_words))
    return cfg, controller, stt


def report(outcome, transcript):
    via = f" {outcome.via}" if outcome.via == "llm" else ""
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

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_options(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_options(sub.add_parser("run"), suppress=True)
    t = sub.add_parser("text"); t.add_argument("transcript"); add_options(t, suppress=True)
    w = sub.add_parser("wav"); w.add_argument("file"); add_options(w, suppress=True)
    e = sub.add_parser("eval"); e.add_argument("file", nargs="?"); add_options(e, suppress=True)
    add_options(sub.add_parser("keys"), suppress=True)
    add_options(sub.add_parser("mic"), suppress=True)
    args = ap.parse_args()

    cfg, controller, stt = build(args)

    if args.cmd == "eval":
        from llm_eval import run_eval
        sys.exit(run_eval(controller, args.file))
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

    from audio import PushToTalk, beep, listen_segments, record_while_pressed
    p, a, lis = cfg.get("ptt", {}), cfg.get("audio", {}), cfg.get("listen", {})
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
        for pcm in listen_segments(rate, a.get("device"), muted=lambda: muted["value"],
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
