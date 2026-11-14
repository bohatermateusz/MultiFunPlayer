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
    mine, library, added, speakable, tts_calls = [], [], [], None, []

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        if self.path.startswith("/v1/shared-voices"):
            self.wfile.write(json.dumps({"voices": FakeEleven.library}).encode())
        else:
            self.wfile.write(json.dumps({"voices": FakeEleven.mine}).encode())

    def do_POST(self):
        if "/text-to-speech/" in self.path:
            vid = self.path.split("/text-to-speech/")[1].split("/")[0]
            FakeEleven.tts_calls.append(vid)
            if FakeEleven.speakable is not None and vid not in FakeEleven.speakable:
                self.send_response(404); self.end_headers()
                self.wfile.write(b'{"detail":{"status":"voice_not_found","message":"A voice with that id was not found"}}'); return
            if FakeEleven.speakable == "401":
                pass
            self.send_response(200); self.send_header("Content-Type", "audio/pcm"); self.end_headers()
            self.wfile.write(b"\x01\x00" * 100); return
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
        FakeEleven.mine, FakeEleven.library, FakeEleven.added, FakeEleven.speakable, FakeEleven.tts_calls = [], [], [], None, []
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

    def test_the_users_own_ids_come_before_labels(self):
        FakeEleven.mine = [{"voice_id": "y1", "name": "Zosia", "labels": {"gender": "female", "age": "young"}}]
        prefer = {"names": ["Jessica"], "ids": [{"id": "NacdHGUYR1k3M0FAbAia", "name": "Hanna - Polish soft"},
                                                {"id": "m2VPSbnJZWpLA9r0WX9q", "name": "Jagna", "public_owner_id": "own2"}]}
        self.assertEqual(self.sp.pick_voice(prefer), ("new-m2VPSbnJZWpLA9r0WX9q", "Jagna"))   # Hanna not on the account, Jagna added
        FakeEleven.mine.append({"voice_id": "NacdHGUYR1k3M0FAbAia", "name": "Hanna", "labels": {}})
        self.assertEqual(self.sp.pick_voice(prefer), ("NacdHGUYR1k3M0FAbAia", "Hanna"))

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

    def test_automatic_voice_on_the_first_line_without_any_setup(self):
        sp = ElevenLabsSpeaker("", "eleven_multilingual_v2", api_key="k", base_url=self.base, language="pl")
        sp.candidates = [{"voice_id": "hanna", "name": "Hanna"}, {"voice_id": "jagna", "name": "Jagna", "public_owner_id": "own"},
                         {"voice_id": "asia", "name": "Asia"}]
        saved = []
        sp.on_voice = lambda vid, name: saved.append((vid, name))
        FakeEleven.speakable = {"asia", "new-jagna"}            # Hanna is not reachable, Jagna only after adding it
        pcm = b"".join(sp.stream_auto("Masz.", 2))
        self.assertEqual(len(pcm), 200)
        self.assertEqual(FakeEleven.tts_calls, ["hanna", "jagna", "new-jagna"])
        self.assertEqual(FakeEleven.added, ["/v1/voices/add/own/jagna"])
        self.assertEqual(saved, [("new-jagna", "Jagna")])
        self.assertTrue(sp.verified)
        FakeEleven.tts_calls.clear()
        b"".join(sp.stream_auto("Jeszcze.", 2))                 # remembered: straight to the working voice
        self.assertEqual(FakeEleven.tts_calls, ["new-jagna"])
        self.assertEqual(len(saved), 1)

    def test_last_resort_is_a_pick_from_the_account(self):
        sp = ElevenLabsSpeaker("", "eleven_multilingual_v2", api_key="k", base_url=self.base, language="pl")
        sp.candidates = [{"voice_id": "gone", "name": "Gone"}]
        sp.prefer = {"names": ["Lily"]}
        FakeEleven.mine = [{"voice_id": "l1", "name": "Lily", "labels": {"gender": "female"}}]
        FakeEleven.speakable = {"l1"}
        b"".join(sp.stream_auto("Masz.", 0))
        self.assertEqual(sp.voice_id, "l1")

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
        self.assertIn("Hanna", t["voice_prefer"]["names"])
        self.assertEqual(t["voice_prefer"]["ids"][0]["id"], "NacdHGUYR1k3M0FAbAia")
        n = cfg["persona"]["narration"]
        self.assertGreaterEqual(len(n["themes"]), 30)
        self.assertGreaterEqual(len(n["pool"]["seed"]), 15)
        self.assertTrue(all(th in n["themes"] for th in n["pool"]["seed"]))
        # hand-written seed lines keep their length (the pool limits only the generated ones), but stay speakable
        self.assertTrue(all(len(x.split()) <= 30 and "?" not in x for v in n["pool"]["seed"].values() for x in v))


if __name__ == "__main__":
    unittest.main()
