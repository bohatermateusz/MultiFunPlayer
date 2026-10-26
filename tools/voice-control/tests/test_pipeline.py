"""The whole pipeline minus the microphone and the cloud: fake transcripts -> Controller ->
Executor -> a fake MultiFunPlayer UDP listener. Same objects and wiring as voice_control.main()."""
import json
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from executor import Executor  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")


class FakeMultiFunPlayer:
    """collects the UDP messages a real MultiFunPlayer would receive on 127.0.0.1:<port>"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.received, self._stop = [], threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(4096)
                self.received.append(json.loads(data))
            except socket.timeout:
                pass

    def wait(self, count, timeout=2.0):
        import time
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return list(self.received)

    def close(self):
        self._stop.set()
        self.sock.close()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.mfp = FakeMultiFunPlayer()
        cfg, intents = load_config(CONFIG)
        self.executor = Executor("127.0.0.1", self.mfp.port, cfg["target"], step_delay=0)
        self.controller = Controller(intents, self.executor.run_steps)

    def tearDown(self):
        self.mfp.close()

    def heard(self, *transcripts):
        return [self.controller.handle(t).status for t in transcripts]

    def test_lube_command_reaches_multifunplayer_as_the_fire_action(self):
        self.assertEqual(self.heard("Spuść się na mnie."), ["executed"])
        msgs = self.mfp.wait(1)
        self.assertEqual(msgs, [{"action": "Buttplug.io/0::Hismith::LubeLauncher::Fire", "args": []}])

    def test_preset_mode_sends_auto_mode_then_the_mode(self):
        self.assertEqual(self.heard("tryb trzeci"), ["executed"])
        msgs = self.mfp.wait(2)
        self.assertEqual([m["action"].split("::")[-2] for m in msgs], ["ManualMode", "PresetMode"])
        self.assertEqual([m["args"] for m in msgs], [[False], [3]])

    def test_pause_play_and_loop(self):
        self.assertEqual(self.heard("stop", "graj", "zapętl to", "koniec pętli"), ["executed"] * 4)
        msgs = self.mfp.wait(4)
        self.assertEqual([m["action"] for m in msgs],
                         ["Media::PlayPause::Set", "Media::PlayPause::Set", "Script::Loop::Start", "Script::Loop::Clear"])
        self.assertEqual([m["args"] for m in msgs[:2]], [[False], [True]])

    def test_dialogue_and_hallucinations_send_nothing(self):
        statuses = self.heard("Dzięki za oglądanie!", "no dalej mocniej", "Napisy stworzone przez społeczność Amara.org", "")
        self.assertTrue(all(s == "ignored" for s in statuses), statuses)
        self.assertEqual(self.mfp.wait(1, timeout=0.5), [])

    def test_lube_cooldown_blocks_a_quick_repeat(self):
        self.assertEqual(self.heard("spuść się na mnie", "spuść się na mnie"), ["executed", "cooldown"])
        self.assertEqual(len(self.mfp.wait(2, timeout=0.5)), 1)


if __name__ == "__main__":
    unittest.main()
