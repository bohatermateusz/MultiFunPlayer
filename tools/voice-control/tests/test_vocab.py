"""The vocabulary bank (variety), the inspiration given to the model, and the local detection of confessions / obedience."""
import os
import random
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from persona import Persona, build_system_prompt  # noqa: E402
from test_persona import Clock, FakePani, turn  # noqa: E402
from voice_control import load_config  # noqa: E402

CFG, INTENTS = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))
NOMANNERS = dict(CFG["persona"]["manners"], politeness_factor=1.0, ignore_demand_factor=1.0)
VOC = CFG["persona"]["vocabulary"]


def make(seed=1, **kw):
    return Controller(INTENTS, lambda s: None, clock=Clock(), wake_words=CFG["wake"]["words"], persona=Persona("x", api_key="k"),
                      persona_cfg=CFG["persona"], rng=random.Random(seed), **kw)


class BankTests(unittest.TestCase):
    def test_every_category_covers_every_stage_with_variety(self):
        for cat in ("rozkazy", "własność", "pochwały", "drwiny", "wyznania"):
            for stage in range(5):
                self.assertTrue(VOC[cat].get(str(stage)), f"{cat} stage {stage}")
        for cat, levels in VOC.items():
            total = sum(len(v) for v in levels.values())
            self.assertGreaterEqual(total, 8, cat)
            for lvl, items in levels.items():
                self.assertIn(lvl, "01234")
                for text in items:
                    self.assertIsInstance(text, str)
                    self.assertTrue(text.strip())

    def test_the_users_examples_are_in_the_bank(self):
        flat = " | ".join(t for levels in VOC.values() for items in levels.values() for t in items).lower()
        for needle in ("sssij", "połykaj", "spójrz na siebie", "należysz do mnie"):
            self.assertIn(needle, flat)

    def test_stage_zero_is_gentle_and_the_sharp_words_come_later(self):
        sharp = ("suk", "dziwk", "szmat", "ssij", "połykaj")
        stage0 = " ".join(t for levels in VOC.values() for t in levels.get("0", [])).lower()
        for word in sharp:
            self.assertNotIn(word, stage0)
        late = " ".join(t for levels in VOC.values() for lvl in ("3", "4") for t in levels.get(lvl, [])).lower()
        self.assertTrue(any(w in late for w in sharp))


class InspirationTests(unittest.TestCase):
    def test_picks_from_different_categories_of_the_current_stage(self):
        c = make()
        for stage in range(5):
            ins = c._inspiration(stage)
            self.assertEqual(len(ins), 3)
            self.assertEqual(len({i["rodzaj"] for i in ins}), 3)
            for i in ins:
                allowed = VOC[i["rodzaj"]].get(str(stage), []) + VOC[i["rodzaj"]].get(str(stage - 1), [])
                self.assertIn(i["fraza"], allowed + [t for lv in VOC[i["rodzaj"]].values() for t in lv] if not allowed else allowed)

    def test_no_phrase_repeats_within_the_window(self):
        c = make(seed=5)
        seen = []
        for _ in range(8):
            seen += [i["fraza"] for i in c._inspiration(2)]
        window = CFG["persona"]["inspiration"]["avoid_repeat"]
        for i in range(len(seen) - window):
            chunk = seen[i:i + window]
            self.assertEqual(len(chunk), len(set(chunk)), chunk)

    def test_variety_over_a_session(self):
        c = make(seed=2)
        seen = set()
        for _ in range(40):
            seen |= {i["fraza"] for i in c._inspiration(3)}
        self.assertGreaterEqual(len(seen), 15)

    def test_it_reaches_the_prompt_and_the_prompt_asks_for_variations(self):
        c = make()
        state = c._persona_state()
        self.assertEqual(len(state["inspiracja"]), 3)
        prompt = build_system_prompt(CFG["persona"]["character"], INTENTS, [], state)
        self.assertIn(state["inspiracja"][0]["fraza"], prompt)
        self.assertIn("SŁOWNIK", prompt)
        self.assertIn("sssij", prompt)


class LocalDetection(unittest.TestCase):
    def test_confessions_are_recognised_locally(self):
        c = make()
        for text in ["należę do ciebie", "jestem twoją dziwką", "Jestem Twoją małą dziwką, Pani", "proszę, jestem twoja", "nie mam własnej woli",
                     "zrób ze mną co chcesz", "jestem twoją suczką"]:
            self.assertTrue(c._says_any(text, CFG["persona"]["confession_phrases"]), text)
        for text in ["jak minął ci dzień", "kup mleko", "jestem głodna", "spuść się na mnie"]:
            self.assertFalse(c._says_any(text, CFG["persona"]["confession_phrases"]), text)

    def test_obedience_is_recognised_locally(self):
        c = make()
        for text in ["dziękuję", "Dziękuję, Pani", "ssę", "połykam", "klęczę", "tak Pani", "jak rozkażesz"]:
            self.assertTrue(c._says_any(text, CFG["persona"]["obey_phrases"], 0.88), text)
        self.assertFalse(c._says_any("dziękuję, ale nie", CFG["persona"]["obey_phrases"], 0.88))


class FullTurn(unittest.TestCase):
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
        self.runs, self.clock = [], Clock()
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"],
                            persona=Persona("x", self.base, "m", api_key="k", timeout=2), persona_cfg=CFG["persona"],
                            lock_cfg=CFG["lock"], rng=random.Random(1))

    def say(self, text, **t):
        FakePani.table[text] = turn(**t)
        self.clock.t += 1
        return self.c.handle(text)

    def test_a_confession_the_model_missed_still_raises_the_odds(self):
        self.c.persona_cfg = dict(CFG["persona"], manners=NOMANNERS, base_p=0.0, base_p_overrides={}, step_p=0.0, confession_p=0.3)
        o = self.say("kochanie, należę do ciebie, spuść się na mnie", request="lube_fire", confession=False)
        self.assertEqual(o.extra["p"], 0.3)

    def test_obedience_the_model_missed_pays_what_she_owed(self):
        self.c.persona_cfg = dict(CFG["persona"], manners=NOMANNERS, base_p=0.0, base_p_overrides={}, step_p=0.0)
        self.say("kochanie, spuść się na mnie", request="lube_fire", demand="thank")
        o = self.say("dziękuję, Pani", request="none", thanked=False, granted="Dobra dziewczynka. Masz.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.runs[-1][0]["action"], "{target}::Hismith::LubeLauncher::Fire")

    def test_each_turn_hands_the_model_new_phrases(self):
        for i in range(4):
            self.say(f"kochanie, proszę {i}", request="lube_fire")
        sets = []
        for call in FakePani.calls:
            system = call["body"]["messages"][0]["content"]
            sets.append(system.split('"inspiracja": ')[1][:120])
        self.assertEqual(len(set(sets)), len(sets))


if __name__ == "__main__":
    unittest.main()
