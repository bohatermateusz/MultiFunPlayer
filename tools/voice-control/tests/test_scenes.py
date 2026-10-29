"""Erotic scene commands, the stop command and the improvised plans (bounded by the tool)."""
import json
import os
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from llm import IntentLlm, build_schema, build_system_prompt  # noqa: E402
from matcher import match  # noqa: E402
from test_llm import FakeChat  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)
BY_ID = {i.id: i for i in INTENTS}


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def actions(steps):
    return [s["action"].split("::", 1)[-1] for s in steps]


class StrictScenes(unittest.TestCase):
    def make(self):
        self.runs = []
        w = CFG["wake"]
        return Controller(INTENTS, self.runs.append, clock=Clock(), wake_words=w["words"], wake_min_similarity=w["min_similarity"])

    def test_phrases_map_to_the_scene(self):
        cases = {"pieprz mnie": "hard", "Zerżnij mnie!": "hard", "rżnij mnie": "hard", "rozepchaj mnie": "fill",
                 "głęboko w dupcię": "fill", "drażnij mnie": "tease", "pieść mnie": "tease", "użyj mnie": "use_me",
                 "jestem twoją sissy": "use_me", "jestem twoją suczką": "use_me", "jeszcze": "lock", "właśnie tak": "lock",
                 "nie przestawaj": "lock", "zalej mnie": "lube_many", "obryzgaj mnie kilka razy": "lube_many",
                 "dość": "calm", "wystarczy": "calm", "stój": "calm", "przestań": "calm", "koniec": "halt",
                 "wyłącz maszynę": "halt", "koniec pętli": "loop_off", "koniec sekwencji": "sequence_stop"}
        for text, intent in cases.items():
            m, reason = match(text, INTENTS)
            self.assertIsNotNone(m, f"{text}: {reason}")
            self.assertEqual(m.intent.id, intent, text)

    def test_scenes_run_the_expected_steps_after_the_wake_word(self):
        c = self.make()
        self.assertEqual(c.handle("kochanie, pieprz mnie").status, "executed")
        self.assertEqual(actions(self.runs[-1]), ["Hismith::Speed::Faster", "Hismith::Speed::Faster", "Hismith::Depth::Deeper"])
        self.assertEqual(c.handle("kochanie, drażnij mnie").status, "executed")
        self.assertEqual(actions(self.runs[-1]), ["Hismith::Speed::Slower", "Hismith::Speed::Slower", "Hismith::Depth::Shallower"])

    def test_without_the_wake_word_nothing_runs(self):
        c = self.make()
        for t in ["pieprz mnie", "dość", "użyj mnie"]:
            self.assertEqual(c.handle(t).status, "ignored", t)
        self.assertEqual(self.runs, [])

    def test_enough_goes_to_calm_and_does_not_stop(self):
        c = self.make()
        for text in ["kochanie, dość", "kochanie, wystarczy", "kochanie, starczy", "kochanie, przestań", "kochanie, stój"]:
            c._last.clear()
            self.assertEqual(c.handle(text).intent, "calm", text)
            self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Calm", "args": []}], text)

    def test_only_koniec_stops_everything(self):
        c = self.make()
        for text in ["kochanie, koniec", "kochanie, wyłącz maszynę", "kochanie, koniec zabawy"]:
            c._last.clear()
            self.assertEqual(c.handle(text).intent, "halt", text)
            self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Halt", "args": []}], text)

    def test_surprise_picks_a_random_mode_in_the_app(self):
        c = self.make()
        for text in ["kochanie, zaskocz mnie", "kochanie, niespodzianka", "kochanie, wybierz za mnie", "kochanie, losowo", "kochanie, zdaj się na los"]:
            c._last.clear()
            self.assertEqual(c.handle(text).intent, "surprise", text)
            self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Surprise", "args": []}], text)
        self.assertEqual(CFG["keys"]["insert"], "surprise")

    def test_re_roll_phrases_run_the_again_action(self):
        c = self.make()
        for text in ["kochanie, nieee, coś innego", "kochanie, zmień", "kochanie, nie, zmień", "kochanie, jeszcze raz",
                     "kochanie, losuj ponownie", "kochanie, inaczej", "kochanie, nie to"]:
            c._last.clear()
            self.assertEqual(c.handle(text).intent, "surprise_again", text)
            self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Surprise::Again", "args": []}], text)
        self.assertEqual(CFG["keys"]["delete"], "surprise_again")

    def test_re_roll_words_without_the_wake_word_do_nothing(self):
        c = self.make()
        for text in ["zmień", "coś innego", "jeszcze raz", "inaczej"]:
            self.assertEqual(c.handle(text).status, "ignored", text)
        self.assertEqual(self.runs, [])

    def test_surprise_is_not_confused_with_improvising_or_scenes(self):
        for text in ["zrób ze mną co chcesz", "zaskocz mnie jutro w pracy"]:
            m, _ = match(text, INTENTS)
            self.assertTrue(m is None or m.intent.id != "surprise", text)

    def test_keys_for_calm_and_halt(self):
        self.assertEqual(CFG["keys"]["home"], "calm")
        self.assertEqual(CFG["keys"]["end"], "halt")

    def test_dialogue_next_to_the_scene_words_is_not_a_command(self):
        c = self.make()
        for t in ["kochanie, nie pieprz mi głowy", "kochanie, użyj mnie jutro do zakupów", "kochanie, dość już tego gadania o pracy"]:
            self.assertEqual(c.handle(t).status, "ignored", t)

    def test_improvise_has_no_strict_phrases_and_atomic_steps_only(self):
        self.assertEqual(BY_ID["improvise"].phrases, [])
        allowed = {i.id for i in INTENTS if i.improvise}
        for forbidden in ["lube_many", "lube_two", "hard", "fill", "tease", "use_me", "halt", "calm", "pause", "play", "sequence_start", "lock", "surprise"]:
            self.assertNotIn(forbidden, allowed)
        for needed in ["faster", "slower", "deeper", "shallower", "script", "mode_3", "lube_fire", "mode_a", "mode_b"]:
            self.assertIn(needed, allowed)

    def test_schema_and_prompt_for_improvising(self):
        schema = build_schema(INTENTS)["schema"]["properties"]
        self.assertIn("improvise", schema["intent"]["enum"])
        step_enum = schema["plan"]["items"]["properties"]["step"]["enum"]
        self.assertNotIn("halt", step_enum)
        self.assertNotIn("lube_many", step_enum)
        for ok in ["faster", "lube_fire", "mode_a", "mode_b"]:
            self.assertIn(ok, step_enum)
        prompt = build_system_prompt(INTENTS, ["kochanie"], [])
        self.assertIn("IMPROVISE", prompt)
        self.assertIn("consensual", prompt)


class Improvise(unittest.TestCase):
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
        llm = IntentLlm(self.base, "m", api_key="k", timeout=2, temperature=0.3, max_steps=CFG["llm"]["max_steps"])
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=w["words"], llm=llm, llm_cfg=CFG["llm"])

    def say(self, text, plan, **kw):
        FakeChat.table[text] = dict(intent="improvise", confidence=0.9, addressed=True, plan=plan, **kw)
        return self.c.handle(text)

    def test_plan_becomes_steps_of_the_allowed_commands(self):
        o = self.say("kochanie, jestem twoją małą dziwką do zabawy", [{"step": "faster", "times": 1}, {"step": "deeper", "times": 2}, {"step": "mode_3", "times": 1}])
        self.assertEqual((o.status, o.via), ("executed", "llm"))
        self.assertEqual(actions(self.runs[-1]), ["Hismith::Speed::Faster", "Hismith::Depth::Deeper", "Hismith::Depth::Deeper", "Hismith::Mode::Set"])
        self.assertEqual(self.runs[-1][-1]["args"], [3])

    def test_the_model_cannot_use_forbidden_steps(self):
        o = self.say("kochanie, rób ze mną co ci się podoba", [{"step": "halt", "times": 1}, {"step": "calm", "times": 1},
                                                  {"step": "lube_many", "times": 1}, {"step": "format_c", "times": 1}])
        self.assertEqual(o.status, "ignored")
        self.assertEqual(self.runs, [])

    def test_lube_and_modes_a_b_may_be_part_of_a_scene(self):
        self.say("kochanie, zniszcz mnie i skończ we mnie", [{"step": "mode_a", "times": 1}, {"step": "lube_fire", "times": 1}])
        self.assertEqual(actions(self.runs[-1]), ["Hismith::Mode::Set", "Hismith::LubeLauncher::Fire"])
        self.assertEqual(self.runs[-1][0]["args"], [9])

    def test_at_most_one_shot_per_scene_and_forbidden_steps_are_dropped(self):
        self.say("kochanie, weź mnie jak chcesz", [{"step": "lube_fire", "times": 2}, {"step": "halt", "times": 1},
                                                   {"step": "lube_fire", "times": 1}, {"step": "slower", "times": 1}])
        self.assertEqual(actions(self.runs[-1]), ["Hismith::LubeLauncher::Fire", "Hismith::Speed::Slower"])

    def test_total_increase_is_capped(self):
        plan = [{"step": "faster", "times": 2}] * 4 + [{"step": "deeper", "times": 2}] * 4
        self.say("kochanie, zrób ze mną co chcesz maksymalnie", plan)
        steps = actions(self.runs[-1])
        self.assertLessEqual(steps.count("Hismith::Speed::Faster"), CFG["llm"]["max_faster"])
        self.assertLessEqual(steps.count("Hismith::Depth::Deeper"), CFG["llm"]["max_deeper"])
        self.assertLessEqual(len(steps), 2 * CFG["llm"]["max_steps"])

    def test_number_of_plan_steps_is_capped(self):
        plan = [{"step": "slower", "times": 1}] * 20
        self.say("kochanie, powoli i czule", plan)
        self.assertLessEqual(len(self.runs[-1]), CFG["llm"]["max_steps"] * 2)

    def test_not_erotic_or_not_addressed_runs_nothing(self):
        FakeChat.table["kochanie, kup mleko"] = dict(intent="none", confidence=0.9, addressed=True, plan=[])
        self.assertEqual(self.c.handle("kochanie, kup mleko").status, "ignored")
        o = self.say("kochanie, tak rozmawiam z kolegą", [{"step": "faster", "times": 1}], )
        FakeChat.table["kochanie, tak rozmawiam z kolegą"]["addressed"] = False
        self.clock.t += 20
        self.assertEqual(self.c.handle("kochanie, tak rozmawiam z kolegą").status, "ignored")

    def test_low_confidence_and_cooldown(self):
        o = self.say("kochanie, cała dla ciebie", [{"step": "faster", "times": 1}], )
        self.assertEqual(o.status, "executed")
        o = self.say("kochanie, cała dla ciebie jeszcze bardziej", [{"step": "faster", "times": 1}])
        self.assertEqual(o.status, "cooldown")
        self.clock.t += 20
        FakeChat.table["kochanie, nie wiem co"] = dict(intent="improvise", confidence=0.5, addressed=True, plan=[{"step": "faster", "times": 1}])
        self.assertEqual(self.c.handle("kochanie, nie wiem co").status, "ignored")

    def test_improvised_plan_is_in_the_log_and_remembered(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.c.log_path = os.path.join(d, "log.jsonl")
            self.say("kochanie, wiesz że jestem tylko twoją małą sissy", [{"step": "faster", "times": 2}, {"step": "deeper", "times": 1}])
            rec = json.loads(open(self.c.log_path, encoding="utf-8").read())
            self.assertEqual(rec["plan"], ["fasterx2", "deeper"])
        self.assertIn("improvised", self.c._recent[-1])

    def test_dry_run_sends_nothing(self):
        self.c.dry_run = True
        o = self.say("kochanie, weź mnie", [{"step": "faster", "times": 1}])
        self.assertEqual(o.status, "dry-run")
        self.assertEqual(self.runs, [])


if __name__ == "__main__":
    unittest.main()
