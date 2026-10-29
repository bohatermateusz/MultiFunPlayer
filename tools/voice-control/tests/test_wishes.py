"""She understands wishes like a person: how many shots ("zalej mnie"), and timing ("zmieniaj tryby co 3 minuty",
"za 5 minut zwolnij", "na minutę szybciej") - a program run by tick(), through the dice like everything else."""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from persona import PersonaTurn, build_schema, build_system_prompt, parse_program  # noqa: E402
from test_manners import CFG, INTENTS, P, Server  # noqa: E402
from test_persona import FakePani  # noqa: E402


def lube_steps(runs):
    return [st for r in runs for st in r if "LubeLauncher" in st["action"]]


def actions(runs):
    return [st["action"].split("::", 1)[1] for r in runs for st in r]


class Base(Server):
    def setUp(self):
        super().setUp()
        c = copy.deepcopy(P)
        c.update(base_p=1.0, base_p_overrides={}, step_p_overrides={}, difficulty=1.0,
                 manners=dict(P["manners"], politeness_factor=1.0, ignore_demand_factor=1.0))
        c["last_second"]["enabled"] = False
        c["spontaneous"]["lube_surprise"]["enabled"] = False
        c["spontaneous"]["turn_chance"] = [0.0] * 5
        self.c.persona_cfg = c

    def wish(self, text, **t):
        self.clock.t += 20
        return self.say(text, **t)


class Shots(Base):
    def test_zalej_mnie_means_several(self):
        o = self.wish("kochanie, zalej mnie", request="lube_fire", shots=4)
        self.assertEqual(o.status, "executed")
        self.assertEqual(lube_steps(self.runs), [{"action": "{target}::Hismith::LubeLauncher::FireMany", "args": [4]}])
        self.assertEqual(o.extra["shots"], 4)

    def test_capped_at_max_shots(self):
        self.wish("kochanie, zalej mnie całą", request="lube_fire", shots=9)
        self.assertEqual(lube_steps(self.runs)[0]["args"], [5])

    def test_one_shot_stays_one(self):
        self.wish("kochanie, spuść się", request="lube_fire", shots=1)
        self.assertEqual(lube_steps(self.runs), [{"action": "{target}::Hismith::LubeLauncher::Fire", "args": []}])

    def test_no_count_means_what_the_command_says(self):
        self.wish("kochanie, strzel kilka razy", request="lube_many", shots=0)
        self.assertEqual(lube_steps(self.runs)[0]["args"], [3])

    def test_denied_means_nothing(self):
        self.c.persona_cfg = dict(self.c.persona_cfg, base_p=0.0)
        o = self.wish("kochanie, zalej mnie", request="lube_fire", shots=4)
        self.assertEqual(o.status, "denied")
        self.assertEqual(lube_steps(self.runs), [])

    def test_her_own_moves_still_one_shot(self):
        steps, used = self.c._improvised_steps([{"step": "lube_fire", "times": 2}, {"step": "lube_many", "times": 1}])
        self.assertEqual(len(lube_steps([steps])), 1)

    def test_fallback_without_her_zalej_is_three(self):
        self.c.persona = None
        o = self.c.handle("kochanie, zalej mnie")
        self.assertEqual(o.intent, "lube_many")


class Program(Base):
    def modes_every(self, every=180, **over):
        pr = dict(start_after_seconds=0, every_seconds=every, duration_seconds=0, plan=[], random_modes=True, cancel=False)
        pr.update(over)
        return self.wish("kochanie, zmieniaj tryby co trzy minuty", request="mode_random", program=pr)

    def test_random_modes_every_three_minutes(self):
        o = self.modes_every()
        self.assertEqual(o.status, "executed")
        self.assertEqual(actions(self.runs), ["Hismith::Mode::Random"])          # the first one now
        self.assertIn("losowy tryb co 180 s", o.extra["program"])
        self.clock.t += 100
        self.assertFalse(self.c.tick())
        self.clock.t += 80
        self.assertTrue(self.c.tick())
        self.assertEqual(actions(self.runs).count("Hismith::Mode::Random"), 2)
        self.clock.t += 180
        self.assertTrue(self.c.tick())
        self.assertEqual(actions(self.runs).count("Hismith::Mode::Random"), 3)

    def test_start_after(self):
        pr = dict(start_after_seconds=300, every_seconds=0, duration_seconds=0, plan=[], random_modes=False, cancel=False)
        o = self.wish("kochanie, za pięć minut zwolnij", request="slower", program=pr)
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.runs, [])
        self.clock.t += 299
        self.assertFalse(self.c.tick())
        self.clock.t += 2
        self.assertTrue(self.c.tick())
        self.assertEqual(actions(self.runs), ["Hismith::Speed::Slower"])
        self.assertIsNone(self.c._program)                                      # once

    def test_for_a_minute_then_back(self):
        pr = dict(start_after_seconds=0, every_seconds=0, duration_seconds=60, plan=[], random_modes=False, cancel=False)
        self.wish("kochanie, na minutę szybciej", request="faster", program=pr)
        self.assertEqual(actions(self.runs), ["Hismith::Speed::Faster"])
        self.clock.t += 61
        self.assertTrue(self.c.tick())
        self.assertEqual(actions(self.runs), ["Hismith::Speed::Faster", "Hismith::Speed::Slower"])
        self.assertIsNone(self.c._program)

    def test_duration_ends_a_repeating_program(self):
        self.modes_every(every=60, duration_seconds=150)
        self.clock.t += 61
        self.c.tick()
        self.clock.t += 61
        self.c.tick()
        self.assertEqual(actions(self.runs).count("Hismith::Mode::Random"), 3)
        self.assertIsNone(self.c._program)

    def test_interval_is_clamped(self):
        self.modes_every(every=5)
        self.assertEqual(self.c._program["every"], 30)
        self.modes_every(every=99999)
        self.assertEqual(self.c._program["every"], 900)

    def test_hold_makes_it_wait_and_does_not_cancel(self):
        self.modes_every()
        self.c.press("lock")
        self.clock.t += 200
        self.assertFalse(self.c.tick())
        self.assertIsNotNone(self.c._program)
        self.c.press("halt")
        self.assertIsNone(self.c._program)                                      # the safe word ends it

    def test_koniec_by_voice_ends_it(self):
        self.modes_every()
        self.c.handle("koniec")
        self.assertIsNone(self.c._program)

    def test_cancel_by_words_no_dice(self):
        self.modes_every()
        self.c.persona_cfg = dict(self.c.persona_cfg, base_p=0.0)
        pr = dict(start_after_seconds=0, every_seconds=0, duration_seconds=0, plan=[], random_modes=False, cancel=True)
        o = self.wish("kochanie, przestań zmieniać", request="none", program=pr)
        self.assertEqual(o.extra["program"], "cancelled")
        self.assertIsNone(self.c._program)

    def test_a_new_program_replaces_the_old(self):
        self.modes_every(every=180)
        self.modes_every(every=60)
        self.assertEqual(self.c._program["every"], 60)

    def test_denied_installs_nothing(self):
        self.c.persona_cfg = dict(self.c.persona_cfg, base_p=0.0)
        o = self.modes_every()
        self.assertEqual(o.status, "denied")
        self.assertIsNone(self.c._program)
        self.assertEqual(self.runs, [])

    def test_lube_in_a_program_one_shot_with_a_gap(self):
        pr = dict(start_after_seconds=0, every_seconds=30, duration_seconds=0, plan=[{"step": "lube_fire", "times": 2}],
                  random_modes=False, cancel=False)
        self.wish("kochanie, zalewaj mnie co pół minuty", request="lube_fire", program=pr)
        self.assertEqual(lube_steps(self.runs), [{"action": "{target}::Hismith::LubeLauncher::Fire", "args": []}])
        self.clock.t += 31
        self.c.tick()
        self.assertEqual(len(lube_steps(self.runs)), 1)                          # lube_every_min not yet over
        self.clock.t += 31
        self.c.tick()
        self.assertEqual(len(lube_steps(self.runs)), 2)

    def test_never_halt_or_calm(self):
        pr = dict(start_after_seconds=0, every_seconds=60, duration_seconds=0, plan=[{"step": "halt", "times": 1}, {"step": "calm", "times": 1}],
                  random_modes=False, cancel=False)
        o = self.wish("kochanie, co minutę stop", request="none", program=pr)
        self.assertIsNone(self.c._program)
        self.assertNotIn("Hismith::Halt", actions(self.runs))

    def test_she_sees_her_program(self):
        self.assertIsNone(self.c._persona_state()["program_aktywny"])
        self.modes_every()
        self.assertIn("losowy tryb", self.c._persona_state()["program_aktywny"])

    def test_dry_run_runs_nothing(self):
        self.c.dry_run = True
        self.modes_every()
        self.clock.t += 181
        self.c.tick()
        self.assertEqual(self.runs, [])


class Schema(unittest.TestCase):
    def test_shots_and_program_in_the_schema(self):
        s = build_schema(INTENTS, ["faster"])["schema"]
        self.assertIn("shots", s["required"])
        self.assertIn("program", s["required"])
        self.assertEqual(sorted(s["properties"]["program"]["anyOf"][1]["required"]),
                         sorted(["start_after_seconds", "every_seconds", "duration_seconds", "plan", "random_modes", "cancel"]))

    def test_parse_program(self):
        self.assertIsNone(parse_program(None))
        self.assertIsNone(parse_program({"start_after_seconds": 0, "every_seconds": 0, "duration_seconds": 0, "plan": [], "random_modes": False, "cancel": False}))
        pr = parse_program({"every_seconds": "180", "random_modes": True, "plan": [{"step": "faster", "times": "2"}]})
        self.assertEqual((pr["every_seconds"], pr["random_modes"], pr["plan"]), (180, True, [{"step": "faster", "times": 2}]))
        self.assertTrue(parse_program({"cancel": True})["cancel"])

    def test_the_prompt_teaches_wishes(self):
        text = build_system_prompt("x", INTENTS, ["faster"], {})
        for s in ("ROZUMIENIE ŻYCZEŃ", "zmieniaj tryby co 3 minuty", "every_seconds: 180", "za pięć minut zwolnij", "zalej mnie", "cancel"):
            self.assertIn(s, text, s)

    def test_mode_random_intent_exists_and_is_hers(self):
        ids = {i.id: i for i in INTENTS}
        self.assertIn("mode_random", ids)
        self.assertTrue(ids["mode_random"].improvise)
        self.assertIn("mode_random", P["gated"])
        self.assertIn("several times", ids["lube_many"].description)


if __name__ == "__main__":
    unittest.main()
