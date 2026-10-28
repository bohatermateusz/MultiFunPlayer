"""ElevenLabs streaming, stream playback, escalation, her own initiative (nudges), barge-in and the voices command."""
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from persona import Persona, build_nudge_prompt, build_system_prompt  # noqa: E402
from test_persona import FakePani, Clock, turn  # noqa: E402
from tts import ElevenLabsSpeaker, TtsError, pcm_chunks  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)
HERE = os.path.join(os.path.dirname(__file__), "..")


class FakeEleven(BaseHTTPRequestHandler):
    seen, mode = {}, "ok"

    def do_GET(self):
        FakeEleven.seen = {"path": self.path, "key": self.headers.get("xi-api-key")}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"voices": [{"voice_id": "abc123", "name": "Pani Testowa", "labels": {"gender": "female", "accent": "polish"}}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeEleven.seen = {"path": self.path, "key": self.headers.get("xi-api-key"), "body": body}
        if FakeEleven.mode == "401":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"detail":"bad key"}'); return
        if FakeEleven.mode == "refused":
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"detail":"content policy"}'); return
        if FakeEleven.mode == "empty":
            self.send_response(200); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "audio/pcm"); self.end_headers()
        for _ in range(5):
            self.wfile.write(b"\x01\x00" * 1200); self.wfile.flush(); time.sleep(0.15)
        FakeEleven.finished = time.monotonic()

    def log_message(self, *a):
        pass


class ElevenLabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeEleven)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def speaker(self, **kw):
        return ElevenLabsSpeaker("voiceX", "eleven_flash_v2_5", api_key="k", base_url=self.base, stage_settings=CFG["persona"]["tts"]["stage_settings"], **kw)

    def test_request_shape_and_stage_settings(self):
        FakeEleven.mode = "ok"
        list(self.speaker().stream("Poproś mnie.", stage=3))
        seen = FakeEleven.seen
        self.assertEqual(seen["path"], "/v1/text-to-speech/voiceX/stream?output_format=pcm_24000")
        self.assertEqual(seen["key"], "k")
        self.assertEqual(seen["body"]["text"], "Poproś mnie.")
        self.assertEqual(seen["body"]["model_id"], "eleven_flash_v2_5")
        self.assertEqual(seen["body"]["voice_settings"], CFG["persona"]["tts"]["stage_settings"][3])
        self.assertLess(seen["body"]["voice_settings"]["style"], CFG["persona"]["tts"]["stage_settings"][8]["style"])
        list(self.speaker().stream("x", stage=99))      # out of range = the last stage
        self.assertEqual(FakeEleven.seen["body"]["voice_settings"], CFG["persona"]["tts"]["stage_settings"][-1])
        list(self.speaker().stream("x", stage=7))           # a sharp voice (5-9)
        self.assertEqual(FakeEleven.seen["body"]["voice_settings"], CFG["persona"]["tts"]["stage_settings"][7])

    def test_chunks_arrive_before_the_answer_is_complete(self):
        FakeEleven.mode = "ok"
        start = time.monotonic()
        stream = self.speaker().stream("test")
        first = next(stream)
        self.assertLess(time.monotonic() - start, 0.6)       # the whole answer takes ~0.75 s
        self.assertGreater(len(first), 0)
        total = len(first) + sum(len(c) for c in stream)
        self.assertEqual(total, 5 * 2400)

    def test_errors(self):
        for mode in ("401", "refused"):
            FakeEleven.mode = mode
            with self.assertRaises(TtsError):
                self.speaker().stream("x")
        FakeEleven.mode = "empty"
        with self.assertRaises(TtsError):
            list(self.speaker().stream("x"))
        FakeEleven.mode = "ok"
        with self.assertRaises(TtsError):
            ElevenLabsSpeaker("v", api_key="", api_key_env="NO_SUCH_VARIABLE_X").stream("x")
        with self.assertRaises(TtsError):
            ElevenLabsSpeaker("", api_key="k", base_url=self.base).stream("x")      # no voice_id
        with self.assertRaises(TtsError):
            ElevenLabsSpeaker("v", api_key="k", base_url="http://127.0.0.1:9", timeout=1).stream("x")

    def test_voices_listing_and_the_cli_command(self):
        voices = ElevenLabsSpeaker(api_key="k", base_url=self.base).voices()
        self.assertEqual(voices[0][:2], ("abc123", "Pani Testowa"))
        self.assertEqual(FakeEleven.seen["key"], "k")
        with tempfile.TemporaryDirectory() as d:
            cfg = json.load(open(CONFIG, encoding="utf-8"))
            cfg["persona"]["tts"]["base_url"] = self.base
            path = os.path.join(d, "c.json")
            json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
            env = dict(os.environ, ELEVENLABS_API_KEY="k")
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "voice_control.py"), "voices", "--config", path], capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("abc123", r.stdout)
        self.assertIn("Pani Testowa", r.stdout)

    def test_pcm_chunks_helper(self):
        self.assertEqual(b"".join(pcm_chunks(b"\x00\x01" * 5000)), b"\x00\x01" * 5000)


class FakeOut:
    writes = []

    def __init__(self, samplerate, channels, dtype):
        self.t0 = time.monotonic()

    def __enter__(self):
        FakeOut.writes = []
        return self

    def __exit__(self, *a):
        pass

    def write(self, data):
        FakeOut.writes.append((time.monotonic(), len(data)))


class StreamPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=FakeOut, InputStream=None)
        import importlib
        import audio
        importlib.reload(audio)
        cls.audio = audio

    def test_chunks_are_written_as_they_come_and_odd_bytes_are_carried(self):
        def gen():
            yield b"\x01\x00\x02"          # an odd number of bytes ...
            yield b"\x00\x03\x00"
        self.audio.play_stream(gen(), 24000)
        self.assertEqual([n for _, n in FakeOut.writes], [1, 2])      # ... carried into the next chunk

    def test_stop_ends_playback_and_closes_the_stream(self):
        closed = []

        def gen():
            try:
                for _ in range(100):
                    yield b"\x00\x00" * 100
            finally:
                closed.append(True)
        stream = gen()
        count = {"n": 0}

        def stop():
            count["n"] += 1
            return count["n"] > 3
        self.audio.play_stream(stream, 24000, stop=stop)
        self.assertLess(len(FakeOut.writes), 5)
        self.assertEqual(closed, [True])


class EscalationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakePani)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakePani.table, FakePani.calls, FakePani.mode = {}, [], "ok"
        self.runs, self.said, self.clock = [], [], Clock()
        self.persona = Persona(CFG["persona"]["character"], self.base, "m", api_key="k", timeout=2)
        w = CFG["wake"]
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=w["words"], persona=self.persona,
                            persona_cfg=CFG["persona"], lock_cfg=CFG["lock"], rng=random.Random(1))
        self.c.speak_ex = lambda line, stage: self.said.append((line, stage))

    def say(self, text, **t):
        FakePani.table[text] = turn(**t)
        self.clock.t += 1
        return self.c.handle(text)

    def test_the_session_heats_up_stage_by_stage(self):
        self.assertEqual(self.c.stage(), 0)
        stages = []
        for i in range(14):
            self.say(f"kochanie, proszę nr {i}", request="lube_fire")
            stages.append(self.c.stage())
        self.assertEqual(stages, sorted(stages))               # never goes down while the session lasts
        self.assertGreaterEqual(max(stages), 2)
        self.assertLessEqual(max(stages), 4)
        self.assertTrue(all(voice in range(10) for _, voice in self.said))      # 0-4 tender, 5-9 sharp

    def test_confession_heats_faster_and_the_cap_holds(self):
        self.c.persona_cfg = dict(CFG["persona"], escalation=dict(CFG["persona"]["escalation"], max_stage=1))
        for i in range(20):
            self.say(f"kochanie, jestem Twoja {i}", request="none", confession=True)
        self.assertEqual(self.c.stage(), 1)

    def test_a_long_silence_cools_the_session_down(self):
        for i in range(10):
            self.say(f"kochanie, proszę {i}", request="lube_fire")
        self.assertGreaterEqual(self.c.stage(), 1)
        self.clock.t += CFG["persona"]["escalation"]["heat_reset"] + 1
        self.assertEqual(self.c.stage(), 0)

    def test_stage_and_tone_reach_the_model_and_the_voice(self):
        for i in range(8):
            self.say(f"kochanie, proszę {i}", request="lube_fire")
        state = FakePani.calls[-1]["body"]["messages"][0]["content"]
        stage = self.c.stage()
        self.assertIn(CFG["persona"]["stages"][stage][:30], state)
        self.assertIn('"etap"', state)
        self.assertIn(self.said[-1][1], (stage, stage + 5))

    def test_stages_are_defined_for_every_level_and_get_sharper(self):
        stages = CFG["persona"]["stages"]
        self.assertEqual(len(stages), 5)
        settings = CFG["persona"]["tts"]["stage_settings"]
        self.assertEqual(len(settings), 10)                  # 0-4 tender by stage, 5-9 sharp by stage
        for half in (settings[:5], settings[5:]):
            styles = [x["style"] for x in half]
            self.assertEqual(styles, sorted(styles))
        self.assertLess(max(x["style"] for x in settings[:5]), min(x["style"] for x in settings[5:]) + 0.1)
        self.assertLess(settings[0]["speed"], settings[5]["speed"])
        self.assertEqual(len(CFG["persona"]["escalation"]["tease_chance"]), 5)

    def test_higher_stages_tease_more_often(self):
        chances = CFG["persona"]["escalation"]["tease_chance"]
        self.assertEqual(chances, sorted(chances))
        self.assertEqual(chances[-1], 1.0)

    def test_prompts_carry_the_brevity_and_the_tone(self):
        prompt = build_system_prompt(CFG["persona"]["character"], INTENTS, [], {"etap": 3, "ton": "TON-TEST"})
        self.assertIn("12 słów", prompt)
        self.assertIn("TON-TEST", prompt)
        self.assertIn("etap", build_nudge_prompt("x", {"etap": 2, "ton": "t"}))


class NudgeTests(EscalationTests):
    """she speaks first when you are silent (inherits the setup; the inherited tests are skipped)"""

    def setUp(self):
        super().setUp()
        self.nudges = []
        FakePani.nudge_line = "Czemu milczysz? Poproś mnie."
        self.orig = self.persona.nudge
        self.persona.nudge = lambda state, history: (self.nudges.append((state, history)) or FakePani.nudge_line)

    def start_session(self):
        self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.said.clear()

    def test_no_session_no_nudge(self):
        self.clock.t += 100
        self.assertIsNone(self.c.idle_nudge())

    def test_nudge_after_the_silence_but_not_before(self):
        self.start_session()
        before = len(self.runs)
        self.clock.t += 10
        self.assertIsNone(self.c.idle_nudge())
        self.clock.t += 20
        self.assertEqual(self.c.idle_nudge(), "Czemu milczysz? Poproś mnie.")
        self.assertEqual(self.said[-1][0], "Czemu milczysz? Poproś mnie.")
        self.assertTrue(self.c.in_reply_window())
        self.assertEqual(len(self.runs), before)               # a nudge never runs a command

    def test_limit_then_silence_and_reset_by_the_user(self):
        self.start_session()
        for _ in range(CFG["persona"]["initiative"]["max_nudges"]):
            self.clock.t += 30
            self.assertIsNotNone(self.c.idle_nudge())
        self.clock.t += 30
        self.assertIsNone(self.c.idle_nudge())
        self.say("kochanie, proszę, Pani", request="lube_fire")
        self.clock.t += 30
        self.assertIsNotNone(self.c.idle_nudge())

    def test_not_while_she_is_speaking_or_muted(self):
        self.start_session()
        self.clock.t += 30
        self.c.busy = lambda: True
        self.assertIsNone(self.c.idle_nudge())
        self.c.busy = lambda: False
        self.assertIsNotNone(self.c.idle_nudge())

    def test_off_switches_and_errors(self):
        self.start_session()
        self.clock.t += 30
        self.c.persona_cfg = dict(CFG["persona"], initiative=dict(CFG["persona"]["initiative"], enabled=False))
        self.assertIsNone(self.c.idle_nudge())
        self.c.persona_cfg = dict(CFG["persona"])
        self.persona.nudge = lambda state, history: (_ for _ in ()).throw(RuntimeError("network"))
        self.assertIsNone(self.c.idle_nudge())
        self.c.persona = None
        self.assertIsNone(self.c.idle_nudge())

    def test_a_nudge_is_in_the_history_and_the_log(self):
        with tempfile.TemporaryDirectory() as d:
            self.c.log_path = os.path.join(d, "l.jsonl")
            self.start_session()
            self.clock.t += 30
            self.c.idle_nudge()
            rec = [json.loads(l) for l in open(self.c.log_path, encoding="utf-8")][-1]
            self.assertEqual(rec["transcript"], "[silence]")
            self.assertEqual(rec["nudge"], 1)
        self.assertEqual(self.c._history[-1], ("assistant", "Czemu milczysz? Poproś mnie."))


# the inherited escalation tests do not need to run twice
for _name in [n for n in dir(EscalationTests) if n.startswith("test_")]:
    setattr(NudgeTests, _name, None)


class SpeechStartTests(unittest.TestCase):
    def test_detector_reports_speech_start_for_barge_in(self):
        import numpy as np
        from audio import SegmentDetector
        det = SegmentDetector(calibration_seconds=0.2)
        quiet = (np.random.default_rng(0).standard_normal(320) * 5).astype("int16")
        loud = (np.random.default_rng(1).standard_normal(320) * 6000).astype("int16")
        for _ in range(15):
            det.feed(quiet)
        self.assertFalse(det.in_speech)
        det.feed(loud)
        det.feed(loud)
        self.assertTrue(det.in_speech)

    def test_listen_segments_calls_on_speech_start(self):
        import numpy as np

        class Stream:
            def __init__(self, samplerate, channels, dtype, device, blocksize, callback):
                self.cb = callback

            def __enter__(self):
                def pump():
                    quiet = (np.random.default_rng(0).standard_normal((320, 1)) * 5).astype("int16")
                    loud = (np.random.default_rng(1).standard_normal((320, 1)) * 6000).astype("int16")
                    for i in range(400):
                        self.cb(quiet if i < 120 or i > 160 else loud, 320, None, None)
                        time.sleep(0.002)
                threading.Thread(target=pump, daemon=True).start()
                return self

            def __exit__(self, *a):
                pass
        sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=Stream, OutputStream=FakeOut)
        import importlib
        import audio
        importlib.reload(audio)
        starts, start = [], time.monotonic()
        for pcm in audio.listen_segments(16000, stop=lambda: time.monotonic() - start > 2.5, on_speech_start=lambda: starts.append(1),
                                         calibration_seconds=0.3):
            break
        self.assertEqual(starts, [1])


if __name__ == "__main__":
    unittest.main()
