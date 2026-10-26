#!/usr/bin/env python3
"""Polish voice commands for MultiFunPlayer: hold the push-to-talk key, say a command, let go.

    python voice_control.py run                      # push-to-talk loop
    python voice_control.py run --dry-run            # same, but only logs what it would do
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
            confirm=i.get("confirm", d.get("confirm", False))))
    return cfg, intents


def stt_prompt(intents):
    # the cloud model is nudged towards the vocabulary of the commands (a hint, not a guarantee)
    return "Komendy: " + ", ".join(i.phrases[0] for i in intents)[:220]


def build(args):
    cfg, intents = load_config(args.config)
    udp = cfg.get("udp", {})
    executor = Executor(udp.get("host", "127.0.0.1"), udp.get("port", 23555), cfg.get("target", "Buttplug.io/0"))
    log_path = None if args.no_log else os.path.join(os.path.dirname(os.path.abspath(args.config)), cfg.get("log_file", "voice_log.jsonl"))
    controller = Controller(intents, executor.run_steps, dry_run=args.dry_run, log_path=log_path)
    s = cfg.get("stt", {})
    stt = CloudStt(s.get("base_url", "https://api.openai.com/v1"), s.get("model", "whisper-1"), s.get("language", "pl"),
                   api_key_env=s.get("api_key_env", "OPENAI_API_KEY"), timeout=s.get("timeout", 15), prompt=stt_prompt(intents))
    return cfg, controller, stt


def report(outcome, transcript):
    print(f'  "{transcript}" -> {outcome.status}' + (f" [{outcome.intent} {outcome.score:.2f}]" if outcome.intent else "")
          + (f" ({outcome.detail})" if outcome.detail else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "commands.json"))
    ap.add_argument("--dry-run", action="store_true", help="match and log, but send nothing")
    ap.add_argument("--no-log", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    t = sub.add_parser("text"); t.add_argument("transcript")
    w = sub.add_parser("wav"); w.add_argument("file")
    args = ap.parse_args()

    cfg, controller, stt = build(args)

    if args.cmd == "text":
        report(controller.handle(args.transcript), args.transcript)
        return
    if args.cmd == "wav":
        with open(args.file, "rb") as f:
            text = stt.transcribe_wav(f.read())
        report(controller.handle(text), text)
        return

    from audio import PushToTalk, beep, record_while_pressed
    p, a = cfg.get("ptt", {}), cfg.get("audio", {})
    ptt = PushToTalk(p.get("keyboard"), p.get("gamepad_button"))
    print(f"Push-to-talk: {p.get('keyboard') or p.get('gamepad_button')}. {'DRY RUN. ' if args.dry_run else ''}Ctrl+C to quit.")
    while True:
        pcm = record_while_pressed(ptt, a.get("sample_rate", 16000), a.get("min_seconds", 0.4), a.get("max_seconds", 8), a.get("device"))
        if not pcm:
            continue
        try:
            text = stt.transcribe_wav(pcm_to_wav(pcm, a.get("sample_rate", 16000)))
        except SttError as e:
            print("  speech-to-text failed:", e)
            beep("error")
            continue
        outcome = controller.handle(text)
        report(outcome, text)
        beep({"executed": "ok", "dry-run": "ok", "confirm": "confirm"}.get(outcome.status, "ignored"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
