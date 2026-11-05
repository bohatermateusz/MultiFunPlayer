"""Two machines and two Lube Launchers: front (her mouth / face) and rear (from behind / inside). The words choose the
side, else her choice from the context, else the side used last; keys and the safe word reach every side."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from test_manners import CFG, INTENTS, P, M, Server  # noqa: E402
from test_persona import Clock  # noqa: E402


def actions(runs):
    return [st["action"].split("::", 1)[1] for r in runs for st in r]


class Words(unittest.TestCase):
    def setUp(self):
        self.runs, self.clock = [], Clock()
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"])

    def say(self, text):
        self.clock.t += 20
        self.c._last.clear()
        return self.c.handle(text)

    def test_side_of(self):
        self.assertEqual(self.c.side_of("spuść mi się do ust"), "front")
        self.assertEqual(self.c.side_of("spuść mi się na twarz"), "front")
        self.assertEqual(self.c.side_of("ssij mnie"), "front")
        self.assertEqual(self.c.side_of("bierz mnie szybciej od tyłu"), "rear")
        self.assertEqual(self.c.side_of("spuść się do środka"), "rear")
        self.assertEqual(self.c.side_of("zalej mi twarz i dupę"), "both")
        self.assertIsNone(self.c.side_of("szybciej"))
        self.assertEqual(self.c.side_of("cum in my mouth"), "front")
        self.assertEqual(self.c.side_of("fuck me from behind"), "rear")

    def test_the_words_pick_the_machine(self):
        self.say("kochanie, szybciej od tyłu")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Rear::Speed::Faster")
        self.say("kochanie, głębiej w usta")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Front::Depth::Deeper")

    def test_without_words_the_last_side_goes_on(self):
        self.say("kochanie, szybciej od tyłu")
        self.say("kochanie, szybciej")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Rear::Speed::Faster")

    def test_nothing_said_ever_means_every_side(self):
        self.say("kochanie, szybciej")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Speed::Faster")

    def test_the_lube_launcher_of_that_side(self):
        self.say("kochanie, spuść się na mnie do ust")
        self.assertIn("Hismith::Front::LubeLauncher::Fire", actions(self.runs))

    def test_keys_and_the_safe_word_reach_every_side(self):
        self.say("kochanie, szybciej od tyłu")
        self.c.press("faster")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Speed::Faster")
        self.say("trąbka")
        self.assertEqual(actions(self.runs)[-1], "Hismith::Halt")

    def test_off_switch(self):
        steps = [{"action": "{target}::Hismith::Speed::Faster", "args": []}]
        self.assertEqual(self.c._sided(steps, "rear")[0]["action"], "{target}::Hismith::Rear::Speed::Faster")
        self.c.sides_enabled = False
        self.assertEqual(self.c._sided(steps, "rear"), steps)


class HerChoice(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = dict(P, base_p=1.0, base_p_overrides={}, step_p_overrides={},
                                  manners=dict(M, politeness_factor=1.0, ignore_demand_factor=1.0))
        self.c.persona_cfg["last_second"]["enabled"] = False

    def test_she_picks_the_side_from_the_context(self):
        o = self.say("kochanie, proszę, zalej mnie", request="lube_fire", side="front", shots=3)
        self.assertEqual(o.status, "executed")
        self.assertEqual([st["action"] for r in self.runs for st in r], ["{target}::Hismith::Front::LubeLauncher::FireMany"])

    def test_his_words_beat_her_choice(self):
        self.say("kochanie, proszę, spuść się do środka", request="lube_fire", side="front")
        self.assertIn("{target}::Hismith::Rear::LubeLauncher::Fire", [st["action"] for r in self.runs for st in r])


class Config(unittest.TestCase):
    def test_voice_rotation_and_handover_lines(self):
        t = P["tts"]
        self.assertEqual(t["rotate_minutes"], 10)
        self.assertTrue(all("{name}" in l for l in t["handover_lines"]))
        self.assertTrue(any("zajmę" in l for l in t["handover_lines"]))
        self.assertIn("misiaczku", P["address"]["names"])
        self.assertIn("DWAJ", P["identity"]["narzedzia"])


if __name__ == "__main__":
    unittest.main()
