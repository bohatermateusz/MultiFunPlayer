"""Choosing a girlish female voice when voice_id is empty, the voices command, v3 settings, the OpenAI fallback voice."""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tts import ElevenLabsSpeaker  # noqa: E402
from voice_control import load_config, save_voice_id, voices_command  # noqa: E402

CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "commands.json")


class FakeEleven(BaseHTTPRequestHandler):
    mine, library, added = [], [], []

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        if self.path.startswith("/v1/shared-voices"):
            self.wfile.write(json.dumps({"voices": FakeEleven.library}).encode())
        else:
            self.wfile.write(json.dumps({"voices": FakeEleven.mine}).encode())

    def do_POST(self):
        FakeEleven.added.append(self.path)
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"voice_id": "new-" + self.path.rsplit("/", 1)[1]}).encode())

    def log_message(self, *a):
        pass


class Pick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeEleven)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeEleven.mine, FakeEleven.library, FakeEleven.added = [], [], []
        self.sp = ElevenLabsSpeaker("", "eleven_multilingual_v2", api_key="k", base_url=self.base, language="pl")
        self.prefer = {"names": ["Jessica", "Lily"], "allow_library": True}

    def test_by_name_first(self):
        FakeEleven.mine = [{"voice_id": "m1", "name": "Mike", "labels": {"gender": "male"}},
                           {"voice_id": "l1", "name": "Lily", "labels": {"gender": "female", "age": "young"}},
                           {"voice_id": "j1", "name": "Jessica", "labels": {"gender": "female", "age": "young"}}]
        self.assertEqual(self.sp.pick_voice(self.prefer), ("j1", "Jessica"))
        self.assertEqual(self.sp.voice_id, "j1")

    def test_then_young_female_by_labels_then_any_female(self):
        FakeEleven.mine = [{"voice_id": "m1", "name": "Mike", "labels": {"gender": "male"}},
                           {"voice_id": "o1", "name": "Olga", "labels": {"gender": "female", "age": "old"}},
                           {"voice_id": "y1", "name": "Zosia", "labels": {"gender": "female", "age": "young"}}]
        self.assertEqual(self.sp.pick_voice(self.prefer), ("y1", "Zosia"))
        FakeEleven.mine = FakeEleven.mine[:2]
        self.assertEqual(self.sp.pick_voice(self.prefer), ("o1", "Olga"))

    def test_library_when_the_account_has_no_female_voice(self):
        FakeEleven.mine = [{"voice_id": "m1", "name": "Mike", "labels": {"gender": "male"}}]
        FakeEleven.library = [{"voice_id": "lib1", "name": "Kasia", "gender": "female", "age": "young", "public_owner_id": "own1",
                               "preview_url": "http://x/p.mp3"}]
        self.assertEqual(self.sp.pick_voice(self.prefer), ("new-lib1", "Kasia"))
        self.assertEqual(FakeEleven.added, ["/v1/voices/add/own1/lib1"])
        self.assertIsNone(self.sp.pick_voice({"allow_library": False}) if not FakeEleven.library.clear() else None)

    def test_nothing_without_a_key(self):
        sp = ElevenLabsSpeaker("", api_key="", api_key_env="NO_SUCH_VAR_X", base_url=self.base)
        with self.assertRaises(Exception):
            sp.pick_voice(self.prefer)

    def test_v3_drops_style_and_speed(self):
        sp = ElevenLabsSpeaker("v", "eleven_v3", api_key="k", stage_settings=[{"stability": 0.5, "style": 0.6, "speed": 1.1, "similarity_boost": 0.7}])
        self.assertEqual(sp._settings(0), {"stability": 0.5, "similarity_boost": 0.7})
        sp.model = "eleven_multilingual_v2"
        self.assertIn("style", sp._settings(0))

    def test_save_voice_id_keeps_the_file_otherwise_untouched(self):
        with open(CFG_PATH, encoding="utf-8") as f:
            text = f.read()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "commands.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            save_voice_id(p, "abc-123")
            with open(p, encoding="utf-8") as f:
                new = f.read()
            self.assertIn('"voice_id": "abc-123"', new)
            self.assertEqual(len(new.splitlines()), len(text.splitlines()))
            self.assertEqual(json.loads(new)["persona"]["tts"]["voice_id"], "abc-123")

    def test_voices_command_filters_and_use(self):
        FakeEleven.mine = [{"voice_id": "m1", "name": "Mike", "labels": {"gender": "male"}, "preview_url": "http://x/m.mp3"},
                           {"voice_id": "y1", "name": "Zosia", "labels": {"gender": "female", "age": "young"}}]
        cfg, _ = load_config(CFG_PATH)
        cfg["persona"]["tts"]["base_url"] = self.base
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "commands.json")
            with open(CFG_PATH, encoding="utf-8") as f, open(p, "w", encoding="utf-8") as g:
                g.write(f.read())
            os.environ["ELEVENLABS_API_KEY"] = "k"
            args = types.SimpleNamespace(female=True, young=True, library=False, use="zosia", test=None, config=p)
            self.assertEqual(voices_command(args, cfg), 0)
            self.assertEqual(json.load(open(p, encoding="utf-8"))["persona"]["tts"]["voice_id"], "y1")
            args.use = "mike"
            self.assertIn("no voice", voices_command(args, cfg))

    def test_config_defaults_are_girlish(self):
        cfg, _ = load_config(CFG_PATH)
        t = cfg["persona"]["tts"]
        self.assertEqual(t["model"], "eleven_multilingual_v2")
        self.assertEqual(t["openai"]["voice"], "coral")
        self.assertIn("dziewczęcy", t["openai"]["instructions"])
        self.assertEqual(len(t["stage_settings"]), 10)
        self.assertTrue(all(s["style"] >= 0.35 and s["use_speaker_boost"] for s in t["stage_settings"]))
        self.assertIn("Jessica", t["voice_prefer"]["names"])


if __name__ == "__main__":
    unittest.main()
