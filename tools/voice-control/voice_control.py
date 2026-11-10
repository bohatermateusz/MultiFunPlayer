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
import collections
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
            phrases_by_lang={k[len("phrases_"):]: v for k, v in i.items() if k.startswith("phrases_") and isinstance(v, list)},
            llm_min_confidence=i.get("llm_min_confidence"), improvise=i.get("improvise", False)))
    return cfg, intents


STT_HINT_INTENTS = ("faster", "slower", "deeper", "shallower", "halt", "lock", "lube_fire", "cooldown")
# short answers that count even when said while she is still speaking (unless she is saying them herself)
OVER_HER_WORDS = ("proszę", "błagam", "przepraszam", "dziękuję", "tak", "please", "sorry", "yes")


def stt_prompt(intents, wake_words=None):
    # a short hint only: a long list of commands is what Whisper loops on noise ("tryb pięć, tryb pięć, ...")
    wake = ", ".join(w for w in wake_words) + ". " if wake_words else ""
    phrases = [i.phrases[0] for i in intents if i.phrases and i.id in STT_HINT_INTENTS]
    return (wake + ", ".join(phrases))[:100]


def save_voice_id(config_path, voice_id, lang="pl"):
    """writes persona.tts.voice_id (or persona.languages.<lang>.tts.voice_id) into commands.json without reformatting
    the file: the one "voice_id" line of that tts block is replaced (the entries of the voices list are left alone)"""
    with open(config_path, encoding="utf-8") as f:
        text = f.read()
    start = text.index('"persona"')
    if lang != "pl":
        start = text.index('"languages"', start)
        start = text.index(json.dumps(lang), start)
    tts_at = text.index('"tts"', start)
    # the block's own "voice_id" is on a line of its own (the voices list keeps its ids inline in {...})
    m = re.compile(r'^(\s*"voice_id"\s*:\s*)"[^"]*"', re.M).search(text, tts_at)
    if not m:
        raise ValueError("persona.tts.voice_id not found in the config")
    new = text[:m.start()] + m.group(1) + json.dumps(voice_id) + text[m.end():]
    n = 1
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
            save_voice_id(args.config, vid, getattr(args, "lang", "pl"))
            sp.voice_id = vid
            print(f"voice: {name} ({vid}) saved to {args.config}")
        if getattr(args, "model", None):
            sp.model = args.model
        sp.delivery = t.get("delivery") or {}
        tags = [x for x in str(getattr(args, "tags", "") or "").split() if x] or None
        if getattr(args, "audition", False):
            line = args.test or ("Let go… I've got it. Good girl." if getattr(args, "lang", "pl") == "en" else "Odpuść… ja to trzymam. Dobra dziewczynka.")
            presets = [args.preset] if args.preset else ["whisper", "tender", "tease", "sharp"]
            for entry in [v for v in t.get("voices", []) if v.get("language", "pl") == getattr(args, "lang", "pl")]:
                vid = entry.get("voice_id") or next((v for v, n, _ in voices if n.lower() == entry.get("name", "").lower()), "")
                if not vid:
                    print(f"  {entry.get('name')}: no voice_id on this account, skipped")
                    continue
                sp.voice_id = vid
                for preset in presets:
                    print(f"  {entry.get('name')} / {preset} / {sp.model}")
                    try:
                        play_stream(sp.stream(line, 7, None, preset, tags), PCM_RATE)
                    except TtsError as e:
                        print(f"    failed: {e}")
            return 0
        if args.test:
            if not sp.voice_id:
                return "voices: no voice_id yet (use --use <id|name>)"
            play_stream(sp.stream(args.test, 7, {"tone": "czuły", "kind": "narration", "stage": 2}, args.preset, tags), PCM_RATE)
            return 0
    except TtsError as e:
        return f"voices: {e}"
    if not args.use:
        print("Pick one with: python voice_control.py voices --use <id|name>   (listen first: open preview_url)")
        for vid, name, labels in voices:
            labels = {k: v for k, v in labels.items() if k not in ("public_owner_id", "library")}
            print(f"  {vid}  {name}  {', '.join(f'{k}={v}' for k, v in labels.items())}")
    return 0


def build_pool(args, pc, persona, log_path, lang="pl"):
    """the never-repeating narration pool (lines_pool.json + said.jsonl next to the log), filled by the persona model;
    pc is the persona config already merged for that language (its own pool file, the said history is shared)"""
    n = pc.get("narration", {})
    p = n.get("pool", {})
    if persona is None or not n.get("enabled") or not p.get("enabled", True) or getattr(args, "no_narration", False):
        return None
    from lines import LinePool
    folder = os.path.dirname(os.path.abspath(args.config))
    examples, max_words = list(n.get("examples", [])), int(n.get("max_words", 16))
    style, rule = str(n.get("style", "")), str(pc.get("language_rule", ""))
    pool = LinePool(lambda theme, avoid, count: persona.fresh_lines(theme, examples, avoid, count, max_words, style=style, language_rule=rule),
                    pool_path=None if args.no_log else os.path.join(folder, "lines_pool.json" if lang == "pl" else f"lines_pool.{lang}.json"),
                    said_path=None if args.no_log else os.path.join(folder, "said.jsonl"),
                    min_pool=p.get("min_pool", 8), batch=p.get("batch", 20), max_said=p.get("max_said", 2000),
                    similarity=p.get("similarity", 0.8), max_words=max_words, seed=p.get("seed"), banned_words=p.get("banned_words"))
    return pool


def build_conversation(args, cfg, controller, stt, voice, speakers, muted, gate, headphones, barge_in, process, rate):
    """the conversation modes (classic / live / agents) and the F7 switch between them"""
    from audio import listen_segments, play_stream
    from conversation import ClassicLoop, ConversationManager, LiveLoop
    a, lis = cfg.get("audio", {}), cfg.get("listen", {})
    conv = cfg.get("conversation", {})
    muted_fn = lambda: muted["value"] or (not headphones and gate.speaking())

    def listen(stop):
        return listen_segments(rate, a.get("device"), stop=stop, muted=muted_fn, on_speech_start=barge_in,
                               threshold_db=lis.get("threshold_db"), margin_db=lis.get("margin_db", 12),
                               end_silence=lis.get("end_silence", 0.7), pre_roll=lis.get("pre_roll", 0.3),
                               min_seconds=lis.get("min_seconds", 0.5), max_seconds=lis.get("max_seconds", 6.0))
    loops = {"classic": ClassicLoop(listen, process)}

    def mic(sample_rate, on_block, stop):
        import queue as _q
        import sounddevice as sd
        blocks = _q.Queue()
        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", device=a.get("device"),
                            blocksize=int(sample_rate * 0.04), callback=lambda indata, frames, t, status: blocks.put(bytes(indata))):
            while not stop():
                try:
                    on_block(blocks.get(timeout=0.2))
                except _q.Empty:
                    continue

    live = conv.get("live", {})
    from realtime_stt import RealtimeStt
    if RealtimeStt.available():
        s = cfg.get("stt", {})
        rt = RealtimeStt(os.environ.get(s.get("api_key_env", "OPENAI_API_KEY"), ""), live.get("stt_model", "gpt-4o-mini-transcribe"),
                         controller.language, stt.prompt, silence_ms=live.get("silence_ms", 500), threshold=live.get("vad_threshold", 0.5),
                         api=live.get("api", "ga"), url=live.get("url", "wss://api.openai.com/v1/realtime?intent=transcription"))
        from audio import SegmentDetector
        from stt import pcm_to_wav
        detector = SegmentDetector(sample_rate=24000, threshold_db=lis.get("threshold_db"), margin_db=lis.get("margin_db", 12),
                                   end_silence=lis.get("end_silence", 0.7), pre_roll=lis.get("pre_roll", 0.3),
                                   min_seconds=lis.get("min_seconds", 0.5), max_seconds=lis.get("max_seconds", 6.0))
        ws_speaker = None
        if "elevenlabs" in speakers:
            from tts_ws import ElevenLabsWsSpeaker
            ws_speaker = ElevenLabsWsSpeaker(speakers["elevenlabs"])
        loops["live"] = LiveLoop(controller, rt, mic, voice, ws_speaker, fallback_transcribe=lambda pcm: stt.transcribe_wav(pcm_to_wav(pcm, 24000)),
                                 detector=detector, headphones=headphones, muted=muted_fn, speculative=live.get("speculative", True),
                                 interrupt_on_speech=live.get("interrupt_on_speech", True))
        old_on_language = controller.on_language

        def on_language(lang):
            if old_on_language:
                old_on_language(lang)
            rt.set_language(lang, stt.prompt)
        controller.on_language = on_language
    else:
        print("  live mode needs websocket-client (pip install websocket-client); only classic is available")

    ag = conv.get("agents", {})
    if ag.get("agent_id") and RealtimeStt.available() and controller.persona is not None:
        from agents import AgentsLoop, ChunkPlayer, LlmProxy
        proxy = LlmProxy(controller, controller.persona, port=int(ag.get("proxy_port", 8787)))
        port = proxy.start()
        print(f"  agents: custom LLM proxy on http://127.0.0.1:{port}/v1 - expose it (e.g. cloudflared tunnel --url http://localhost:{port})"
              f" and put the public URL + /v1 into the agent's Custom LLM settings")
        player = ChunkPlayer(play_stream, rate=int(ag.get("output_rate", 16000)))
        gaps = controller.persona_cfg.get("narration", {}).get("gap_seconds", [[60, 120]])

        def gap():
            lo, hi = gaps[max(0, min(controller.stage(), len(gaps) - 1))]
            return controller.rng.uniform(float(lo), float(hi))
        overrides = {"agent": {"prompt": {"prompt": controller.persona.character}, "language": controller.language}} if ag.get("override_prompt") else {}
        loops["agents"] = AgentsLoop(controller, os.environ.get("ELEVENLABS_API_KEY", ""), ag["agent_id"], mic, player,
                                     rate_in=int(ag.get("input_rate", 16000)), rate_out=int(ag.get("output_rate", 16000)),
                                     overrides=overrides, narration_gap=gap)

    def on_switch(mode):
        controller.external_voice = (mode == "agents")
        if mode != "agents":
            controller.say_now({"classic": "Klasycznie." if controller.language == "pl" else "Classic mode.",
                                "live": "Teraz na żywo." if controller.language == "pl" else "Live now."}[mode])
    start_mode = getattr(args, "mode", None) or conv.get("mode", "classic")
    if start_mode not in loops:
        print(f"  mode {start_mode} not available, starting classic")
        start_mode = "classic"
    m = ConversationManager(loops, start_mode, on_switch=on_switch)
    controller.external_voice = (start_mode == "agents")
    return m


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
    by_lang = {"pl": wake.get("words", [])}
    for k, v in wake.items():
        if k.startswith("words_") and isinstance(v, list):
            by_lang[k[len("words_"):]] = v
    if not wake.get("required", True):
        by_lang = {k: [] for k in by_lang}
    controller = Controller(intents, executor.run_steps, dry_run=args.dry_run, log_path=log_path,
                            wake_words=wake_words, wake_min_similarity=wake.get("min_similarity", 0.8), llm=llm, llm_cfg=lc,
                            lock_cfg=cfg.get("lock"), persona=persona, persona_cfg=pc, wake_words_by_lang=by_lang)
    controller.speak = lambda line: print(f"  Pani: {line}")
    controller.sides_enabled = bool(cfg.get("devices", {}).get("sides", True))
    from controller import deep_merge
    languages = ["pl"] + [k for k in (pc.get("languages") or {}) if k != "pl"]
    for lang in languages:
        merged = pc if lang == "pl" else deep_merge(pc, pc["languages"][lang])
        controller.pools[lang] = build_pool(args, merged, persona, log_path, lang)
    controller.pool = controller.pools.get("pl")
    s = cfg.get("stt", {})
    stt = CloudStt(s.get("base_url", "https://api.openai.com/v1"), s.get("model", "whisper-1"), s.get("language", "pl"),
                   api_key_env=s.get("api_key_env", "OPENAI_API_KEY"), timeout=s.get("timeout", 15), prompt=stt_prompt(intents, wake_words))

    def on_language(lang):
        # speech recognition follows her language (the user speaks it too); the voice is switched by the main loop
        stt.language = lang
        stt.prompt = stt_prompt(controller.intents, controller.wake_words)
        if getattr(controller, "on_language_voice", None):
            controller.on_language_voice(lang)
    controller.on_language = on_language
    start = getattr(args, "language", None) or cfg.get("language", "pl")
    if start != "pl":
        controller.set_language(start)
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
        parser.add_argument("--language", choices=["pl", "en"], help="start in this language (default: 'language' in the config)", **d)
        parser.add_argument("--mode", choices=["classic", "live", "agents"], help="conversation mode (default: conversation.mode in the config)", **d)

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
    v.add_argument("--lang", choices=["pl", "en"], default="pl", help="which language's voice_id --use writes (default pl)")
    v.add_argument("--preset", help="delivery preset for --test / --audition: whisper, tender, tease, sharp, afterglow")
    v.add_argument("--model", help="ElevenLabs model for --test / --audition (e.g. eleven_v3, eleven_multilingual_v2)")
    v.add_argument("--tags", help="audio tags in front of the text for eleven_v3, e.g. \"[whispers]\"")
    v.add_argument("--audition", action="store_true", help="play the same line with every listed voice and preset, one after another")
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
        from stt import echoes_prompt, is_hallucination
        if is_hallucination(text, lis.get("max_words", 25)) or echoes_prompt(text, stt.prompt):
            print(f'  "{text[:60]}..." -> ignored (speech-to-text hallucination)')
            return
        # with headphones the microphone stays on while she speaks, and the headset's open speakers leak her voice into
        # it ("Dobra dziewczynka" came back as "dobra, dziewczyny"): what started while she was talking counts only
        # when it holds the safe word, starts with the wake word ("kochanie, ...") or is a short answer like "proszę"
        # that she is not saying herself
        her_lines = [line for at, line in recent_lines if time.monotonic() - at < 30]
        over_her_ok = (controller.mentions_safe_word(text) or controller.addressed_to_her(text)
                       or controller.short_answer_over_her(text, her_lines, lis.get("over_her_words", OVER_HER_WORDS)))
        if heard_over_her["value"] and not over_her_ok:
            print(f'  "{text[:60]}" -> ignored (started while she was speaking: her own voice)')
            return
        outcome = controller.handle(text)
        report(outcome, text)
        beep({"executed": "ok", "dry-run": "ok", "confirm": "confirm"}.get(outcome.status, "ignored"))

    muted = {"value": False}
    heard_over_her = {"value": False}   # the utterance being recorded started while she was speaking
    recent_lines = collections.deque(maxlen=6)   # (time, line) of her last lines, to tell her echo from an answer

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
            # no setup: the voices of the list (Hanna, Jagna, ... for Polish) are tried on the first line, a library
            # voice is added to the account when needed, the first one that speaks is kept and written to the config
            lang = controller.language
            el.candidates = [v for v in tcfg.get("voices", []) if str(v.get("language", "pl")) == lang and v.get("voice_id")]
            el.prefer = tcfg.get("voice_prefer", {})
            el.language = (controller.persona_cfg.get("tts", {}) or {}).get("language", lang)

            def remember(vid, name, lang=lang):
                if name and name != "configured":
                    print(f"  voice: {name} ({vid})")
                try:
                    save_voice_id(args.config, vid, controller.language)
                except (OSError, ValueError):
                    pass
            el.on_voice = remember
            el.delivery = tcfg.get("delivery") or {}
            if not el.api_key:
                print("  voice: ELEVENLABS_API_KEY is not set - the OpenAI voice is used (set the key and restart for ElevenLabs)")
                out["_elevenlabs"] = el          # kept for the F9/F10 handlers, not used to speak
            else:
                out["elevenlabs"] = el
        o = tcfg.get("openai", {})
        out["openai"] = Speaker(o.get("base_url", "https://api.openai.com/v1"), o.get("model", "gpt-4o-mini-tts"), o.get("voice", "coral"),
                                o.get("instructions", ""), api_key_env=o.get("api_key_env", "OPENAI_API_KEY"), speed=o.get("speed", 1.0))
        return out

    speakers = build_speakers() if controller.persona is not None and not args.no_tts else {}

    def tts_now():
        return controller.persona_cfg.get("tts", {})       # merged for the current language

    def provider(name):
        if name == "elevenlabs":
            return lambda line, stage, meta=None: speakers["elevenlabs"].stream_auto(line, stage, meta)

        def openai_stream(line, stage):
            o = tts_now().get("openai", {})
            speakers["openai"].instructions = o.get("instructions", speakers["openai"].instructions)
            return stream_openai(speakers["openai"], line, stage, o.get("instructions_soft"), o.get("instructions_sharp"))
        return openai_stream

    # ---- the voice list (F9) and the language (F10) -----------------------------------------------------------
    chosen = {}                                             # language -> voice_id in use

    def voices_for(lang):
        return [v for v in tcfg.get("voices", []) if str(v.get("language", "pl")) == lang]

    def resolve_voice(entry):
        """-> voice_id of a voices-list entry: as given, else by name on the account, else from the library"""
        el = speakers.get("elevenlabs") or speakers.get("_elevenlabs")
        if entry.get("voice_id") or el is None:
            return entry.get("voice_id", "")
        try:
            picked = el.pick_voice({"names": [entry.get("name", "")], "ids": [entry] if entry.get("public_owner_id") else [],
                                    "allow_library": False})
            return picked[0] if picked else ""
        except TtsError:
            return ""

    def use_voice(lang, voice_id, announce):
        el = speakers.get("elevenlabs") or speakers.get("_elevenlabs")
        if el is None or not voice_id:
            return
        if "elevenlabs" not in speakers and el.api_key:
            speakers["elevenlabs"] = el       # a voice was chosen by key: ElevenLabs speaks from now on
            voice.providers["elevenlabs"] = provider("elevenlabs")
            voice.order = [n for n in order if n in voice.providers]
        el.voice_id, el.language = voice_id, tts_now().get("language", lang)
        el.verified = False
        chosen[lang] = voice_id
        try:
            save_voice_id(args.config, voice_id, lang)
        except (OSError, ValueError) as e:
            print(f"  voice: could not save voice_id: {e}")
        if announce:
            controller.say_now(announce)

    rotation = {"last": time.monotonic(), "used": []}

    def next_voice():
        lang = controller.language
        lst = voices_for(lang)
        if not lst:
            print(f"  voice: no voices listed for {lang} (persona.tts.voices)")
            return
        ids = [resolve_voice(v) for v in lst]
        el0 = speakers.get("elevenlabs") or speakers.get("_elevenlabs")
        current = chosen.get(lang) or (el0.voice_id if el0 is not None else "")
        i = (ids.index(current) + 1) % len(ids) if current in ids else 0
        if not ids[i]:
            print(f"  voice: {lst[i].get('name')} could not be resolved")
            return
        print(f"  voice: {lst[i].get('name')} ({ids[i]})")
        use_voice(lang, ids[i], handover_line(lang))
        rotation["last"] = time.monotonic()

    def handover_line(lang):
        """what the new voice says first: "Teraz ja się tobą zajmę, misiaczku." - a line and a name, drawn, never twice"""
        t = tts_now()
        lines = t.get("handover_lines") or (["This is my voice now."] if lang == "en" else ["Teraz brzmię tak."])
        names = controller.persona_cfg.get("address", {}).get("names") or ["mała"]
        soft = [n for n in names if n not in ("szmato", "dziwko", "slut", "whore")] or names
        fresh = [l for l in lines if l not in rotation["used"]] or lines
        line = controller.rng.choice(fresh)
        rotation["used"].append(line)
        del rotation["used"][:-max(1, len(lines) - 1)]
        return line.format(name=controller.rng.choice(soft))

    def on_language_voice(lang):
        t = tts_now()
        el1 = speakers.get("elevenlabs") or speakers.get("_elevenlabs")
        if el1 is not None:
            el1.candidates = [v for v in tcfg.get("voices", []) if str(v.get("language", "pl")) == lang and v.get("voice_id")]
            el1.prefer = t.get("voice_prefer", {})
            el1.verified = False
            # the voice of that language: the one used before, else the configured one, else the list decides on the
            # first line (stream_auto) - nothing to choose by hand
            el1.voice_id = chosen.get(lang) or t.get("voice_id", "")
            el1.language = t.get("language", lang)
        print(f"  language: {lang}")
        controller.say_now("English now, darling. Say what you want." if lang == "en" else "Teraz po polsku, kochanie.")
    controller.on_language_voice = on_language_voice

    def toggle_language():
        controller.set_language("en" if controller.language == "pl" else "pl")

    manager = {"m": None}

    def on_special(target):
        if target == "@voice":
            next_voice()
        elif target == "@lang":
            toggle_language()
        elif target == "@mode" and manager["m"] is not None:
            manager["m"].next()

    order = [tcfg.get("provider", "elevenlabs"), tcfg.get("fallback_provider", "openai")]
    voice = Voice({n: provider(n) for n in speakers if not n.startswith("_")}, order, lambda stream, stop: play_stream(stream, PCM_RATE, stop=stop),
                  gate=gate, muted=lambda: muted["value"], cut_previous=bool(lis.get("cut_previous_line", False)))

    def speak(line, stage=0, meta=None):
        pcm = controller.audio_cache.get(line) if (controller.audio_cache is not None and meta and meta.get("kind") == "narration") else None
        print(f"  Pani [{stage}{' cache' if pcm else ''}]: {line}")
        recent_lines.append((time.monotonic(), line))
        voice.say(line, stage, meta, pcm=pcm)
        if pcm and controller.pool is not None:
            controller.audio_cache.warm(controller.pool, controller.persona_cfg.get("narration", {}).get("themes", []))

    controller.speak_ex = speak

    # the narration cache: pool lines synthesized ahead with the expressive model (v3 + audio tags), v2 as a fallback
    ac = tcfg.get("audio_cache", {})
    if "elevenlabs" in speakers and ac.get("enabled", True) and controller.pool is not None and not args.no_log:
        from lines import AudioCache
        el = speakers["elevenlabs"]
        expressive = ElevenLabsSpeaker(el.voice_id, tcfg.get("narration_model", "eleven_v3"), base_url=el.base_url, timeout=max(el.timeout, 30),
                                       stage_settings=el.stage_settings, language=el.language)
        expressive.delivery = el.delivery

        def synth(line, meta):
            expressive.voice_id, expressive.language = el.voice_id, el.language
            try:
                return expressive.synthesize(line, meta)
            except TtsError:
                return el.synthesize(line, meta)              # the expressive model refused / failed: the live model
        controller.audio_cache = AudioCache(os.path.join(os.path.dirname(os.path.abspath(args.config)), "audio_cache"), synth,
                                            meta_for=lambda line, theme: {"tone": "ostry" if controller.stage() >= 3 else "czuły",
                                                                          "kind": "narration", "stage": controller.stage(), "theme": theme},
                                            prefetch=ac.get("prefetch", 6), max_bytes=int(ac.get("max_mb", 200)) * 1024 * 1024)
        controller.audio_cache.prune()
    controller.busy = lambda: muted["value"] or voice.speaking() or gate.speaking()

    def barge_in():
        heard_over_her["value"] = headphones and lis.get("echo_filter", True) and (gate.speaking() or voice.speaking())
        # your voice cuts her off only with listen.barge_in "always"; by default ("safe_word") only the safe word does
        if headphones and lis.get("barge_in", "safe_word") == "always":
            voice.interrupt()
    controller.on_halt = voice.interrupt

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
            Hotkeys(keys, on_key, toggle_mute, on_special)
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
                if controller.audio_cache is not None:
                    controller.audio_cache.warm(controller.pool, controller.persona_cfg.get("narration", {}).get("themes", []))
            def nudger():
                while True:
                    time.sleep(1.0)
                    try:
                        controller.tick()            # her program (e.g. a new mode every 3 minutes)
                        every = float(tts_now().get("rotate_minutes", 0) or 0) * 60
                        if every and time.monotonic() - rotation["last"] >= every and controller.persona_active() \
                                and controller._session_on(controller.clock()) and not controller.busy():
                            next_voice()             # another woman takes over, every rotate_minutes
                        if manager["m"] is not None and manager["m"].mode == "agents":
                            continue                 # the agent speaks for her: narration goes through its proxy
                        if not args.no_narration:
                            controller.idle_narration()
                        if not args.no_nudge:
                            controller.idle_nudge()
                    except Exception:
                        pass
            threading.Thread(target=nudger, daemon=True).start()
        manager["m"] = build_conversation(args, cfg, controller, stt, voice, speakers, muted, gate, headphones, barge_in, process, rate)
        manager["m"].start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            manager["m"].current.stop() if manager["m"].current else None
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
