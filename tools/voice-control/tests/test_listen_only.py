"""Listen only ("trąbka mikrofon" / the mute key): she goes on talking, you are not heard - only the safe word and the
cooldown password still work; and your voice makes her narration wait instead of being talked over."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_manners import P, Server  # noqa: E402


def actions(runs):
    return [step["action"] for steps in runs for step in steps]


class ListenOnly(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)
        self.toggles = []
        self.c.on_listen_only = self.toggles.append

    def test_the_phrase_switches_it_on_and_off_without_stopping_the_machine(self):
        o = self.c.handle("trąbka mikrofon")
        self.assertEqual(o.intent, "listen_only")
        self.assertTrue(self.c.listen_only)
        self.assertEqual(self.runs, [])                           # "trąbka" in it is not the safe word here
        self.clock.t += 5
        self.c.handle("Trąbka, mikrofon.")
        self.assertFalse(self.c.listen_only)
        self.assertEqual(self.toggles, [True, False])

    def test_heard_twice_in_a_row_it_counts_once(self):
        self.c.handle("trąbka mikrofon")
        self.clock.t += 1
        self.c.handle("trąbka mikrofon")                          # the same words twice (an echo, a repeated transcript)
        self.assertTrue(self.c.listen_only)

    def test_while_listening_only_commands_and_talk_are_ignored(self):
        self.c.toggle_listen_only(True)
        heard = self.c._last_heard
        self.clock.t += 5
        o = self.c.handle("kochanie, szybciej")
        self.assertEqual(o.status, "ignored")
        self.assertEqual(self.runs, [])
        self.assertEqual(self.c._last_heard, heard)               # her narration does not wait for what she did not hear
        self.assertEqual(self.c.prepare("kochanie, wolniej")[1].status, "ignored")

    def test_the_safe_word_and_the_password_still_work(self):
        self.c.toggle_listen_only(True)
        self.clock.t += 5
        self.c.handle("trąbka")
        self.assertTrue(any(a.endswith("::Hismith::Halt") for a in actions(self.runs)))
        self.runs.clear()
        self.clock.t += 10
        self.c.handle("trąbka uspakajająca")
        self.assertTrue(any(a.endswith("::Hismith::Cooldown") for a in actions(self.runs)))
        self.assertTrue(self.c.listen_only)

    def test_the_session_goes_on_while_only_listening(self):
        self.c.toggle_listen_only(True)
        self.clock.t += 3 * 3600
        self.assertTrue(self.c._session_on(self.clock.t))
        self.assertIsNone(self.c.idle_nudge())                    # no "say something" while you are meant to listen
        self.c.toggle_listen_only(False)
        self.assertTrue(self.c._session_on(self.clock.t))         # back to talking: the usual clock starts now

    def test_the_key_toggles_the_same_switch(self):
        o = self.c.toggle_listen_only(via="key")
        self.assertEqual((o.via, self.c.listen_only), ("key", True))

    def test_live_partials(self):
        self.assertIsNone(self.c.handle_partial("a", "trąbka mikrofon"))       # the whole phrase at once: no stop
        self.assertEqual(self.runs, [])
        self.assertEqual(self.c.handle_final("a", "trąbka mikrofon").intent, "listen_only")
        self.assertTrue(self.c.listen_only)
        self.clock.t += 5
        self.c.handle_partial("b", "trąbka")                                   # "trąbka" alone first: safety first
        self.assertTrue(any(a.endswith("::Hismith::Halt") for a in actions(self.runs)))
        self.c.handle_final("b", "trąbka mikrofon")
        self.assertFalse(self.c.listen_only)
        self.c.toggle_listen_only(True)
        self.assertIsNone(self.c.handle_partial("c", "kochanie szybciej"))     # not listening

    def test_your_voice_makes_her_narration_wait(self):
        self.clock.t += 100
        self.c.heard_voice()
        self.assertEqual(self.c._last_heard, self.clock.t)
        self.c.toggle_listen_only(True)
        self.clock.t += 100
        self.c.heard_voice()
        self.assertNotEqual(self.c._last_heard, self.clock.t)

    def test_other_words_are_not_the_toggle(self):
        for text in ("trąbka", "mikrofon", "kochanie, wyłącz mikrofon i weź mnie mocniej od tyłu", "trąbka uspakajająca"):
            self.assertFalse(self.c.is_mic_toggle(text), text)
        for text in ("trąbka mikrofon", "Trąbko, mikrofon!", "trumpet microphone", "microphone trumpet please"):
            self.assertTrue(self.c.is_mic_toggle(text), text)


if __name__ == "__main__":
    unittest.main()
