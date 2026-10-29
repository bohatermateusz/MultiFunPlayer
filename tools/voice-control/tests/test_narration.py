"""Her running commentary: explicit lines at random moments while a session is on, whether you talk or not."""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from persona import build_narration_prompt  # noqa: E402
from test_manners import CFG, INTENTS, P, Server  # noqa: E402

N = P["narration"]


class Narration(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)
        self.calls = []

        def narrate(state, history, theme, examples, max_words=16):
            self.calls.append((state, theme, examples, max_words))
            return f"Linia o: {theme[:10]}"
        self.c.persona.narrate = narrate
        self.say("kochanie, jestem twoja", request="none")      # a session is on
        self.said.clear()

    def run_for(self, seconds, step=1):
        lines = []
        for _ in range(int(seconds / step)):
            self.clock.t += step
            line = self.c.idle_narration()
            if line:
                lines.append((self.clock.t, line))
        return lines

    def test_she_comments_within_the_gap_without_being_spoken_to(self):
        lo, hi = N["gap_seconds"][0]
        lines = self.run_for(hi + 5)
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(lines[0][0] - self.clock.t + hi + 5, lo)
        self.assertEqual(self.said[0][0], lines[0][1])

    def test_gaps_are_random_and_bounded(self):
        self.c._heat = 100.0                                     # stage 4: short gaps
        self.c._heat_last = self.clock.t
        self.c.persona_cfg["initiative"]["session_seconds"] = 10_000
        lo, hi = N["gap_seconds"][4]
        lines = self.run_for(600)
        gaps = [b[0] - a[0] for a, b in zip(lines, lines[1:])]
        self.assertGreater(len(gaps), 5)
        self.assertTrue(all(lo - 1 <= g <= hi + 9 for g in gaps), gaps)      # +8 s: never right after her last line
        self.assertGreater(len(set(round(g) for g in gaps)), 2)

    def test_themes_rotate(self):
        self.c.persona_cfg["initiative"]["session_seconds"] = 10_000
        self.c._heat = 100.0
        self.c._heat_last = self.clock.t
        self.run_for(900)
        themes = [t for _, t, _, _ in self.calls]
        self.assertGreater(len(themes), 6)
        for a, b in zip(themes, themes[1:]):
            self.assertNotEqual(a, b)
        self.assertGreater(len(set(themes)), 5)

    def test_sharper_with_the_stage(self):
        self.c.persona_cfg["initiative"]["session_seconds"] = 100_000
        tones = {}
        for stage, heat in ((0, 0.0), (4, 100.0)):
            self.calls.clear()
            self.c._heat, self.c._heat_last = heat, self.clock.t
            self.c.rng = random.Random(3)
            self.run_for(4000)
            tones[stage] = sum(1 for s, *_ in self.calls if s["ton_tury"] == "ostry") / max(1, len(self.calls))
        self.assertLess(tones[0], tones[4])

    def test_nothing_without_a_session_or_when_off(self):
        self.clock.t += 5000                                     # the session is long over
        self.assertEqual(self.run_for(300), [])
        self.say("kochanie, jestem twoja", request="none")
        self.c.persona_cfg["narration"]["enabled"] = False
        self.assertEqual(self.run_for(300), [])

    def test_not_while_she_is_busy(self):
        self.c.busy = lambda: True
        self.assertEqual(self.run_for(400), [])

    def test_the_line_opens_the_reply_window_and_is_remembered(self):
        lines = []
        while not lines:
            lines = self.run_for(1)
        self.assertTrue(self.c.in_reply_window())
        self.assertIn(("assistant", lines[0][1]), self.c._history)

    def test_prompt_carries_theme_examples_and_register(self):
        text = build_narration_prompt("x", {"ton_tury": "ostry", "zwroty_do_niej": ["mała"]}, "temat X", ["Przykład 1."], 12)
        for s in ("temat X", "Przykład 1.", "12 słów", "wulgarnie", "mała", "wyuzdanie"):
            self.assertIn(s, text, s)
        soft = build_narration_prompt("x", {"ton_tury": "czuły"}, "t", [], 16)
        self.assertIn("czuły w poniżeniu", soft)

    def test_config_has_the_users_examples_and_themes(self):
        self.assertTrue(N["enabled"])
        self.assertGreaterEqual(len(N["themes"]), 8)
        self.assertIn("Należysz do nich.", N["examples"])
        self.assertEqual(len(N["gap_seconds"]), 5)
        self.assertTrue(all(lo < hi for lo, hi in N["gap_seconds"]))

    def test_a_failed_call_is_not_retried_in_a_loop(self):
        def boom(*a, **k):
            self.calls.append(a)
            raise RuntimeError("down")
        self.c.persona.narrate = boom
        self.run_for(200)
        self.assertLessEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
