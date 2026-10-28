"""Wake word, synonyms, keyboard shortcuts and the speech detector of the continuous listening."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from audio import SegmentDetector  # noqa: E402
from controller import Controller  # noqa: E402
from hotkeys import cheat_sheet, caption, key_name  # noqa: E402
from matcher import match  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def controller(**kw):
    runs = []
    wake = CFG["wake"]
    c = Controller(INTENTS, runs.append, clock=Clock(), wake_words=wake["words"], wake_min_similarity=wake["min_similarity"], **kw)
    return c, runs


class WakeWord(unittest.TestCase):
    def test_needs_the_wake_word(self):
        c, runs = controller()
        for t in ["szybciej", "spuść się na mnie", "stop", "tryb trzy"]:
            self.assertEqual(c.handle(t).status, "ignored", t)
        self.assertEqual(runs, [])

    def test_with_the_wake_word(self):
        c, runs = controller()
        for t, intent in [("Kochanie, szybciej", "faster"), ("kochanie wolniej.", "slower"), ("Kochanie! Głębiej", "deeper"),
                          ("kochanie płycej", "shallower"), ("kochanie spuść się na mnie", "lube_fire"), ("kochanie tryb trzy", "mode_3")]:
            self.assertEqual(c.handle(t).intent, intent, t)

    def test_wake_word_typos_and_wake_word_only(self):
        c, runs = controller()
        self.assertEqual(c.handle("kochaniee szybciej").intent, "faster")
        self.assertEqual(c.handle("kochanie").detail, "wake word only")
        self.assertEqual(c.handle("kocham cię szybciej").status, "ignored")
        self.assertEqual(c.handle("Rochanie, wolniej").status, "executed")   # typical mis-hearing, still close

    def test_wake_word_inside_a_sentence_is_not_enough(self):
        c, runs = controller()
        self.assertEqual(c.handle("nie kochanie to nie tak szybciej").status, "ignored")
        self.assertEqual(runs, [])

    def test_dialogue_with_wake_word_runs_nothing(self):
        c, runs = controller()
        for t in ["kochanie jak ci minął dzień", "kochanie chodź tu", "kochanie dziękuję"]:
            self.assertEqual(c.handle(t).status, "ignored", t)
        self.assertEqual(runs, [])


class Synonyms(unittest.TestCase):
    def check(self, intent, phrases):
        for p in phrases:
            m, reason = match(p, INTENTS)
            self.assertIsNotNone(m, f"{p}: {reason}")
            self.assertEqual(m.intent.id, intent, p)

    def test_speed(self):
        self.check("faster", ["szybciej", "prędzej", "przyspiesz", "mocniej", "dawaj", "więcej", "jeszcze szybciej"])
        self.check("slower", ["wolniej", "zwolnij", "spokojniej", "delikatniej", "mniej", "powoli"])

    def test_depth(self):
        self.check("deeper", ["głębiej", "głęboko", "do końca", "w głąb"])
        self.check("shallower", ["płycej", "płytko", "nie tak głęboko", "mniej głęboko", "wyjdź trochę"])

    def test_modes_and_custom_modes(self):
        self.check("mode_1", ["tryb jeden", "tryb pierwszy", "program jeden", "jedynka", "włącz tryb jeden"])
        self.check("mode_8", ["tryb osiem", "tryb ósmy", "ósemka", "numer osiem"])
        self.check("mode_a", ["tryb a", "na maksa", "najszybciej", "maksymalnie"])
        self.check("mode_b", ["tryb b", "najwolniej", "na minimum", "tryb minimalny"])

    def test_back_to_the_script_and_lube(self):
        self.check("script", ["wróć do filmu", "wróć do skryptu", "wyłącz tryb"])
        self.check("normal", ["normalnie", "zresetuj", "bez zmian"])
        self.check("lube_fire", ["spuść się", "spuść się na mnie", "dojdź na mnie"])
        self.check("lube_many", ["spuść się na mnie kilka razy", "strzel kilka razy"])
        self.check("lube_two", ["spuść się na mnie dwa razy"])

    def test_one_word_commands_are_not_hidden_in_other_phrases(self):
        for t in ["bardzo szybko i jeszcze mocniej", "mniej więcej", "dawaj dawaj dawaj mi to"]:
            m, _ = match(t, INTENTS)
            self.assertIsNone(m, t)

    def test_every_phrase_matches_only_its_own_intent(self):
        for intent in INTENTS:
            for phrase in intent.phrases:
                m, reason = match(phrase, INTENTS)
                self.assertIsNotNone(m, f"{phrase}: {reason}")
                self.assertEqual(m.intent.id, intent.id, phrase)


class Keys(unittest.TestCase):
    def test_every_key_points_at_an_intent(self):
        ids = {i.id for i in INTENTS} | {"@mute"}
        for key, target in CFG["keys"].items():
            self.assertIn(target, ids, key)

    def test_press_runs_the_intent_at_once_without_wake_word_or_cooldown(self):
        c, runs = controller()
        for _ in range(3):
            self.assertEqual(c.press("faster").status, "executed")
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0][0]["action"], "{target}::Hismith::Speed::Faster")

    def test_press_unknown_intent_and_dry_run(self):
        c, runs = controller()
        self.assertEqual(c.press("nope").status, "ignored")
        c, runs = controller(dry_run=True)
        self.assertEqual(c.press("lube_fire").status, "dry-run")
        self.assertEqual(runs, [])

    def test_cheat_sheet_lists_all_keys(self):
        sheet = cheat_sheet(CFG["keys"], INTENTS)
        for text in ["Num 1", "Num 8", "Num 9", "Num 0", "Num +", "Num -", "Num *", "Num /", "Num .", "F8", "Pause", "szybciej"]:
            self.assertIn(text, sheet)

    def test_numpad_virtual_keys(self):
        class K:
            def __init__(self, vk, name=None):
                self.vk, self.name = vk, name
        self.assertEqual(key_name(K(0x61)), "num1")
        self.assertEqual(key_name(K(0x6B)), "num_add")
        self.assertEqual(key_name(K(0x77, "f8")), "f8")
        self.assertEqual(caption("num_decimal"), "Num .")


def block(level_db, samples=320, rate=16000):
    """a 20 ms block of noise at about the given RMS level (dBFS)"""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(samples)
    x = x / np.sqrt(np.mean(x * x)) * (10 ** (level_db / 20)) * 32768
    return np.clip(x, -32768, 32767).astype("int16")


class Detector(unittest.TestCase):
    def feed(self, det, level_db, seconds):
        out = []
        for _ in range(int(seconds / 0.02)):
            pcm = det.feed(block(level_db))
            if pcm:
                out.append(pcm)
        return out

    def make(self, **kw):
        det = SegmentDetector(**kw)
        self.feed(det, -55, 2.0)             # calibration on a quiet room
        return det

    def seconds(self, pcm):
        return len(pcm) / 2 / 16000

    def test_speech_between_silence_is_one_segment(self):
        det = self.make()
        out = self.feed(det, -25, 1.2) + self.feed(det, -55, 1.0)
        self.assertEqual(len(out), 1)
        self.assertGreater(self.seconds(out[0]), 1.0)
        self.assertLess(self.seconds(out[0]), 2.6)

    def test_silence_gives_nothing(self):
        det = self.make()
        self.assertEqual(self.feed(det, -55, 3.0), [])

    def test_short_click_is_dropped(self):
        det = self.make()
        out = self.feed(det, -20, 0.12) + self.feed(det, -55, 1.0)
        self.assertEqual(out, [])

    def test_two_utterances(self):
        det = self.make()
        out = self.feed(det, -25, 1.0) + self.feed(det, -55, 1.0) + self.feed(det, -25, 1.0) + self.feed(det, -55, 1.0)
        self.assertEqual(len(out), 2)

    def test_long_speech_is_cut_at_max_seconds(self):
        det = self.make(max_seconds=2.0)
        out = self.feed(det, -25, 5.0)
        self.assertGreaterEqual(len(out), 2)
        for pcm in out:
            self.assertLessEqual(self.seconds(pcm), 2.2)

    def test_pre_roll_keeps_the_beginning(self):
        det = self.make(pre_roll=0.3)
        out = self.feed(det, -25, 1.0) + self.feed(det, -55, 1.0)
        self.assertEqual(len(out), 1)
        self.assertGreater(self.seconds(out[0]), 1.0 + 0.2)   # speech + pre-roll + the closing silence

    def test_noisy_room_raises_the_threshold(self):
        det = SegmentDetector()
        self.feed(det, -35, 2.0)              # calibration in a noisy room
        self.assertGreater(det.threshold_db, -30)
        self.assertEqual(self.feed(det, -35, 2.0), [])

    def test_fixed_threshold(self):
        det = SegmentDetector(threshold_db=-40.0)
        self.feed(det, -60, 2.0)
        self.assertEqual(det.threshold_db, -40.0)


if __name__ == "__main__":
    unittest.main()
