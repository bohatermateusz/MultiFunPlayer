"""A refusal at the last second (lube) and an unasked Lube Launcher shot."""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_manners import CFG, INTENTS, P, Server  # noqa: E402
from test_persona import turn  # noqa: E402


def lube_runs(runs):
    return [r for r in runs if any("LubeLauncher" in st["action"] for st in r)]


class Base(Server):
    def cfg(self, **over):
        c = copy.deepcopy(P)
        c.update(base_p=1.0, base_p_overrides={}, step_p_overrides={}, max_asks=8, difficulty=1.0,
                 manners=dict(P["manners"], politeness_factor=1.0, ignore_demand_factor=1.0))
        for k, v in over.items():
            c[k] = v
        c["spontaneous"]["lube_surprise"]["enabled"] = False
        c["last_second"]["enabled"] = False
        return c

    def heat(self, stage):
        self.c._heat = float(self.c.persona_cfg["escalation"]["thresholds"][stage]) + 0.5
        self.c._heat_last = self.clock.t


class LastSecond(Base):
    def setUp(self):
        super().setUp()
        c = self.cfg()
        c["last_second"].update(enabled=True, chance=1.0)
        self.c.persona_cfg = c
        self.heat(2)

    def ask(self, text="kochanie, proszę, spuść się na mnie", request="lube_fire"):
        self.clock.t += 20
        return self.say(text, request=request)

    def test_first_grant_is_held_back_the_next_goes_through(self):
        o = self.ask()
        self.assertEqual(o.status, "denied")
        self.assertTrue(o.extra.get("last_second"))
        self.assertEqual(lube_runs(self.runs), [])
        self.assertEqual(self.c.penalty_remaining(), 0)
        o = self.ask()
        self.assertEqual(o.status, "executed")
        self.assertEqual(len(lube_runs(self.runs)), 1)

    def test_not_twice_in_a_row_and_asks_do_not_grow(self):
        self.ask()
        self.assertEqual(self.c._asks.get("lube_fire", 0), 0)
        self.assertIn("lube_fire", self.c._retry)

    def test_other_requests_are_not_held_back(self):
        o = self.ask("kochanie, głębiej", request="deeper")
        self.assertNotIn("last_second", o.extra)

    def test_not_in_a_hold_and_not_at_stage_zero(self):
        self.heat(0)
        self.c._heat = 0.0
        o = self.ask()
        self.assertNotIn("last_second", o.extra)

    def test_off_by_config(self):
        self.c.persona_cfg["last_second"]["enabled"] = False
        o = self.ask()
        self.assertEqual(o.status, "executed")

    def test_the_sure_ask_is_never_held_back(self):
        self.c.persona_cfg["max_asks"] = 1
        o = self.ask()
        self.assertNotIn("last_second", o.extra)

    def test_retry_bonus_raises_the_chance(self):
        self.c.persona_cfg = dict(self.c.persona_cfg, base_p=0.2)
        self.c._retry.add("lube_fire")

        class T:
            confession = thanked = False
        ps = [self.c._decide("lube_fire", T())[1]]
        self.c._retry.clear()
        ps.append(self.c._decide("lube_fire", T())[1])
        self.assertGreater(ps[0], ps[1])


class LubeSurprise(Base):
    def setUp(self):
        super().setUp()
        c = self.cfg(spontaneous=copy.deepcopy(P["spontaneous"]))
        c["spontaneous"]["lube_surprise"].update(enabled=True, chance=[1.0] * 5, min_stage=2)
        c["spontaneous"]["turn_chance"] = [0.0] * 5
        self.c.persona_cfg = c
        self.heat(3)

    def chat(self, gap=310):
        self.clock.t += gap
        return self.say("kochanie, jestem twoja", request="none")

    def test_one_unasked_shot_then_the_gap(self):
        o = self.chat()
        self.assertEqual(o.extra.get("unsolicited"), "lube_surprise")
        self.assertEqual(len(lube_runs(self.runs)), 1)
        self.clock.t += 10
        self.say("kochanie, dziękuję", request="none")
        self.assertEqual(len(lube_runs(self.runs)), 1)       # inside lube_gap_seconds

    def test_max_per_window(self):
        for _ in range(6):
            self.chat()
        self.assertEqual(len(lube_runs(self.runs)), 3)

    def test_never_below_min_stage(self):
        self.c._heat = 0.0
        self.chat()
        self.assertEqual(lube_runs(self.runs), [])

    def test_not_while_punished(self):
        self.say("kochanie, nie chcę", request="none")
        self.runs.clear()
        self.chat(gap=5)
        self.assertEqual(lube_runs(self.runs), [])

    def test_off_by_config(self):
        self.c.persona_cfg["spontaneous"]["lube_surprise"]["enabled"] = False
        self.chat()
        self.assertEqual(lube_runs(self.runs), [])

    def test_nothing_else_comes_with_it(self):
        self.chat()
        for r in self.runs:
            self.assertFalse(any("Halt" in st["action"] or "Calm" in st["action"] for st in r))

    def test_frequency_depends_on_stage(self):
        self.c.persona_cfg["spontaneous"]["lube_surprise"]["chance"] = [0.0, 0.0, 0.06, 0.12, 0.2]
        counts = {}
        for stage in (1, 2, 4):
            self.c.rng = random.Random(7)
            self.c._surprises, self.c._last_lube = [], -1e9
            counts[stage] = sum(self.c._lube_surprise_roll(stage) for _ in range(2000)) if False else sum(
                1 for _ in range(2000) if (self.c._surprises.clear() or True) and self.c._lube_surprise_roll(stage))
        self.assertEqual(counts[1], 0)
        self.assertLess(counts[2], counts[4])

    def test_the_nudge_can_shoot(self):
        self.c._last_user = self.clock.t
        self.c._nudges = 0
        self.clock.t += 30
        self.c.persona.nudge = lambda state, history, step_ids=None: "Patrz na mnie."
        line = self.c.idle_nudge()
        self.assertEqual(line, "Patrz na mnie.")
        self.assertEqual(len(lube_runs(self.runs)), 1)


if __name__ == "__main__":
    unittest.main()
