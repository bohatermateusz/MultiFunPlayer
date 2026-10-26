import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from executor import Executor  # noqa: E402
from matcher import match, normalize  # noqa: E402
from stt import CloudStt, SttError, pcm_to_wav  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
_, INTENTS = load_config(CONFIG)


def best(text):
    m, reason = match(text, INTENTS)
    return (m.intent.id if m else None), reason


class Normalize(unittest.TestCase):
    def test_polish_letters_digits_punctuation(self):
        self.assertEqual(normalize("Spuść się na MNIE!"), "spusc sie na mnie")
        self.assertEqual(normalize("Łódź, żółć"), "lodz zolc")
        self.assertEqual(normalize("Tryb 3."), "tryb trzy")


class MatchPositive(unittest.TestCase):
    def test_lube(self):
        for t in ["Spuść się na mnie", "spuść się na mnie.", "spusc sie na mnie", "Spuść się na mnie, proszę",
                  "spuść sie na mnei", "Strzel na mnie"]:
            self.assertEqual(best(t)[0], "lube_fire", t)

    def test_repeated_word_and_longer_phrase_with_a_short_one_inside(self):
        self.assertEqual(best("stop stop")[0], "pause")
        self.assertEqual(best("wznów sekwencję")[0], "sequence_release")   # contains the one-word command "wznów"
        self.assertEqual(best("wznów")[0], "play")
        self.assertIsNone(best("wznów proszę")[0])                          # one-word commands must be said alone

    def test_others(self):
        cases = {"Stop.": "pause", "pauza": "pause", "Graj": "play", "zapętl": "loop_on", "Zapętl to!": "loop_on",
                 "koniec pętli": "loop_off", "włącz sekwencję": "sequence_start", "wróć do skryptu": "sequence_stop",
                 "wstrzymaj tryb": "sequence_hold", "wznów sekwencję": "sequence_release", "tryb ręczny": "manual_mode",
                 "tryb automatyczny": "auto_mode", "szybciej": "faster", "wolniej": "slower"}
        for text, intent in cases.items():
            self.assertEqual(best(text)[0], intent, text)

    def test_preset_modes_words_ordinals_digits(self):
        words = ["jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem"]
        ordinals = ["pierwszy", "drugi", "trzeci", "czwarty", "piąty", "szósty", "siódmy", "ósmy"]
        for n in range(1, 9):
            for t in (f"tryb {words[n-1]}", f"Tryb {ordinals[n-1]}.", f"tryb {n}"):
                self.assertEqual(best(t)[0], f"preset_{n}", t)


class MatchNegative(unittest.TestCase):
    """Things that must NOT run anything - dialogue, partial phrases, typical speech-to-text hallucinations."""

    def test_lube_needs_the_whole_phrase(self):
        for t in ["spuść się", "na mnie", "spuść wodę", "spuść się na niego", "spuść mnie", "spuścić się na mnie wczoraj rano"]:
            self.assertNotEqual(best(t)[0], "lube_fire", t)

    def test_noise_dialogue_and_hallucinations(self):
        for t in ["", "mhm", "Dziękuję.", "Napisy stworzone przez społeczność Amara.org", "Dzięki za oglądanie!",
                  "Oh yes baby that feels so good", "no dalej mocniej proszę nie przestawaj teraz",
                  "Komendy: spuść się na mnie, stop, graj"]:
            self.assertIsNone(best(t)[0], t)

    def test_similar_but_wrong_words(self):
        for t in ["top", "stół", "gra", "tryb dziewięć", "tryb dziesięć", "tryb", "szybko"]:
            self.assertIsNone(best(t)[0], t)

    def test_mode_numbers_are_not_mixed_up(self):
        self.assertEqual(best("tryb trzy")[0], "preset_3")
        self.assertEqual(best("tryb cztery")[0], "preset_4")
        self.assertNotEqual(best("tryb siedem")[0], "preset_6")


class FakeClock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


class ControllerTests(unittest.TestCase):
    def make(self, **kw):
        self.runs = []
        self.clock = FakeClock()
        return Controller(INTENTS, self.runs.append, clock=self.clock, **kw)

    def test_executes_and_cooldown(self):
        c = self.make()
        self.assertEqual(c.handle("Spuść się na mnie").status, "executed")
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(c.handle("Spuść się na mnie").status, "cooldown")
        self.assertEqual(len(self.runs), 1)
        self.clock.t += 10.5
        self.assertEqual(c.handle("Spuść się na mnie").status, "executed")
        self.assertEqual(len(self.runs), 2)

    def test_ignored_does_nothing(self):
        c = self.make()
        self.assertEqual(c.handle("dziękuję").status, "ignored")
        self.assertEqual(self.runs, [])

    def test_dry_run_sends_nothing(self):
        c = self.make(dry_run=True)
        self.assertEqual(c.handle("Spuść się na mnie").status, "dry-run")
        self.assertEqual(self.runs, [])

    def test_confirmation_flow(self):
        for i in INTENTS:
            if i.id == "lube_fire":
                i.confirm = True
        try:
            c = self.make()
            self.assertEqual(c.handle("spuść się na mnie").status, "confirm")
            self.assertEqual(self.runs, [])
            self.assertEqual(c.handle("tak").status, "executed")
            self.assertEqual(len(self.runs), 1)
            self.clock.t += 20
            self.assertEqual(c.handle("spuść się na mnie").status, "confirm")
            self.assertEqual(c.handle("nie").status, "cancelled")
            self.assertEqual(c.handle("spuść się na mnie").status, "confirm")
            self.clock.t += 7   # confirmation timed out
            self.assertEqual(c.handle("tak").status, "ignored")
            self.assertEqual(len(self.runs), 1)
        finally:
            for i in INTENTS:
                i.confirm = False

    def test_preset_mode_steps_switch_to_auto_first(self):
        c = self.make()
        c.handle("tryb trzy")
        steps = self.runs[0]
        self.assertEqual([s["action"].split("::")[-3:] for s in steps], [["Hismith", "ManualMode", "Set"], ["Hismith", "PresetMode", "Set"]])
        self.assertEqual(steps[0]["args"], [False])
        self.assertEqual(steps[1]["args"], [3])

    def test_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            c = self.make(log_path=path)
            c.handle("Spuść się na mnie")
            c.handle("dziękuję")
            lines = [json.loads(l) for l in open(path, encoding="utf-8")]
            self.assertEqual([l["status"] for l in lines], ["executed", "ignored"])
            self.assertEqual(lines[0]["intent"], "lube_fire")


class ExecutorTests(unittest.TestCase):
    def test_udp_message(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        srv.settimeout(2)
        ex = Executor("127.0.0.1", srv.getsockname()[1], target="Buttplug.io/0", step_delay=0)
        ex.run_steps([{"action": "{target}::Hismith::PresetMode::Set", "args": [3]}, {"action": "Script::Loop::Start"}])
        first = json.loads(srv.recv(4096))
        second = json.loads(srv.recv(4096))
        srv.close()
        self.assertEqual(first, {"action": "Buttplug.io/0::Hismith::PresetMode::Set", "args": [3]})
        self.assertEqual(second, {"action": "Script::Loop::Start", "args": []})


class FakeOpenAI(BaseHTTPRequestHandler):
    seen = {}

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeOpenAI.seen = {"path": self.path, "auth": self.headers.get("Authorization"), "ctype": self.headers.get("Content-Type"), "body": body}
        if self.headers.get("Authorization") == "Bearer bad":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"nope"}'); return
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"text": " Spuść się na mnie. "}).encode("utf-8"))

    def log_message(self, *a): pass


class SttTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_request_and_answer(self):
        stt = CloudStt(self.base, "whisper-1", "pl", api_key="secret", prompt="Komendy: stop")
        text = stt.transcribe_wav(pcm_to_wav(b"\x00\x00" * 1600))
        self.assertEqual(text, "Spuść się na mnie.")
        seen = FakeOpenAI.seen
        self.assertEqual(seen["path"], "/v1/audio/transcriptions")
        self.assertEqual(seen["auth"], "Bearer secret")
        self.assertTrue(seen["ctype"].startswith("multipart/form-data; boundary="))
        body = seen["body"]
        for needle in [b'name="model"\r\n\r\nwhisper-1', b'name="language"\r\n\r\npl', b'name="temperature"\r\n\r\n0',
                       b'name="prompt"\r\n\r\nKomendy: stop', b'filename="speech.wav"', b"RIFF"]:
            self.assertIn(needle, body)

    def test_errors(self):
        with self.assertRaises(SttError):
            CloudStt(self.base, api_key="bad").transcribe_wav(b"x")
        with self.assertRaises(SttError):
            CloudStt(self.base, api_key="", api_key_env="NO_SUCH_VARIABLE_XYZ").transcribe_wav(b"x")
        with self.assertRaises(SttError):
            CloudStt("http://127.0.0.1:9/v1", api_key="k", timeout=1).transcribe_wav(b"x")


if __name__ == "__main__":
    unittest.main()
