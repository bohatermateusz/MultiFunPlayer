"""A refusal always says what to do: an order from the bank ("Wypnij pupę"), granted once he says he did it; a refusal
without a demand gets an instructive line; a listed command needs no wake word ("ruchaj mnie mocniej")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_manners import CFG, P, Server  # noqa: E402
from test_persona import FakePani  # noqa: E402

TD = P["manners"]["task_demands"]


def cfg(chance, **kw):
    base = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0,
                manners=dict(P["manners"], task_demands=dict(TD, enabled=True, chance=[chance])))
    base.update(kw)
    return base


class Orders(Server):
    def test_a_refusal_with_an_order_says_it_and_how_to_report_it(self):
        self.c.persona_cfg = cfg(1.0)
        o = self.say("kochanie, ruchaj mnie mocniej", request="hard", denied="Nie.")
        self.assertEqual(o.status, "denied")
        line = o.extra["say"]
        self.assertIn(o.extra["task"], line)
        self.assertIn(TD["tail"], line)
        machine = [t["line"] for t in TD["tasks"]["machine"] + TD["tasks"]["any"]]
        self.assertIn(o.extra["task"], machine)          # a body position for the machine, not the mouth
        self.assertEqual(self.c._owed, "hard")

    def test_lube_gets_a_mouth_or_face_order(self):
        self.c.persona_cfg = cfg(1.0)
        o = self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.assertIn(o.extra["task"], [t["line"] for t in TD["tasks"]["lube"] + TD["tasks"]["any"]])

    def test_reporting_the_order_done_grants_the_request(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, ruchaj mnie mocniej", request="hard")
        done = self.c._task["done"][0]
        o = self.say(done, request="none", granted="Grzeczna. Masz.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(o.intent, "hard")
        self.assertEqual(o.extra["decision"], "owed")
        self.assertIsNone(self.c._task)

    def test_a_generic_done_word_counts_too(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, włącz tryb ósmy", request="mode_8")
        o = self.say("gotowe", request="none")
        self.assertEqual((o.status, o.intent), ("executed", "mode_8"))

    def test_asking_again_without_doing_it_repeats_the_order(self):
        self.c.persona_cfg = cfg(1.0)
        first = self.say("kochanie, ruchaj mnie mocniej", request="hard")
        task = first.extra["task"]
        self.c.persona_cfg = cfg(0.0)                    # no new order: the pending one is said again
        again = self.say("kochanie, ruchaj mnie mocniej", request="hard", denied="Nie.")
        self.assertEqual(again.status, "denied")
        self.assertIn(task, again.extra["say"])

    def test_the_order_is_in_the_state_for_her_reminders(self):
        self.c.persona_cfg = cfg(1.0)
        o = self.say("kochanie, ruchaj mnie mocniej", request="hard")
        self.assertEqual(self.c._persona_state()["czekam_az_wykona"], o.extra["task"])

    def test_done_words_count_while_she_speaks(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, ruchaj mnie mocniej", request="hard")
        self.assertIn(self.c._task["done"][0], self.c.task_done_words())


class OrderAndDefiance(Server):
    def test_an_open_refusal_drops_the_order_and_she_wants_an_apology(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, ruchaj mnie mocniej", request="hard")
        self.say("nie chcę", request="none", defiance=True)
        self.assertIsNone(self.c._task)
        o = self.say("kochanie, ruchaj mnie mocniej", request="hard", denied="Nie? To nie dostajesz nic. Przeproś.")
        self.assertNotIn("task", o.extra)

    def test_asking_again_can_still_be_granted_by_the_dice(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, ruchaj mnie mocniej", request="hard")
        self.c.persona_cfg = cfg(1.0, base_p_overrides={"hard": 0.95}, manners=dict(P["manners"], ignore_demand_factor=1.0,
                                                                                   task_demands=dict(TD, enabled=True, chance=[1.0])))
        o = self.say("kochanie, proszę, ruchaj mnie mocniej", request="hard")
        self.assertIn(o.status, ("executed", "denied"))
        if o.status == "denied":
            self.assertIn(self.c._task["line"], o.extra["say"])   # the same order, said again


class Bank(Server):
    def test_every_order_is_met_by_each_of_its_own_done_phrases(self):
        self.c.persona_cfg = cfg(1.0)
        for kind, tasks in TD["tasks"].items():
            for t in tasks:
                for done in t["done"]:
                    self.c._task, self.c._last_demand = t, ("hard", "task")
                    self.assertTrue(self.c._demand_met(done), f"{kind}: {t['line']} <- {done!r}")
                    self.assertTrue(self.c._demand_met("kochanie, " + done), f"{kind}: {t['line']} <- kochanie, {done!r}")

    def test_an_unrelated_sentence_does_not_count_as_done(self):
        self.c.persona_cfg = cfg(1.0)
        t = next(t for t in TD["tasks"]["lube"] if t["line"].startswith("Złóż dłonie"))
        self.c._task, self.c._last_demand = t, ("lube_fire", "task")
        self.assertFalse(self.c._demand_met("kochanie, spuść się na mnie"))

    def test_orders_are_unique_and_staged(self):
        lines = [t["line"] for v in TD["tasks"].values() for t in v]
        self.assertEqual(len(lines), len(set(lines)))
        self.assertTrue(all(0 <= t.get("min_stage", 0) <= 4 for v in TD["tasks"].values() for t in v))


class InstructiveRefusal(Server):
    def test_a_refusal_without_a_demand_says_what_to_do(self):
        # 08:48:32 "Nie. I nie dąsaj się." - no hint that asking again, nicer, would work
        self.c.persona_cfg = cfg(0.0)
        o = self.say("kochanie, proszę, spuść się na mnie", request="lube_fire", demand="none", denied="Nie. I nie dąsaj się.")
        self.assertEqual(o.status, "denied")
        self.assertIn(o.extra["say"], P["manners"]["denials"]["beg"])


class NoWakeWord(Server):
    def setUp(self):
        super().setUp()
        self.c.wake_cfg_optional_for_commands = True
        self.c._window_until = 0                         # outside her reply window

    def test_a_listed_command_works_without_the_wake_word(self):
        self.c.persona_cfg = cfg(0.0, base_p_overrides={"hard": 1.0})
        o = self.say("ruchaj mnie mocniej", request="hard", addressed=False)
        self.assertEqual(o.intent, "hard")               # it reached her (her dice decide), not "no wake word"
        self.assertIn(o.status, ("executed", "denied"))
        self.assertEqual(len(FakePani.calls), 1)

    def test_loose_talk_still_needs_it(self):
        o = self.say("ale dzisiaj ładna pogoda", request="none")
        self.assertEqual(o.detail, "no wake word")
        self.assertEqual(FakePani.calls, [])

    def test_a_command_over_her_counts_unless_it_is_her_echo(self):
        self.assertTrue(self.c.command_over_her("ruchaj mnie mocniej", ["Leżysz pokryta i bezsilna."]))
        self.assertFalse(self.c.command_over_her("ruchaj mnie mocniej", ["Ruchaj mnie mocniej, mówisz? Jeszcze nie."]))
        self.assertFalse(self.c.command_over_her("dobra dziewczynka", ["Dobra dziewczynka."]))


if __name__ == "__main__":
    unittest.main()
