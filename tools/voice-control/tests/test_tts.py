"""Text to speech against a fake /audio/speech server, playback and the speech gate with a fake sounddevice."""
import os
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeSpeech(BaseHTTPRequestHandler):
    seen = {}
    mode = "ok"

    def do_POST(self):
        import json
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeSpeech.seen = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        if FakeSpeech.mode == "http500":
            self.send_response(500); self.end_headers(); self.wfile.write(b"no"); return
        self.send_response(200); self.send_header("Content-Type", "audio/pcm"); self.end_headers()
        self.wfile.write(b"\x00\x01" * 2400 + b"\x05")   # an odd trailing byte is cut off

    def log_message(self, *a):
        pass


class SpeakerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeSpeech)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_request_and_pcm(self):
        from tts import Speaker
        FakeSpeech.mode = "ok"
        sp = Speaker(self.base, "gpt-4o-mini-tts", "sage", "stanowczo", api_key="secret")
        pcm = sp.synthesize("Poproś mnie.")
        self.assertEqual(len(pcm), 4800)
        body = FakeSpeech.seen["body"]
        self.assertEqual(FakeSpeech.seen["path"], "/v1/audio/speech")
        self.assertEqual(FakeSpeech.seen["auth"], "Bearer secret")
        self.assertEqual((body["voice"], body["response_format"], body["instructions"], body["input"]), ("sage", "pcm", "stanowczo", "Poproś mnie."))

    def test_errors(self):
        from tts import Speaker, TtsError
        FakeSpeech.mode = "http500"
        with self.assertRaises(TtsError):
            Speaker(self.base, api_key="k").synthesize("x")
        FakeSpeech.mode = "ok"
        with self.assertRaises(TtsError):
            Speaker(self.base, api_key="", api_key_env="NO_SUCH_VARIABLE_X").synthesize("x")
        with self.assertRaises(TtsError):
            Speaker("http://127.0.0.1:9/v1", api_key="k", timeout=1).synthesize("x")


class FakeStream:
    active = True


class FakeSd:
    played, stopped = [], 0
    _stream = FakeStream()

    @classmethod
    def play(cls, samples, samplerate):
        cls.played.append((len(samples), samplerate))
        cls._stream.active = True
        threading.Timer(0.1, lambda: setattr(cls._stream, "active", False)).start()

    @classmethod
    def get_stream(cls):
        return cls._stream

    @classmethod
    def stop(cls):
        cls.stopped += 1
        cls._stream.active = False


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=None, play=FakeSd.play, get_stream=FakeSd.get_stream, stop=FakeSd.stop)
        import importlib
        import audio
        importlib.reload(audio)
        cls.audio = audio

    def test_play_pcm_waits_for_the_end(self):
        start = time.monotonic()
        self.audio.play_pcm(b"\x00\x00" * 2400, 24000)
        self.assertEqual(FakeSd.played[-1], (2400, 24000))
        self.assertGreaterEqual(time.monotonic() - start, 0.09)

    def test_play_pcm_can_be_interrupted(self):
        FakeSd.stopped = 0
        self.audio.play_pcm(b"\x00\x00" * 2400, 24000, stop=lambda: True)
        self.assertEqual(FakeSd.stopped, 1)

    def test_empty_pcm_is_a_no_op(self):
        before = len(FakeSd.played)
        self.audio.play_pcm(b"", 24000)
        self.assertEqual(len(FakeSd.played), before)

    def test_speech_gate_covers_the_line_and_the_echo_tail(self):
        gate = self.audio.SpeechGate(tail=0.2)
        self.assertFalse(gate.speaking())
        gate.begin()
        self.assertTrue(gate.speaking())
        gate.end()
        self.assertTrue(gate.speaking())
        time.sleep(0.25)
        self.assertFalse(gate.speaking())


if __name__ == "__main__":
    unittest.main()
