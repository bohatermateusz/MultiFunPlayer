"""F10: everything switches to English (and back) - phrases, wake words, her addresses and manners, narration, voices."""
import copy
import json
import os
import random
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller, deep_merge  # noqa: E402
from persona import Persona, build_narration_prompt, build_nudge_prompt, build_system_prompt  # noqa: E402
from test_manners import CFG, INTENTS, P, Server  # noqa: E402
from test_persona import Clock  # noqa: E402
from voice_control import load_config, save_voice_id  # noqa: E402

CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "commands.json")
EN = P["languages"]["en"]


class Plain(unittest.TestCase):
    """without the persona: the English phrases run the commands"""

    def setUp(self):
        self.runs, self.clock = [], Clock()
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"],
                            wake_words_by_lang={"pl": CFG["wake"]["words"], "en": CFG["wake"]["words_en"]})

    def say(self, text):
        self.clock.t += 20
        return self.c.handle(text)

    def test_english_phrases_after_the_switch(self):
        self.assertEqual(self.say("darling, faster").status, "ignored")        # Polish wake word expected
        self.assertTrue(self.c.set_language("en"))
        self.assertEqual(self.say("darling, faster").intent, "faster")
        self.assertEqual(self.say("mistress, cum on me").intent, "lube_fire")
        self.assertEqual(self.say("honey, mode three").intent, "mode_3")
        self.assertEqual(self.say("kochanie, szybciej").status, "ignored")   # Polish no longer understood

    def test_the_safe_word_is_trabka_in_english_too(self):
        self.c.set_language("en")
        o = self.say("trąbka")
        self.assertEqual((o.intent, o.status), ("halt", "executed"))
        self.assertEqual(self.say("darling, enough").intent, "calm")

    def test_back_to_polish(self):
        self.c.set_language("en")
        self.assertFalse(self.c.set_language("en"))                           # no change
        self.assertTrue(self.c.set_language("pl"))
        self.assertEqual(self.say("kochanie, szybciej").intent, "faster")
        self.assertEqual(self.say("trąbka").intent, "halt")

    def test_every_intent_with_phrases_has_english_ones(self):
        for i in INTENTS:
            if i.phrases:
                self.assertTrue(i.phrases_by_lang.get("en"), i.id)

    def test_on_language_callback(self):
        seen = []
        self.c.on_language = seen.append
        self.c.set_language("en")
        self.assertEqual(seen, ["en"])


class WithHer(Server):
    def setUp(self):
        super().setUp()
        self.c.wake_words_by_lang = {"pl": CFG["wake"]["words"], "en": CFG["wake"]["words_en"]}
        self.c.persona_cfg = copy.deepcopy(P)

    def test_overlay_changes_addresses_manners_and_the_rule(self):
        self.assertIn("Pani", self.c.persona_cfg["address"]["titles"])
        self.c.set_language("en")
        cfg = self.c.persona_cfg
        self.assertIn("Mistress", cfg["address"]["titles"])
        self.assertIn("please", cfg["manners"]["polite_markers"])
        self.assertEqual(cfg["manners"]["penalty_seconds"], P["manners"]["penalty_seconds"])   # untouched keys stay
        self.assertIn("ENGLISH", cfg["language_rule"])
        state = self.c._persona_state()
        self.assertEqual(state["jezyk"], "en")
        self.assertIn("ENGLISH", build_system_prompt("x", INTENTS, ["faster"], state))
        self.assertIn("ENGLISH", build_nudge_prompt("x", state))
        self.assertIn("ENGLISH", build_narration_prompt("x", state, "t", []))
        self.c.set_language("pl")
        self.assertIn("Pani", self.c.persona_cfg["address"]["titles"])
        self.assertIn("polsku", self.c._persona_state()["regula_jezyka"])

    def test_english_confession_refusal_and_apology_are_heard_locally(self):
        self.c.set_language("en")
        self.say("darling, i'm your whore", request="none")
        self.assertGreater(self.c._heat, 1.0)
        self.say("darling, i won't beg", request="none")
        self.assertGreater(self.c.penalty_remaining(), 0)
        self.say("i'm sorry, mistress", request="none")
        self.assertEqual(self.c.penalty_remaining(), 0)
        self.assertTrue(self.c._is_polite("please, mistress"))
        self.assertFalse(self.c._is_polite("proszę, Pani"))

    def test_narration_tasks_and_pool_follow_the_language(self):
        class FakePool:
            def __init__(self, tag):
                self.tag = tag

            def take(self, theme):
                return f"{self.tag}: {theme[:10]}"

            def left(self, theme):
                return 5

            def remember(self, line):
                pass

            def is_repeat(self, line):
                return False
        self.c.pools = {"pl": FakePool("PL"), "en": FakePool("EN")}
        self.c.pool = self.c.pools["pl"]
        self.c.set_language("en")
        self.assertIs(self.c.pool, self.c.pools["en"])
        self.assertIn("Squeeze. Release. Feel it.", self.c.persona_cfg["narration"]["tasks"]["by_stage"]["0"])
        self.assertIn("machine", self.c.persona_cfg["narration"]["pool"]["banned_words"])
        self.assertTrue(self.c.is_banned("The machine takes you."))
        themes = self.c.persona_cfg["narration"]["themes"]
        self.assertTrue(all(th in themes for th in self.c.persona_cfg["narration"]["pool"]["seed"]))

    def test_say_now_speaks_and_remembers(self):
        self.c.say_now("This is my voice now.")
        self.assertEqual(self.said[-1][0], "This is my voice now.")
        self.assertIn("This is my voice now.", self.c._persona_state()["nie_powtarzaj"])


class Keys(unittest.TestCase):
    def test_special_targets_are_dispatched(self):
        from hotkeys import Hotkeys, cheat_sheet
        h = Hotkeys.__new__(Hotkeys)
        seen = []
        h.on_mute, h.on_intent, h.on_special = lambda: seen.append("mute"), seen.append, seen.append
        import threading
        for t in ("@voice", "@lang", "@mute", "faster"):
            h._fire(t)
        for th in threading.enumerate():
            if th is not threading.main_thread():
                th.join(1)
        self.assertEqual(sorted(seen), ["@lang", "@voice", "faster", "mute"])
        self.assertEqual(CFG["keys"]["f9"], "@voice")
        self.assertEqual(CFG["keys"]["f10"], "@lang")
        sheet = cheat_sheet(CFG["keys"], INTENTS)
        self.assertIn("next voice", sheet)
        self.assertIn("language PL/EN", sheet)


class VoiceConfig(unittest.TestCase):
    def test_save_voice_id_per_language(self):
        with open(CFG_PATH, encoding="utf-8") as f:
            text = f.read()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "commands.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            save_voice_id(p, "pl-1", "pl")
            save_voice_id(p, "en-1", "en")
            cfg = json.load(open(p, encoding="utf-8"))
            self.assertEqual(cfg["persona"]["tts"]["voice_id"], "pl-1")
            self.assertEqual(cfg["persona"]["languages"]["en"]["tts"]["voice_id"], "en-1")
            self.assertEqual(cfg["persona"]["tts"]["voices"][0]["voice_id"], "NacdHGUYR1k3M0FAbAia")   # the list is untouched

    def test_voices_list_has_both_languages(self):
        voices = P["tts"]["voices"]
        self.assertTrue([v for v in voices if v["language"] == "pl"])
        self.assertTrue([v for v in voices if v["language"] == "en"])
        self.assertEqual(EN["tts"]["language"], "en")
        self.assertIn("Speak English", EN["tts"]["openai"]["instructions"])


class Hormones(unittest.TestCase):
    def test_theme_in_both_languages_without_medical_detail(self):
        import re
        pl = [t for t in P["narration"]["themes"] if t.startswith("hormony")]
        en = [t for t in EN["narration"]["themes"] if t.startswith("hormones")]
        self.assertTrue(pl and en)
        lines = P["narration"]["pool"]["seed"][pl[0]] + EN["narration"]["pool"]["seed"][en[0]]
        self.assertGreaterEqual(len(lines), 12)
        for l in lines:
            self.assertFalse(re.search(r"\\b(mg|ml|mcg|estradiol|spiro|cypro|finasteryd|dawk|dose)\\w*", l, re.I), l)
            self.assertNotIn("?", l)
            self.assertLessEqual(len(l.split()), 16, l)


if __name__ == "__main__":
    unittest.main()


class Slips(WithHer):
    def test_a_polish_line_in_english_is_replaced(self):
        from controller import in_language
        self.assertFalse(in_language("Nie dostajesz nic. Przeproś.", "en"))
        self.assertFalse(in_language("Poproś ładniej.", "en"))
        self.assertTrue(in_language("Not yet. Beg nicer.", "en"))
        self.assertTrue(in_language("No? You get nothing. Apologize.", "en"))
        self.assertFalse(in_language("Not yet. Say it properly, princess.", "pl"))
        self.assertTrue(in_language("Jeszcze nie. Poproś ładniej.", "pl"))
        self.c.set_language("en")
        self.say("darling, cum on me", request="lube_fire", denied="Poproś ładniej.")
        line = self.said[-1][0]
        self.assertTrue(in_language(line, "en"), line)
        self.assertNotIn("Poproś", line)
        self.say("darling, i'm yours", request="none", granted="Wiem, mała. Chodź bliżej.", denied="Nie.")
        self.assertTrue(in_language(self.said[-1][0], "en"), self.said[-1][0])

    def test_the_prompt_tells_her_to_translate_the_examples(self):
        self.c.set_language("en")
        self.assertIn("nigdy nie cytuj ich po polsku", build_system_prompt("x", INTENTS, ["faster"], self.c._persona_state()))
