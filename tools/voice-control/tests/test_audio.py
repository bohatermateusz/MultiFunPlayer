"""audio.py without a microphone: a fake sounddevice stream and a fake push-to-talk."""
import os
import sys
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

SAMPLE_RATE = 16000


class FakeInputStream:
    """calls the callback with 10 ms blocks of silence until the stream is closed"""
    chunk_calls = 0

    def __init__(self, samplerate, channels, dtype, device, callback, blocksize=None):
        self.samplerate, self.callback, self._stop = samplerate, callback, threading.Event()

    def __enter__(self):
        def pump():
            block = np.zeros((self.samplerate // 100, 1), dtype="int16")
            while not self._stop.is_set():
                self.callback(block, len(block), None, None)
                FakeInputStream.chunk_calls += 1
                time.sleep(0.01)
        threading.Thread(target=pump, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._stop.set()


class FakePtt:
    def __init__(self, hold_seconds):
        self.hold_seconds, self._start = hold_seconds, None

    def is_pressed(self):
        if self._start is None:
            self._start = time.monotonic()
        return time.monotonic() - self._start < self.hold_seconds


class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=FakeInputStream)
        import audio
        cls.audio = audio

    def test_too_short_press_gives_nothing(self):
        pcm = self.audio.record_while_pressed(FakePtt(0.1), SAMPLE_RATE, min_seconds=0.4, max_seconds=8)
        self.assertEqual(pcm, b"")

    def test_normal_press_gives_pcm_of_about_the_right_length(self):
        pcm = self.audio.record_while_pressed(FakePtt(0.6), SAMPLE_RATE, min_seconds=0.4, max_seconds=8)
        seconds = len(pcm) / 2 / SAMPLE_RATE
        self.assertGreater(seconds, 0.3)
        self.assertLess(seconds, 1.5)
        self.assertEqual(len(pcm) % 2, 0)

    def test_recording_is_cut_at_max_seconds(self):
        start = time.monotonic()
        pcm = self.audio.record_while_pressed(FakePtt(5), SAMPLE_RATE, min_seconds=0.1, max_seconds=0.5)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertGreater(len(pcm), 0)
        self.assertLess(len(pcm) / 2 / SAMPLE_RATE, 1.5)

    def test_ptt_without_key_or_gamepad_is_never_pressed(self):
        ptt = self.audio.PushToTalk(keyboard_key=None, gamepad_button=None)
        self.assertFalse(ptt.is_pressed())

    def test_beep_is_silent_and_harmless_off_windows(self):
        for kind in ("ok", "ignored", "error", "confirm", "unknown"):
            self.audio.beep(kind)

    def test_continuous_listening_stops_and_yields_nothing_for_silence(self):
        start = time.monotonic()
        got = list(self.audio.listen_segments(SAMPLE_RATE, stop=lambda: time.monotonic() - start > 0.6))
        self.assertEqual(got, [])
        self.assertLess(time.monotonic() - start, 3.0)

    def test_muted_listening_resets_the_detector(self):
        start = time.monotonic()
        got = list(self.audio.listen_segments(SAMPLE_RATE, stop=lambda: time.monotonic() - start > 0.4, muted=lambda: True))
        self.assertEqual(got, [])

    def test_wav_from_pcm(self):
        from stt import pcm_to_wav
        wav = pcm_to_wav(b"\x00\x00" * SAMPLE_RATE, SAMPLE_RATE)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])
        self.assertEqual(len(wav), 44 + 2 * SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
