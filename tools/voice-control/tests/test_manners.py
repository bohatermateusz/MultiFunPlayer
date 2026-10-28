"""Manners: being polite pays, ignoring her demand costs, an open refusal is punished until you apologise; the 60 s reply window."""
import os
import random
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from persona import Persona  # noqa: E402
from test_persona import Clock, FakePani, turn  # noqa: E402
from voice_control import load_config  # noqa: E402

CFG, INTENTS = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))
P = CFG["persona"]
M = P["manners"]


class T:
    def __init__(self, confession=False, thanked=False):
        self.confession, self.thanked = confession, thanked


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakePani)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakePani.table, FakePani.calls, FakePani.mode = {}, [], "ok"
        self.runs, self.said, self.clock = [], [], Clock()
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"],
                            persona=Persona("x", self.base, "m", api_key="k", timeout=2), persona_cfg=P, rng=random.Random(1))
        self.c.speak_ex = lambda line, voice: self.said.append((line, voice))

    def say(self, text, **t):
        FakePani.table[text] = turn(**t)
        self.clock.t += 1
        return self.c.handle(text)


class Politeness(Server):
    def test_a_polite_ask_has_better_odds_than_a_rude_one(self):
        self.c.persona_cfg = dict(P, base_p=0.5, base_p_overrides={}, step_p_overrides={}, difficulty=1.0)
        a = self.c._decide("lube_fire", T(), polite=True)[1]
        b = self.c._decide("lube_fire", T(), polite=False)[1]
        self.assertAlmostEqual(b, a * M["politeness_factor"], places=6)

    def test_polite_words_are_recognised(self):
        for text in ["proszę, Pani, spuść się", "błagam", "Pani, spuść się", "dziękuję", "bardzo chcę"]:
            self.assertTrue(self.c._is_polite(text), text)
        for text in ["spuść się", "daj mi to", "szybciej"]:
            self.assertFalse(self.c._is_polite(text), text)

    def test_the_extra_is_logged(self):
        o = self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.assertIs(o.extra["polite"], False)
        o = self.say("kochanie, proszę, spuść się na mnie", request="lube_fire")
        self.assertIs(o.extra["polite"], True)


class IgnoringHerDemand(Server):
    def test_asking_again_without_obeying_costs(self):
        self.c.persona_cfg = dict(P, base_p=0.5, base_p_overrides={}, step_p_overrides={}, step_p=0.0)
        self.say("kochanie, proszę, spuść się na mnie", request="lube_fire", demand="confess")
        self.assertEqual(self.c._owed, "lube_fire")
        o = self.say("kochanie, proszę, spuść się na mnie", request="lube_fire", demand="confess")
        self.assertAlmostEqual(o.extra["p"], 0.5 * M["ignore_demand_factor"], places=2)

    def test_obeying_pays_in_full(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0)
        self.say("kochanie, proszę, spuść się na mnie", request="lube_fire", demand="thank")
        o = self.say("dziękuję, Pani", request="none", granted="Dobra dziewczynka. Masz.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(o.extra["decision"], "owed")


class RefusalIsPunished(Server):
    def test_an_open_refusal_wipes_the_progress_and_blocks_everything_for_a_while(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.3)
        for i in range(3):
            self.say(f"kochanie, proszę {i}, spuść się na mnie", request="lube_fire")
        self.assertEqual(self.c._asks["lube_fire"], 3)
        o = self.say("kochanie, nie powiem", request="none", demand="confess", denied="Nie? To nie dostajesz nic. Przeproś.")
        self.assertEqual(self.c._asks, {})
        self.assertGreater(self.c.penalty_remaining(), 60)
        self.assertEqual(self.said[-1][0], "Nie? To nie dostajesz nic. Przeproś.")
        self.c.persona_cfg = dict(P, base_p=1.0, base_p_overrides={}, manners=dict(M, politeness_factor=1.0))
        o = self.say("kochanie, błagam, Pani, proszę, spuść się na mnie", request="lube_fire")
        self.assertEqual(o.status, "denied")
        self.assertEqual(o.extra["p"], 0.0)
        self.assertGreater(o.extra["punished"], 0)

    def test_the_model_can_flag_defiance_too(self):
        self.say("kochanie, to głupie", request="none", defiance=True)
        self.assertGreater(self.c.penalty_remaining(), 0)

    def test_local_refusal_phrases(self):
        for text in ["nie chcę", "nie powiem", "nie będę prosić", "odmawiam", "nie ma mowy", "kochanie, nie będę tego mówić"]:
            self.setUp()
            self.say(text if text.startswith("kochanie") else "kochanie, " + text, request="none")
            self.assertGreater(self.c.penalty_remaining(), 0, text)
        for text in ["nie wiem", "chcę jeszcze", "proszę, Pani"]:
            self.setUp()
            self.say("kochanie, " + text, request="none")
            self.assertEqual(self.c.penalty_remaining(), 0, text)

    def test_an_apology_ends_the_punishment(self):
        self.say("kochanie, nie chcę", request="none")
        self.assertGreater(self.c.penalty_remaining(), 0)
        self.say("przepraszam, Pani", request="none")
        self.assertEqual(self.c.penalty_remaining(), 0)
        self.c.persona_cfg = dict(P, base_p=1.0, base_p_overrides={}, manners=dict(M, politeness_factor=1.0, ignore_demand_factor=1.0))
        o = self.say("kochanie, proszę, spuść się na mnie", request="lube_fire")
        self.assertEqual(o.status, "executed")

    def test_the_punishment_runs_out(self):
        self.say("kochanie, nie chcę", request="none")
        self.clock.t += M["penalty_seconds"] + 1
        self.assertEqual(self.c.penalty_remaining(), 0)

    def test_the_sure_ask_does_not_beat_the_punishment(self):
        self.c.persona_cfg = dict(P, max_asks=1, base_p=0.0, base_p_overrides={}, step_p_overrides={})
        self.say("kochanie, nie chcę", request="none")
        o = self.say("kochanie, proszę, spuść się", request="lube_fire")
        self.assertEqual(o.extra["p"], 0.0)

    def test_the_state_tells_the_model(self):
        self.say("kochanie, nie chcę", request="none")
        self.assertGreater(self.c._persona_state()["kara_sekundy"], 0)
        system = FakePani.calls[-1]["body"]["messages"][0]["content"]
        self.assertIn("defiance", system)
        self.assertIn("kara_sekundy", system)

    def test_the_safe_word_and_the_hold_are_never_punished(self):
        self.say("kochanie, nie chcę", request="none")
        self.assertEqual(self.say("kochanie, koniec").status, "executed")
        self.assertEqual(self.say("kochanie, utrzymaj to").status, "executed")


class ReplyWindow(Server):
    def test_the_window_is_a_minute(self):
        self.assertEqual(P["reply_window"], 60)
        self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.clock.t += 50
        self.assertTrue(self.c.in_reply_window())
        o = self.say("proszę, Pani, bardzo proszę", request="lube_fire")
        self.assertIn(o.status, ("denied", "executed"))
        self.clock.t += 70
        self.assertFalse(self.c.in_reply_window())
        self.assertEqual(self.say("proszę, Pani, jeszcze raz", request="lube_fire").status, "ignored")


if __name__ == "__main__":
    unittest.main()
