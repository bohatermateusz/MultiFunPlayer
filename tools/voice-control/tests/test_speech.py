"""Provider order and fallback (ElevenLabs refuses -> OpenAI), no double playback, interruption; titles and the wake word 'Pani'."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from persona import Persona, build_nudge_prompt, build_system_prompt  # noqa: E402
from speech import Voice  # noqa: E402
from tts import TtsError  # noqa: E402
from voice_control import load_config  # noqa: E402

CFG, INTENTS = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))


class Recorder:
    """a fake player: collects the chunks of each played stream"""

    def __init__(self):
        self.played = []
        self.logs = []
        self.delay = 0.0

    def play(self, stream, stop):
        got = []
        for chunk in stream:
            if stop():
                break
            got.append(chunk)
            time.sleep(self.delay)
        self.played.append(got)


def refusing(line, stage):
    raise TtsError("HTTP 400: content policy")


def good(tag):
    def open_(line, stage):
        return iter([f"{tag}:{line}:{stage}".encode("utf-8")])
    return open_


def breaking_midway(line, stage):
    def gen():
        yield b"first"
        raise TtsError("stream failed")
    return gen()


class GateSpy:
    def __init__(self):
        self.events = []

    def begin(self):
        self.events.append("begin")

    def end(self):
        self.events.append("end")


class VoiceTests(unittest.TestCase):
    def make(self, providers, order=("elevenlabs", "openai"), **kw):
        self.rec = Recorder()
        self.gate = GateSpy()
        return Voice(providers, list(order), self.rec.play, gate=self.gate, log=self.rec.logs.append, **kw)

    def say(self, voice, line="Poproś mnie.", stage=2):
        voice.say(line, stage)
        voice.wait()

    def test_the_first_provider_speaks_when_it_works(self):
        v = self.make({"elevenlabs": good("eleven"), "openai": good("openai")})
        self.say(v)
        self.assertEqual(self.rec.played, [["eleven:Poproś mnie.:2".encode("utf-8")]])
        self.assertEqual(v.last_provider, "elevenlabs")

    def test_elevenlabs_refuses_so_openai_speaks_the_same_line(self):
        v = self.make({"elevenlabs": refusing, "openai": good("openai")})
        self.say(v, "Nie. Jeszcze nie skończyłam.", 3)
        self.assertEqual(self.rec.played, [["openai:Nie. Jeszcze nie skończyłam.:3".encode("utf-8")]])
        self.assertEqual(v.last_provider, "openai")
        self.assertTrue(any("elevenlabs" in l and "failed" in l for l in self.rec.logs))

    def test_every_kind_of_elevenlabs_failure_falls_back(self):
        for reason in ("no key", "no voice_id", "HTTP 401", "HTTP 429", "HTTP 400: content policy", "empty answer", "request failed: timeout"):
            def failing(line, stage, reason=reason):
                raise TtsError(reason)
            v = self.make({"elevenlabs": failing, "openai": good("openai")})
            self.say(v)
            self.assertEqual(len(self.rec.played), 1, reason)
            self.assertTrue(self.rec.played[0][0].startswith(b"openai:"), reason)

    def test_empty_stream_before_the_first_sound_falls_back(self):
        def empty(line, stage):
            def gen():
                raise TtsError("empty answer")
                yield b""
            return gen()
        v = self.make({"elevenlabs": empty, "openai": good("openai")})
        self.say(v)
        self.assertEqual(self.rec.played, [["openai:Poproś mnie.:2".encode("utf-8")]])

    def test_a_failure_in_the_middle_does_not_repeat_the_line(self):
        v = self.make({"elevenlabs": breaking_midway, "openai": good("openai")})
        self.say(v)
        self.assertEqual(self.rec.played, [])                  # the player raised on the second chunk: nothing repeated
        self.assertFalse(any(b"openai" in c for chunks in self.rec.played for c in chunks))

    def test_both_fail_is_logged_and_the_tool_goes_on(self):
        v = self.make({"elevenlabs": refusing, "openai": refusing})
        self.say(v)
        self.assertEqual(self.rec.played, [])
        self.assertTrue(any("no voice could speak" in l for l in self.rec.logs))
        v.providers["openai"] = good("openai")
        self.say(v)
        self.assertEqual(len(self.rec.played), 1)

    def test_a_provider_that_is_not_configured_is_skipped(self):
        v = self.make({"openai": good("openai")})
        self.say(v)
        self.assertEqual(self.rec.played, [["openai:Poproś mnie.:2".encode("utf-8")]])

    def test_the_gate_is_closed_around_every_line_even_on_failure(self):
        v = self.make({"elevenlabs": refusing, "openai": refusing})
        self.say(v)
        self.assertEqual(self.gate.events, ["begin", "end"])

    def test_muted_is_silent(self):
        v = self.make({"elevenlabs": good("e")}, muted=lambda: True)
        self.say(v)
        self.assertEqual(self.rec.played, [])

    def test_the_user_can_cut_her_off_and_a_new_line_replaces_the_old(self):
        def slow(line, stage):
            return iter([b"a"] * 50)
        v = self.make({"elevenlabs": slow})
        self.rec.delay = 0.02
        v.say("długa kwestia")
        time.sleep(0.1)
        v.interrupt()
        v.wait()
        self.assertLess(len(self.rec.played[0]), 50)
        v.say("pierwsza")
        v.say("druga")
        v.wait()
        self.assertTrue(len(self.rec.played) >= 2)


class AddressTests(unittest.TestCase):
    def test_pani_and_kochanie_both_start_a_command(self):
        runs = []
        c = Controller(INTENTS, runs.append, wake_words=CFG["wake"]["words"], wake_min_similarity=CFG["wake"]["min_similarity"])
        for text in ["Pani, szybciej", "Pani szybciej", "kochanie, szybciej", "Kochanie szybciej"]:
            c._last.clear()
            self.assertEqual(c.handle(text).intent, "faster", text)
        self.assertEqual(c.handle("szybciej").status, "ignored")

    def test_a_distorted_pani_still_reaches_the_model(self):
        c = Controller(INTENTS, lambda s: None, wake_words=CFG["wake"]["words"], llm=object(), llm_cfg=CFG["llm"])
        self.assertTrue(c._llm_candidate("Pani, zrób coś ze mną"))
        self.assertFalse(c._llm_candidate("a nie spuść się"))
        self.assertFalse(c._llm_candidate("nie to"))

    def test_titles_and_names_reach_the_prompt(self):
        c = Controller(INTENTS, lambda s: None, wake_words=CFG["wake"]["words"], persona=Persona("x", api_key="k"), persona_cfg=CFG["persona"])
        state = c._persona_state()
        for title in ("Pani", "kochanie", "moja Pani"):
            self.assertIn(title, state["zwroty_do_ciebie"])
        for name in ("dziewczynko", "kochanie", "mała"):
            self.assertIn(name, state["zwroty_do_niej"])
        prompt = build_system_prompt(CFG["persona"]["character"], INTENTS, [], state)
        self.assertIn("kochanie", prompt.split("ZWROTY")[1][:300])
        self.assertIn("dziewczynko", build_nudge_prompt("x", state))

    def test_the_character_does_not_demand_one_title(self):
        card = CFG["persona"]["character"]
        self.assertNotIn('"Pani" i "proszę"', card)
        self.assertIn("kochanie", card)


if __name__ == "__main__":
    unittest.main()
