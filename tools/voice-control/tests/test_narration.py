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
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = False     # the physical orders have their own tests
        self.calls = []

        words = ["klęcz", "ssij", "odpuść", "należysz", "dziękuj", "oddychaj", "patrz", "powtórz", "zostań", "puść",
                 "milcz", "czekaj", "otwórz", "głębiej", "wolniej", "moja", "grzecznie", "teraz", "dalej", "cicho"]

        def narrate(state, history, theme, examples, max_words=16, style="", language_rule=""):
            self.calls.append((state, theme, examples, max_words))
            n = len(self.calls)
            return f"{words[n % 20].capitalize()} {words[(n * 7) % 19]} {words[(n * 13) % 17]} {n}."
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

    def test_a_repeated_live_line_is_retried_then_dropped(self):
        self.c.persona.narrate = lambda *a, **k: "Jesteś moja."
        self.c._remember_said("Jesteś moja.")
        lines = self.run_for(400)
        self.assertEqual(lines, [])
        self.assertIn("Jesteś moja.", self.c._persona_state()["nie_powtarzaj"])

    def test_tasks_are_mixed_in_and_never_repeat(self):
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = True
        self.c.persona_cfg["narration"]["tasks"]["share"] = [1.0] * 5
        self.c.persona_cfg["initiative"]["session_seconds"] = 100_000
        self.c.persona_cfg["escalation"]["heat_reset"] = 10 ** 9
        self.c._heat, self.c._heat_last = 100.0, self.clock.t          # stage 4: everything up to stage 4 is allowed
        lines = [l for _, l in self.run_for(3000)]
        self.assertGreater(len(lines), 10)
        self.assertEqual(self.calls, [])                               # orders come from the list, not the model
        by = P["narration"]["tasks"]["by_stage"]
        orders = {x for v in by.values() for x in v}
        care = set(P["narration"]["tasks"]["care"])
        self.assertTrue(set(lines) <= orders | care)
        given = [l for l in lines if l in orders]
        self.assertEqual(len(given), len(set(given)))                  # an order is never given twice
        self.assertEqual(set(given), orders)                           # ... and all of them come before care repeats

    def test_tasks_fit_the_stage(self):
        self.c.persona_cfg["narration"]["tasks"]["share"] = [1.0] * 5
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = True
        by = P["narration"]["tasks"]["by_stage"]
        for _ in range(4):
            t = self.c._draw_task(0)
            self.assertIn(t, by["0"])
        self.c._tasks_used.clear()
        self.assertIn(self.c._draw_task(2), by["2"] + by["1"] + by["0"])

    def test_pool_lines_come_first(self):
        class FakePool:
            said = []

            def take(self, theme):
                return "Z puli: " + theme[:8]

            def left(self, theme):
                return 7

            def remember(self, line):
                self.said.append(line)

            def is_repeat(self, line):
                return False
        self.c.pool = FakePool()
        lines = self.run_for(200)
        self.assertTrue(lines[0][1].startswith("Z puli"))
        self.assertEqual(self.calls, [])

    def test_a_live_line_naming_the_gear_is_dropped(self):
        self.c.persona.narrate = lambda *a, **k: "Maszyna jedzie na tobie."
        self.assertEqual(self.run_for(400), [])

    def test_she_never_says_suka(self):
        import re
        bad = re.compile(r"\b(suk[aoęiąe]?|suczk\w*|bitch\w*)\b", re.I)
        n = P["narration"]
        texts = [P["character"]] + list(P["address"]["names"]) + [l for v in n["pool"]["seed"].values() for l in v] + n["themes"] + n["examples"]
        texts += [l for cat in P["vocabulary"].values() for v in cat.values() for l in v] + [str(v) for v in P["identity"].values()]
        texts += [l for v in n["tasks"]["by_stage"].values() for l in v]
        en = P["languages"]["en"]
        texts += list(en["address"]["names"]) + [l for v in en["narration"]["pool"]["seed"].values() for l in v] + en["narration"]["themes"]
        texts += [l for cat in en["vocabulary"].values() for v in cat.values() for l in v] + en["narration"]["examples"]
        for t in texts:
            self.assertFalse(bad.search(t), t)
        self.assertTrue(self.c.is_banned("Dobra suka."))
        self.assertTrue(self.c.is_banned("Grzeczna suczko."))
        self.assertFalse(self.c.is_banned("Założyłaś sukienkę. Ładnie."))
        self.c.set_language("en")
        self.assertTrue(self.c.is_banned("You're my bitch."))

    def test_a_forbidden_word_in_her_answer_is_swapped_before_she_says_it(self):
        from lines import scrub
        self.assertEqual(scrub("Jeszcze nie. Poproś ładniej, moja suczko."), ("Jeszcze nie. Poproś ładniej, moja dziewczynko.", True))
        self.assertEqual(scrub("Suka. Dobra suka, z suką i o suce."), ("Dziwka. Dobra dziwka, z dziwką i o suce.", True))
        self.assertEqual(scrub("You're my bitch."), ("You're my slut.", True))
        self.assertEqual(scrub("Założyłaś sukienkę."), ("Założyłaś sukienkę.", False))
        said = []
        self.c.speak_ex = lambda line, voice, meta=None: said.append(line)
        self.say("kochanie, jestem twoja", request="none", granted="Dobra suczko.", denied="Dobra suczko.")
        self.assertEqual(said[-1], "Dobra dziewczynko.")
        self.assertIn(("assistant", "Dobra dziewczynko."), self.c._history)

    def test_anal_themes_in_both_languages(self):
        n, en = P["narration"], P["languages"]["en"]["narration"]
        pl = [t for t in n["themes"] if "odbyt" in t or "dupę" in t or "dupa" in t]
        self.assertGreaterEqual(len(pl), 5)
        self.assertTrue(all(len(n["pool"]["seed"][t]) >= 5 for t in pl))
        self.assertTrue(any("księżniczk" in l for t in pl for l in n["pool"]["seed"][t]))
        self.assertIn("księżniczko odbytu", P["address"]["names"])
        self.assertIn("odbyt", P["vocabulary"])
        self.assertIn("odbyt", P["tone"]["sharp_categories"])
        self.assertGreaterEqual(len([t for t in en["themes"] if "ass" in t]), 4)

    def test_no_seed_or_task_names_the_gear(self):
        import re
        n = P["narration"]
        for lines in list(n["pool"]["seed"].values()) + list(n["tasks"]["by_stage"].values()) + [n["tasks"]["care"], n["examples"]]:
            for l in lines:
                self.assertFalse(re.search(r"maszyn|silnik|dildo|urządzen|launcher|skrypt", l, re.I), l)
        self.assertIn("maszyn", n["pool"]["banned_words"])

    def test_a_failed_call_is_not_retried_in_a_loop(self):
        def boom(*a, **k):
            self.calls.append(a)
            raise RuntimeError("down")
        self.c.persona.narrate = boom
        self.run_for(200)
        self.assertLessEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
