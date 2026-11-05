"""How a line is read: delivery presets by kind and tone, text shaping (breaks for v2, audio tags for v3),
the narration audio cache, the audition command."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lines import AudioCache, LinePool  # noqa: E402
from speech import Voice  # noqa: E402
from tts import ElevenLabsSpeaker, choose_delivery, shape_text  # noqa: E402
from test_manners import CFG, INTENTS, P, Server  # noqa: E402

D = P["tts"]["delivery"]


class Shaping(unittest.TestCase):
    def test_v2_gets_breaks_v3_gets_tags(self):
        line = "Odpuść… ja to trzymam. Dobra dziewczynka."
        v2 = shape_text(line, "eleven_multilingual_v2", D)
        self.assertEqual(v2, 'Odpuść… <break time="0.45s" /> ja to trzymam. <break time="0.25s" /> Dobra dziewczynka.')
        v3 = shape_text(line, "eleven_v3", D, ["[whispers]"])
        self.assertEqual(v3, "[whispers] " + line)
        self.assertNotIn("<break", v3)

    def test_no_break_at_the_end_and_dots_become_an_ellipsis(self):
        self.assertEqual(shape_text("Odpuść...", "eleven_multilingual_v2", D), "Odpuść…")
        self.assertEqual(shape_text("Tak.", "eleven_multilingual_v2", D), "Tak.")
        self.assertEqual(shape_text("a… b", "x", dict(D, text_shaping=False)), "a… b")

    def test_presets_by_kind_then_by_tone_and_stage(self):
        self.assertEqual(choose_delivery(D, {"kind": "task", "tone": "czuły", "stage": 0})[0], "sharp")
        self.assertEqual(choose_delivery(D, {"kind": "affirmation", "tone": "ostry", "stage": 4})[0], "whisper")
        self.assertEqual(choose_delivery(D, {"kind": "aftercare"})[0], "afterglow")
        self.assertEqual(choose_delivery(D, {"kind": "narration", "tone": "czuły", "stage": 1})[0], "whisper")
        self.assertEqual(choose_delivery(D, {"kind": "answer", "tone": "czuły", "stage": 4})[0], "tender")
        self.assertEqual(choose_delivery(D, {"kind": "answer", "tone": "ostry", "stage": 1})[0], "tease")
        name, settings, tags = choose_delivery(D, {"kind": "answer", "tone": "ostry", "stage": 4})
        self.assertEqual((name, tags), ("sharp", []))
        self.assertEqual(set(settings), {"stability", "similarity_boost", "style", "speed", "use_speaker_boost"})


class FakeEleven(BaseHTTPRequestHandler):
    bodies, mode = [], "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeEleven.bodies.append((self.path, body))
        if FakeEleven.mode == "refuse-v3" and body.get("model_id", "").startswith("eleven_v3"):
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"detail":"no"}'); return
        self.send_response(200); self.send_header("Content-Type", "audio/pcm"); self.end_headers()
        self.wfile.write(b"\x01\x00" * 600)

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"voices": [{"voice_id": "j1", "name": "Jessica", "labels": {"gender": "female"}}]}).encode())

    def log_message(self, *a):
        pass


class Speaker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeEleven)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeEleven.bodies, FakeEleven.mode = [], "ok"
        self.sp = ElevenLabsSpeaker("v1", "eleven_multilingual_v2", api_key="k", base_url=self.base, stage_settings=P["tts"]["stage_settings"])
        self.sp.delivery = D

    def test_meta_picks_the_preset_and_shapes_the_text(self):
        b"".join(self.sp.stream("Odpuść… ja to trzymam.", 7, {"tone": "czuły", "kind": "narration", "stage": 1}))
        body = FakeEleven.bodies[-1][1]
        self.assertIn('<break time="0.45s" />', body["text"])
        self.assertEqual(body["voice_settings"]["style"], D["presets"]["whisper"]["style"])
        self.assertEqual(self.sp.last_request["preset"], "whisper")

    def test_without_meta_the_stage_settings_apply(self):
        b"".join(self.sp.stream("Masz.", 7))
        self.assertEqual(FakeEleven.bodies[-1][1]["voice_settings"], P["tts"]["stage_settings"][7])
        self.assertEqual(FakeEleven.bodies[-1][1]["text"], "Masz.")

    def test_v3_gets_tags_and_no_style_speed(self):
        self.sp.model = "eleven_v3"
        b"".join(self.sp.stream("Odpuść… ja to trzymam.", 2, {"tone": "czuły", "kind": "narration", "stage": 0}))
        body = FakeEleven.bodies[-1][1]
        self.assertTrue(body["text"].startswith("[whispers] "))
        self.assertNotIn("style", body["voice_settings"])
        self.assertNotIn("<break", body["text"])

    def test_forced_preset_and_tags(self):
        self.sp.model = "eleven_v3"
        b"".join(self.sp.stream("Klęcz.", 0, None, "sharp", ["[sighs]"]))
        self.assertTrue(FakeEleven.bodies[-1][1]["text"].startswith("[sighs] Klęcz."))
        self.assertEqual(self.sp.last_request["preset"], "sharp")

    def test_synthesize_returns_the_whole_pcm(self):
        pcm = self.sp.synthesize("Masz.", {"tone": "ostry", "kind": "answer", "stage": 4})
        self.assertEqual(len(pcm), 1200)

    def test_audio_cache_warms_from_the_pool_and_falls_back_to_v2(self):
        v3 = ElevenLabsSpeaker("v1", "eleven_v3", api_key="k", base_url=self.base)
        v3.delivery = D
        calls = []

        def synth(line, meta):
            calls.append(meta)
            try:
                return v3.synthesize(line, meta)
            except Exception:
                return self.sp.synthesize(line, meta)
        with tempfile.TemporaryDirectory() as d:
            pool = LinePool(lambda t, a, c: [], os.path.join(d, "p.json"), os.path.join(d, "s.jsonl"), background=False,
                            seed={"A": ["Jedna…", "Dwie.", "Trzy."], "B": ["Cztery."]})
            cache = AudioCache(os.path.join(d, "cache"), synth, meta_for=lambda l, t: {"tone": "czuły", "kind": "narration", "stage": 0, "theme": t},
                               prefetch=2, background=False)
            FakeEleven.mode = "refuse-v3"
            cache.warm(pool, ["A", "B"])
            self.assertEqual(len(calls), 3)                              # 2 of A, 1 of B
            self.assertTrue(cache.has("Jedna…") and cache.has("Dwie.") and cache.has("Cztery."))
            self.assertFalse(cache.has("Trzy."))
            models = [b["model_id"] for _, b in FakeEleven.bodies]
            self.assertIn("eleven_v3", models)                           # tried
            self.assertEqual(models.count("eleven_multilingual_v2"), 3)  # and fell back
            pcm = cache.get("Jedna…")
            self.assertEqual(len(pcm), 1200)
            self.assertFalse(cache.has("Jedna…"))                        # said once, gone
            self.assertIsNone(cache.get("Jedna…"))
            pool.take("A")                                               # "Jedna…" was said
            cache.warm(pool, ["A"])                                      # refills: Trzy. is next
            self.assertTrue(cache.has("Trzy."))


class Playback(unittest.TestCase):
    def test_voice_plays_cached_pcm_without_a_provider(self):
        played, calls = [], []
        v = Voice({"x": lambda line, stage, meta: calls.append(meta) or iter([b"\x01\x00"])}, ["x"],
                  lambda stream, stop: played.append(b"".join(stream)))
        v.say("Masz.", 2, {"kind": "narration"}, pcm=b"\x02\x00" * 10)
        v.wait()
        self.assertEqual(played, [b"\x02\x00" * 10])
        self.assertEqual(calls, [])
        self.assertEqual(v.last_provider, "cache")
        v.say("Masz.", 2, {"kind": "answer", "tone": "ostry"})
        v.wait()
        self.assertEqual(calls, [{"kind": "answer", "tone": "ostry"}])

    def test_old_two_argument_providers_still_work(self):
        played = []
        v = Voice({"x": lambda line, stage: iter([b"\x01\x00"])}, ["x"], lambda stream, stop: played.append(b"".join(stream)))
        v.say("Masz.", 0, {"kind": "answer"})
        v.wait()
        self.assertEqual(played, [b"\x01\x00"])


class Kinds(Server):
    def test_the_controller_tags_the_kind_and_hands_meta_to_the_speaker(self):
        seen = []
        self.c.speak_ex = lambda line, voice, meta: seen.append(meta)
        self.say("kochanie, jestem twoja", request="none")
        self.assertEqual(seen[-1]["kind"], "answer")
        self.assertIn(seen[-1]["tone"], ("czuły", "ostry"))
        self.assertEqual(seen[-1]["language"], "pl")
        self.c.say_now("Powtórz za mną: jestem twoja.")
        self.assertEqual(seen[-1]["kind"], "affirmation")
        self.assertEqual(self.c._kind_of("x", {"source": "task"}), "task")
        self.assertEqual(self.c._kind_of("x", {"source": "pool", "theme": "po sesji - opieka"}), "aftercare")
        self.assertEqual(self.c._kind_of("x", {"source": "pool", "theme": "ściema"}), "narration")
        self.assertEqual(self.c._kind_of("Repeat after me: I am yours.", {"source": "live"}), "affirmation")

    def test_two_argument_speak_ex_still_works(self):
        seen = []
        self.c.speak_ex = lambda line, voice: seen.append((line, voice))
        self.say("kochanie, jestem twoja", request="none")
        self.assertTrue(seen)


class ConfigShape(unittest.TestCase):
    def test_config(self):
        t = P["tts"]
        self.assertEqual(t["narration_model"], "eleven_v3")
        self.assertTrue(t["audio_cache"]["enabled"])
        for name in ("whisper", "tender", "tease", "sharp", "afterglow"):
            self.assertIn(name, D["presets"])
        self.assertEqual(D["by_kind"]["task"], "sharp")


if __name__ == "__main__":
    unittest.main()
