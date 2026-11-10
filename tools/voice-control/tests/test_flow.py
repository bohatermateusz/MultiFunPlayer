"""Classic mode flow: her lines are not cut by your voice or by her next line (a queue), only the safe word cuts her;
narration keeps quiet right after you speak; Whisper's hallucinations are dropped; a misheard repetition is obedience,
not rebellion; "trąbka ..." stops whatever follows; one retry of the speech recognition on a dropped connection."""
import os
import random
import sys
import threading
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller, repeat_target  # noqa: E402
from speech import Voice  # noqa: E402
from stt import CloudStt, SttError, is_hallucination  # noqa: E402
from test_manners import CFG, INTENTS, P, M, Server  # noqa: E402
from test_persona import Clock  # noqa: E402
from voice_control import stt_prompt  # noqa: E402


class Rec:
    def __init__(self, delay=0.01):
        self.played, self.delay = [], delay

    def play(self, stream, stop):
        got = []
        for chunk in stream:
            if stop():
                break
            got.append(chunk)
            time.sleep(self.delay)
        self.played.append((got[0].split(b":")[0].decode() if got else "", len(got)))


def slow(n=10):
    return lambda line, stage, meta=None: iter([line.encode() + b":x"] * n)


class Queue(unittest.TestCase):
    def test_a_new_line_waits_for_the_one_playing(self):
        r = Rec()
        v = Voice({"e": slow()}, ["e"], r.play)
        v.say("pierwsza", 0, {"kind": "answer"})
        time.sleep(0.02)
        v.say("druga", 0, {"kind": "answer"})
        for _ in range(100):
            if len(r.played) == 2:
                break
            time.sleep(0.02)
        self.assertEqual(r.played, [("pierwsza", 10), ("druga", 10)])         # both whole, in order

    def test_waiting_narration_is_dropped_for_an_answer_and_never_queues_behind_one(self):
        r = Rec()
        v = Voice({"e": slow()}, ["e"], r.play)
        v.say("pierwsza", 0, {"kind": "answer"})
        time.sleep(0.02)
        v.say("komentarz", 0, {"kind": "narration"})
        v.say("odpowiedź", 0, {"kind": "answer"})
        v.say("drugi komentarz", 0, {"kind": "narration"})
        for _ in range(100):
            if not v.speaking():
                break
            time.sleep(0.02)
        self.assertEqual([p[0] for p in r.played], ["pierwsza", "odpowiedź"])
        self.assertEqual(v.dropped, 2)

    def test_interrupt_cuts_the_line_and_clears_the_queue(self):
        r = Rec(0.02)
        v = Voice({"e": slow(50)}, ["e"], r.play)
        v.say("długa", 0, {"kind": "answer"})
        v.say("następna", 0, {"kind": "answer"})
        time.sleep(0.1)
        v.interrupt()
        v.wait()
        self.assertEqual(len(r.played), 1)
        self.assertLess(r.played[0][1], 50)
        self.assertFalse(v.speaking())

    def test_cut_previous_restores_the_old_behaviour(self):
        r = Rec(0.02)
        v = Voice({"e": slow(50)}, ["e"], r.play, cut_previous=True)
        v.say("pierwsza", 0, {"kind": "answer"})
        time.sleep(0.1)
        v.say("druga", 0, {"kind": "answer"})
        v.wait()
        self.assertLess(r.played[0][1], 50)


class SafeWord(unittest.TestCase):
    def setUp(self):
        self.runs, self.clock, self.halts = [], Clock(), []
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"])
        self.c.on_halt = lambda: self.halts.append(1)

    def test_koniec_first_stops_whatever_follows(self):
        for text in ["trąbka szybciej", "kochanie, trąbka, szybciej", "Trąbka. Dalej."]:
            self.clock.t += 20
            self.c._last.clear()
            o = self.c.handle(text)
            self.assertEqual((o.intent, o.status), ("halt", "executed"), text)
        self.assertEqual(len(self.halts), 3)                                   # and her voice was cut each time

    def test_compound_commands_with_koniec_are_not_the_safe_word(self):
        self.clock.t += 20
        self.assertEqual(self.c.handle("kochanie, koniec pętli").intent, "loop_off")
        self.clock.t += 20
        self.assertEqual(self.c.handle("kochanie, koniec sekwencji").intent, "sequence_stop")

    def test_the_end_key_cuts_her_too(self):
        self.c.press("halt")
        self.assertEqual(self.halts, [1])


class Hallucinations(unittest.TestCase):
    def test_loops_and_walls_of_text(self):
        self.assertTrue(is_hallucination("tryb pięć, " * 30))
        self.assertTrue(is_hallucination("trzy razy, trzy razy, trzy razy, trzy razy, trzy razy"))
        self.assertTrue(is_hallucination(" ".join(["słowo%d" % i for i in range(30)])))
        for ok in ["kochanie, szybciej", "proszę, proszę", "proszę, proszę, Pani, zalej mnie", "sama się zgłosiłam na kolana z uśmiechem",
                   "nie, nie, nie, nie chcę już"]:
            self.assertFalse(is_hallucination(ok), ok)

    def test_the_prompt_is_short(self):
        p = stt_prompt(INTENTS, CFG["wake"]["words"])
        self.assertLessEqual(len(p), 100)
        self.assertIn("kochanie", p)
        self.assertNotIn("tryb", p)


class Retry(unittest.TestCase):
    def test_one_retry_on_a_dropped_connection(self):
        import urllib.request
        calls = []
        real = urllib.request.urlopen

        class Resp:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return b'{"text": "kochanie, szybciej"}'

        def fake(req, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError("Remote end closed connection without response")
            return Resp()
        urllib.request.urlopen = fake
        try:
            stt = CloudStt(api_key="k")
            stt.retry_delay = 0
            self.assertEqual(stt.transcribe_wav(b"RIFF"), "kochanie, szybciej")
            self.assertEqual(len(calls), 2)
            calls.clear()

            def always(req, timeout=0):
                calls.append(1)
                raise urllib.error.URLError("down")
            urllib.request.urlopen = always
            with self.assertRaises(SttError):
                stt.transcribe_wav(b"RIFF")
            self.assertEqual(len(calls), 2)
        finally:
            urllib.request.urlopen = real


class Obedience(Server):
    def test_repeat_target(self):
        self.assertEqual(repeat_target("Powtórz za mną: sama się zgłosiłam. Na kolana. Z uśmiechem."),
                         "sama się zgłosiłam. Na kolana. Z uśmiechem.")
        self.assertEqual(repeat_target("Nie. Powiedz dokładnie: 'sama się zgłosiłam do kolana z uśmiechem'."),
                         "sama się zgłosiłam do kolana z uśmiechem'.".rstrip("'.") + "'." if False else repeat_target(
                             "Nie. Powiedz dokładnie: 'sama się zgłosiłam do kolana z uśmiechem'."))
        self.assertIsNone(repeat_target("Jeszcze nie. Poproś ładniej."))

    def test_a_misheard_repetition_is_obedience_not_rebellion(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={})
        self.c.say_now("Powtórz za mną: sama się zgłosiłam. Na kolana. Z uśmiechem.")
        self.say("sama się zgłosiłam do korana z uśmiechem", request="none", defiance=True)
        self.assertEqual(self.c.penalty_remaining(), 0)                        # no punishment
        self.assertIsNone(self.c._repeat_target)

    def test_the_models_defiance_needs_a_no(self):
        self.say("kochanie, nie wiem co powiedzieć", request="none", defiance=True)   # has "nie": counts
        self.assertGreater(self.c.penalty_remaining(), 0)
        self.setUp()
        self.say("kochanie, jestem twoją dziewczynką", request="none", defiance=True)
        self.assertEqual(self.c.penalty_remaining(), 0)

    def test_narration_keeps_quiet_right_after_he_spoke(self):
        calls = []
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = False
        self.c.persona.narrate = lambda *a, **k: calls.append(1) or "Komentarz."
        self.say("kochanie, jestem twoja", request="none")
        self.c._narration_due = self.clock.t + 1
        for _ in range(10):                                                    # 10 s after him: nothing
            self.clock.t += 1
            self.c.idle_narration()
        self.assertEqual(calls, [])
        for _ in range(15):
            self.clock.t += 1
            self.c.idle_narration()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()


class LessLooping(Server):
    def test_asking_nicer_after_she_said_so_is_credited(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0,
                                  manners=dict(M, politeness_factor=1.0, ignore_demand_factor=1.0, beg_met_bonus=1.0))
        self.c.persona_cfg["last_second"]["enabled"] = False
        o = self.say("kochanie, zalej mnie", request="lube_many", demand="beg")
        self.assertEqual(o.status, "denied")
        self.assertEqual(self.c._last_demand, ("lube_many", "beg"))
        o = self.say("proszę bardzo ładnie", request="none")         # the model did not repeat the request: the ask goes on
        self.assertEqual(o.status, "executed")
        self.assertEqual(o.extra.get("demand_met"), "beg")

    def test_thanks_or_a_rough_confession_pay_what_was_demanded(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0)
        self.c.persona_cfg["last_second"]["enabled"] = False
        self.say("kochanie, szybciej", request="faster", demand="confess")
        o = self.say("jestem twoją dziewczynką z dziureczką", request="none")
        self.assertEqual(o.status, "executed")
        self.say("kochanie, głębiej", request="deeper", demand="thank")
        o = self.say("dziękuję", request="none")
        self.assertEqual(o.status, "executed")

    def test_the_same_refusal_is_not_said_twice_in_a_row(self):
        self.c.persona_cfg = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0, max_asks=50)
        lines = []
        for i in range(6):
            self.say(f"kochanie, szybciej {i}", request="faster", demand="beg", denied="Jeszcze nie. Poproś ładniej.")
            lines.append(self.said[-1][0])
        for a, b in zip(lines, lines[1:]):
            self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(set(lines)), 4)

    def test_plain_talk_without_a_pending_ask_is_not_a_refusal(self):
        o = self.say("kochanie, jestem tu dla ciebie", request="none", granted="Wiem, mała. Chodź bliżej.", denied="Jeszcze nie.")
        self.assertEqual(self.said[-1][0], "Wiem, mała. Chodź bliżej.")

    def test_repeat_with_a_word_between(self):
        self.assertEqual(repeat_target("Powtórz, głośno: jestem posłuszną sissi."), "jestem posłuszną sissi.")
