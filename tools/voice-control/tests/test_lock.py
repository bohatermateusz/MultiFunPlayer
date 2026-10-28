"""The 5-minute hold ("utrzymaj to"): everything except the Lube Launcher, stop and release is ignored."""
import os
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from llm import IntentLlm  # noqa: E402
from matcher import match  # noqa: E402
from test_llm import FakeChat  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.clock = [], Clock()
        w = CFG["wake"]
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=w["words"],
                            wake_min_similarity=w["min_similarity"], lock_cfg=CFG["lock"])

    def say(self, text):
        self.clock.t += 0.01          # cool-downs are not what is tested here
        self.c._last.clear()
        return self.c.handle("kochanie, " + text)

    def hold(self):
        o = self.say("utrzymaj to")
        self.assertEqual(o.status, "executed")
        return o

    # ---- starting it -------------------------------------------------------------------------------
    def test_phrases_start_the_hold_for_5_minutes(self):
        for text in ["utrzymaj to", "rób tak dalej", "tak trzymaj", "zostaw tak", "nie zmieniaj", "tak ma być", "zostań przy tym"]:
            self.setUp()
            self.assertEqual(self.say(text).intent, "lock", text)
            self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Lock::Start", "args": [5]}])
            self.assertAlmostEqual(self.c.lock_remaining(), 300, delta=1)

    def test_without_the_wake_word_nothing_happens(self):
        self.assertEqual(self.c.handle("utrzymaj to").status, "ignored")
        self.assertEqual(self.c.lock_remaining(), 0)

    # ---- what is ignored while held ----------------------------------------------------------------
    def test_every_machine_command_is_ignored_while_held(self):
        self.hold()
        sent = len(self.runs)
        for text in ["szybciej", "wolniej", "głębiej", "płycej", "tryb trzy", "na maksa", "najwolniej", "pieprz mnie",
                     "drażnij mnie", "użyj mnie", "dość", "zaskocz mnie", "coś innego", "wróć do filmu", "normalnie",
                     "tryb ręczny", "włącz sekwencję", "zapętl to"]:
            o = self.say(text)
            self.assertEqual(o.status, "locked", text)
            self.assertIn("koniec", o.detail)
        self.assertEqual(len(self.runs), sent)

    def test_lube_launcher_still_works(self):
        self.hold()
        for text in ["spuść się na mnie", "spuść się na mnie kilka razy", "zalej mnie", "spuść się na mnie dwa razy"]:
            self.assertEqual(self.say(text).status, "executed", text)
        self.assertEqual(self.c.lock_remaining() > 0, True)

    def test_stop_always_works_and_releases_the_hold(self):
        self.hold()
        self.assertEqual(self.say("koniec").status, "executed")
        self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Halt", "args": []}])
        self.assertEqual(self.c.lock_remaining(), 0)
        self.assertEqual(self.say("szybciej").status, "executed")

    def test_nothing_but_the_safe_word_releases_the_hold(self):
        self.hold()
        for text in ["odblokuj", "koniec blokady", "możesz zmieniać", "zdejmij blokadę", "dość", "wystarczy", "wróć do filmu", "normalnie"]:
            o = self.say(text)
            self.assertNotEqual(o.status, "executed", text)
            self.assertGreater(self.c.lock_remaining(), 0, text)
        self.assertEqual(self.say("koniec").status, "executed")
        self.assertEqual(self.c.lock_remaining(), 0)

    # ---- time -------------------------------------------------------------------------------------
    def test_the_hold_ends_by_itself_after_5_minutes(self):
        self.hold()
        self.clock.t += 299
        self.assertEqual(self.say("szybciej").status, "locked")
        self.clock.t += 2
        self.assertEqual(self.say("szybciej").status, "executed")

    def test_saying_it_again_restarts_the_time(self):
        self.hold()
        self.clock.t += 200
        self.hold()
        self.clock.t += 200
        self.assertEqual(self.say("szybciej").status, "locked")
        self.clock.t += 101
        self.assertEqual(self.say("szybciej").status, "executed")

    # ---- other ways in ------------------------------------------------------------------------------
    def test_keys_are_held_too_but_lube_and_stop_work(self):
        self.hold()
        self.assertEqual(self.c.press("faster").status, "locked")
        self.assertEqual(self.c.press("mode_3").status, "locked")
        self.assertEqual(self.c.press("lube_fire").status, "executed")
        self.assertEqual(self.c.press("halt").status, "executed")
        self.assertEqual(self.c.press("faster").status, "executed")

    def test_keys_can_start_the_hold_and_only_end_releases_it(self):
        self.assertEqual(CFG["keys"]["page_up"], "lock")
        self.assertNotIn("page_down", CFG["keys"])
        self.assertEqual(self.c.press("lock").status, "executed")
        self.assertEqual(self.c.press("faster").status, "locked")
        self.assertEqual(self.c.press("halt").status, "executed")
        self.assertEqual(self.c.press("faster").status, "executed")

    def test_dry_run_tracks_the_hold_without_sending(self):
        self.c.dry_run = True
        self.assertEqual(self.say("utrzymaj to").status, "dry-run")
        self.assertEqual(self.say("szybciej").status, "locked")
        self.assertEqual(self.runs, [])

    def test_allow_list_is_the_lube_stop_and_release_commands(self):
        self.assertEqual(sorted(CFG["lock"]["allow"]), sorted(["lube_fire", "lube_two", "lube_many", "halt", "lock"]))

    def test_no_phrase_is_both_a_hold_and_something_else(self):
        self.assertEqual(match("nie zmieniaj", INTENTS)[0].intent.id, "lock")
        self.assertIsNone(match("odblokuj", INTENTS)[0])


class HoldAndModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeChat.table, FakeChat.calls, FakeChat.mode = {}, [], "ok"
        self.runs, self.clock = [], Clock()
        w = CFG["wake"]
        llm = IntentLlm(self.base, "m", api_key="k", timeout=2, max_steps=CFG["llm"]["max_steps"])
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=w["words"], llm=llm, llm_cfg=CFG["llm"],
                            lock_cfg=CFG["lock"])

    def test_the_model_cannot_get_around_the_hold(self):
        self.assertEqual(self.c.handle("kochanie, utrzymaj to").status, "executed")
        FakeChat.table["trwanie, jestem twoją małą dziwką"] = dict(intent="improvise", confidence=0.95, addressed=True,
                                                                     plan=[{"step": "faster", "times": 1}])
        FakeChat.table["trwanie, tryb piąty"] = dict(intent="mode_5", confidence=0.95, addressed=True)
        for text in ["trwanie, jestem twoją małą dziwką", "trwanie, tryb piąty"]:
            self.assertEqual(self.c.handle(text).status, "locked", text)
        self.assertEqual(len(self.runs), 1)
        FakeChat.table["trwanie, strzel"] = dict(intent="lube_fire", confidence=0.95, addressed=True)
        self.assertEqual(self.c.handle("trwanie, strzel").status, "executed")


if __name__ == "__main__":
    unittest.main()
