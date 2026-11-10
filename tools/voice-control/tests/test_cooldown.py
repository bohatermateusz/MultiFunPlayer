"""The password 'trąbka uspakajająca' (EN also 'calming trumpet') / Scroll Lock: the machines wind down (MFP Hismith::Cooldown) - never decided by dice; she only
comments it tenderly, her narration stays tender meanwhile and her program does not speed anything up again."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from hotkeys import caption, cheat_sheet  # noqa: E402
from test_manners import CFG, INTENTS, P, Server  # noqa: E402
from test_persona import Clock  # noqa: E402
from test_sides import actions  # noqa: E402

C = P["cooldown"]


class Plain(unittest.TestCase):
    def setUp(self):
        self.runs, self.clock = [], Clock()
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"],
                            wake_words_by_lang={"pl": CFG["wake"]["words"], "en": CFG["wake"]["words_en"]})

    def utter(self, text):
        self.clock.t += 20
        self.c._last.clear()
        return self.c.handle(text)

    def test_the_password_without_the_wake_word(self):
        for text in ["trąbka uspakajająca", "Trąbka uspokajająca.", "trąbko uspakajająca", "kochanie, trąbka uspakajająca",
                     "trąbka uspakajająca proszę"]:
            o = self.utter(text)
            self.assertEqual((o.status, o.intent), ("executed", "cooldown"), text)
            self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown", text)

    def test_the_old_phrases_do_not_run_it(self):
        for text in ["kochanie, uspokój mnie", "kochanie, wycisz", "Pani, tryb uspokajania", "kochanie, powoli kończ",
                     "uspokój mnie", "pomidor", "uspakajająca"]:
            o = self.utter(text)
            self.assertNotEqual(o.intent, "cooldown", text)
        self.assertNotIn("Hismith::Cooldown", actions(self.runs))

    def test_without_a_body_part_it_winds_down_every_machine(self):
        self.utter("kochanie, szybciej od tyłu")
        self.utter("kochanie, szybciej")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Rear::Speed::Faster")      # other commands go on with the last side
        self.utter("trąbka uspakajająca")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown")
        self.utter("kochanie, trąbka uspakajająca")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown")
        self.utter("trąbka uspakajająca do ust")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Front::Cooldown")
        self.utter("kochanie, szybciej od tyłu")
        self.c.handle_partial("t9", "trąbka uspakajająca")                          # live mode too
        self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown")

    def test_live_runs_it_once_from_the_partial_transcript(self):
        o = self.c.handle_partial("t1", "trąbka")                                   # safety first: it stops at once
        self.assertEqual((o.status, o.intent), ("executed", "halt"))
        o = self.c.handle_partial("t1", "trąbka uspakajająca")                      # ... and the password winds down
        self.assertEqual((o.status, o.intent), ("executed", "cooldown"))
        self.assertIsNone(self.c.handle_partial("t1", "trąbka uspakajająca proszę"))
        o = self.c.handle_final("t1", "trąbka uspakajająca proszę")
        self.assertEqual(o.status, "done")
        self.assertEqual(actions(self.runs).count("Hismith::Cooldown"), 1)

    def test_a_bare_trabka_is_still_the_safe_word(self):
        for text in ["trąbka", "trąbka!", "trąbka trąbka", "kochanie, trąbka"]:
            o = self.utter(text)
            self.assertEqual(o.intent, "halt", text)

    def test_calm_and_slower_stay_what_they_were(self):
        self.c.handle("kochanie, spokojnie")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Calm")
        self.clock.t += 20
        self.c.handle("kochanie, zwolnij")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Speed::Slower")

    def test_one_side(self):
        self.c.handle("trąbka uspakajająca od tyłu")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Rear::Cooldown")

    def test_english(self):
        self.c.set_language("en")
        for text in ["calming trumpet", "darling, calming trumpet", "trąbka uspakajająca"]:
            o = self.utter(text)
            self.assertEqual((o.status, o.intent), ("executed", "cooldown"), text)
        self.assertEqual(self.utter("darling, cool me down").intent, "")

    def test_the_key(self):
        self.assertEqual(CFG["keys"]["scroll_lock"], "cooldown")
        self.assertEqual(caption("scroll_lock"), "Scroll Lock")
        self.assertIn("Scroll Lock", cheat_sheet(CFG["keys"], INTENTS))
        o = self.c.press("cooldown")
        self.assertEqual(o.status, "executed")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown")      # a key is for every side

    def test_another_command_or_the_safe_word_ends_it(self):
        self.c.handle("trąbka uspakajająca")
        self.assertTrue(self.c.cooldown_active())
        self.clock.t += 10
        self.c.press("lube_fire")
        self.assertTrue(self.c.cooldown_active())            # the Lube Launcher does not end it
        self.c.press("faster")
        self.assertFalse(self.c.cooldown_active())
        self.c.press("cooldown")
        self.c.handle("trąbka")
        self.assertFalse(self.c.cooldown_active())

    def test_it_lasts_as_configured(self):
        self.c.press("cooldown")
        self.clock.t += C["seconds"] - 1
        self.assertTrue(self.c.cooldown_active())
        self.clock.t += 2
        self.assertFalse(self.c.cooldown_active())


class WithHer(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)

    def test_never_by_dice_even_while_punished(self):
        self.c._penalty_until = self.clock.t + 600
        o = self.say("trąbka uspakajająca", request="calm")
        self.assertEqual((o.status, o.intent), ("executed", "cooldown"))
        self.assertEqual(actions(self.runs)[-1], "Hismith::Cooldown")
        self.assertNotIn("trąbka uspakajająca", [c[0] if isinstance(c, tuple) else c for c in self._asked()])

    def _asked(self):
        from test_persona import FakePani
        return FakePani.calls

    def test_she_comments_it_tenderly(self):
        o = self.say("trąbka uspakajająca")
        self.assertIn(o.extra["say"], C["lines"])
        self.assertEqual((o.extra["kind"], o.extra["tone"]), ("aftercare", "czuły"))
        self.assertEqual(self.said[-1][0], o.extra["say"])
        self.assertTrue(self.c.in_reply_window())

    def test_the_key_also_gets_a_tender_line(self):
        o = self.c.press("cooldown")
        self.assertIn(o.extra["say"], C["lines"])
        self.assertEqual(self.said[-1][0], o.extra["say"])

    def test_no_line_twice_in_a_row(self):
        first = self.say("trąbka uspakajająca").extra["say"]
        self.clock.t += 30
        second = self.say("trąbka uspakajająca").extra["say"]
        self.assertNotEqual(first, second)

    def test_her_turns_are_tender_meanwhile(self):
        self.c._heat, self.c._heat_last = 100.0, self.clock.t            # stage 4: sharp would be likely
        self.say("trąbka uspakajająca")
        tones = {self.c._roll_tone(4)[0] for _ in range(40)}
        self.assertEqual(tones, {"czuły"})
        self.assertTrue(self.c._persona_state()["wyciszanie"])

    def test_the_narration_is_tender(self):
        calls = []
        self.c.persona.narrate = lambda state, history, theme, *a, **k: calls.append((state, theme)) or f"Ciii, oddychaj {len(calls)}."
        self.c.pool = None
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = True
        self.c.persona_cfg["narration"]["tasks"]["share"] = [1.0]
        self.c.persona_cfg["initiative"]["session_seconds"] = 10_000
        self.c._heat, self.c._heat_last = 100.0, self.clock.t
        self.say("kochanie, jestem twoja", request="none")      # a session is on
        self.say("trąbka uspakajająca")
        for _ in range(C["seconds"] - 5):
            self.clock.t += 1
            self.c._heat_last = self.clock.t
            self.c.idle_narration()
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(theme in C["themes"] for _, theme in calls), calls)
        self.assertTrue(all(state["ton_tury"] == "czuły" for state, _ in calls))

    def test_her_program_does_not_go_on(self):
        self.c._program = {"next_at": self.clock.t + 5, "every": 60, "until": 0, "steps": [{"action": "{target}::Hismith::Speed::Faster", "args": []}],
                           "used": ["faster"], "random_modes": False, "then": None, "side": None, "source": "faster"}
        self.say("trąbka uspakajająca")
        self.assertIsNone(self.c._program)
        n = len(self.runs)
        self.clock.t += 120
        self.assertFalse(self.c.tick())
        self.assertEqual(len(self.runs), n)

    def test_english_lines(self):
        self.c.wake_words_by_lang = {"pl": CFG["wake"]["words"], "en": CFG["wake"]["words_en"]}
        self.c.set_language("en")
        o = self.say("calming trumpet")
        self.assertIn(o.extra["say"], P["languages"]["en"]["cooldown"]["lines"])


if __name__ == "__main__":
    unittest.main()
