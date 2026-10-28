"""The game of tenderness and sharpness: tone per turn, the vocabulary by tone, the harder dice, unsolicited moves."""
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
from tts import stream_openai  # noqa: E402
from voice_control import load_config  # noqa: E402

CFG, INTENTS = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))
P = CFG["persona"]
VOC = P["vocabulary"]
SOFT, SHARP = P["tone"]["soft_categories"], P["tone"]["sharp_categories"]


def make(seed=1, runs=None, **cfg):
    persona_cfg = dict(P, **cfg)
    return Controller(INTENTS, (runs if runs is not None else []).append, clock=Clock(), wake_words=CFG["wake"]["words"],
                      persona=Persona("x", api_key="k"), persona_cfg=persona_cfg, lock_cfg=CFG["lock"], rng=random.Random(seed))


def tone_cfg(**kw):
    return dict(P["tone"], **kw)


class ToneRoll(unittest.TestCase):
    def sim(self, stage, n=4000, **kw):
        c = make(seed=7, tone=tone_cfg(twist_p=0.0, soft_first_turns=0, **kw))
        tones, harsh = [], []
        for _ in range(n):
            t, h = c._roll_tone(stage)
            tones.append(t)
            harsh.append(h)
        return tones, harsh

    def test_sharp_grows_with_the_stage_and_stage_zero_is_always_tender(self):
        fractions = []
        for stage in range(5):
            tones, _ = self.sim(stage)
            fractions.append(tones.count("ostry") / len(tones))
        self.assertEqual(fractions[0], 0.0)
        self.assertEqual(fractions, sorted(fractions))
        self.assertLess(fractions[1], fractions[2] - 0.05)
        self.assertGreater(fractions[4], 0.3)
        self.assertLess(fractions[4], 0.5)                      # tender stays at least half of the game even at the top

    def test_never_more_than_two_sharp_in_a_row(self):
        tones, _ = self.sim(4)
        run = best = 0
        for t in tones:
            run = run + 1 if t == "ostry" else 0
            best = max(best, run)
        self.assertLessEqual(best, P["tone"]["max_sharp_run"])

    def test_tender_follows_sharp_more_often(self):
        tones, _ = self.sim(4)
        after_sharp = [b for a, b in zip(tones, tones[1:]) if a == "ostry"]
        after_tender = [b for a, b in zip(tones, tones[1:]) if a == "czuły"]
        self.assertLess(after_sharp.count("ostry") / len(after_sharp), after_tender.count("ostry") / len(after_tender))

    def test_the_first_turns_are_tender(self):
        for seed in range(20):
            c = make(seed=seed, tone=tone_cfg(twist_p=0.0, sharp_p=[1, 1, 1, 1, 1]))
            self.assertEqual([c._roll_tone(4)[0] for _ in range(P["tone"]["soft_first_turns"])], ["czuły"] * P["tone"]["soft_first_turns"])

    def test_a_twist_surprises_both_ways(self):
        c = make(seed=3, tone=tone_cfg(twist_p=1.0, soft_first_turns=0, sharp_p=[0, 0, 0, 0, 0]))
        self.assertEqual(c._roll_tone(0)[0], "ostry")           # sharp out of nowhere at the very start
        c = make(seed=3, tone=tone_cfg(twist_p=1.0, soft_first_turns=0, sharp_p=[1, 1, 1, 1, 1], max_sharp_run=5))
        self.assertEqual(c._roll_tone(4)[0], "czuły")           # tender at the peak

    def test_a_twist_never_breaks_the_run_limit(self):
        c = make(seed=3, tone=tone_cfg(twist_p=1.0, soft_first_turns=0, sharp_p=[1] * 5, max_sharp_run=2))
        c._tones = ["ostry", "ostry"]
        self.assertEqual(c._roll_tone(4)[0], "czuły")

    def test_the_harshest_words_only_at_the_top_stage(self):
        for stage in range(4):
            _, harsh = self.sim(stage, harsh_p=1.0)
            self.assertLessEqual(max(harsh), 2, stage)
        _, harsh = self.sim(4, harsh_p=0.3)
        self.assertIn(3, harsh)
        self.assertTrue(0.05 < harsh.count(3) / sum(1 for h in harsh if h) < 0.6)
        self.assertEqual(self.sim(0)[1], [0] * 4000)

    def test_tone_and_harshness_reach_the_state_and_the_voice(self):
        c = make(tone=tone_cfg(twist_p=0.0, soft_first_turns=0, sharp_p=[1] * 5, max_sharp_run=9, harsh_p=1.0))
        c._heat = 50
        c._heat_last = c.clock()
        state = c._persona_state()
        self.assertEqual((state["ton_tury"], state["ostrosc"]), ("ostry", 3))
        self.assertEqual(c._tone_now, ("ostry", 3))


class Vocabulary(unittest.TestCase):
    def test_the_gentle_lines_of_the_game_are_there(self):
        tender = " | ".join(t for cat in SOFT for items in VOC[cat].values() for t in items).lower()
        for needle in ("możesz już odpuścić", "nie kontrolujesz tego", "odpuść.", "jesteś moja"):
            self.assertIn(needle, tender)

    def test_every_gentle_category_has_lines_for_every_stage(self):
        for cat in SOFT:
            if cat == "pochwały":
                continue
            for stage in range(5):
                self.assertGreaterEqual(len(VOC[cat].get(str(stage), [])), 2, f"{cat} {stage}")
        for cat in SOFT:
            self.assertNotIn(cat, SHARP)

    def test_the_harsh_bank_exists_and_is_for_the_top_stage_only(self):
        bank = VOC[P["tone"]["harsh_category"]]
        self.assertEqual(list(bank), ["4"])
        self.assertGreaterEqual(len(bank["4"]), 6)
        for cat in SOFT:
            sharp_words = " ".join(t for items in VOC[cat].values() for t in items).lower()
            for word in ("szmat", "dziwk", "suk", "ssij", "połykaj"):
                self.assertNotIn(word, sharp_words, f"{cat} must stay tender")

    def test_tender_turn_draws_only_tender_phrases(self):
        c = make(seed=4)
        allowed = {t for cat in SOFT for items in VOC[cat].values() for t in items}
        for stage in range(5):
            for _ in range(30):
                ins = c._inspiration(stage, "czuły", 0)
                self.assertEqual(len(ins), 3)
                self.assertEqual(len({i["rodzaj"] for i in ins}), 3)
                for i in ins:
                    self.assertIn(i["rodzaj"], SOFT)
                    self.assertIn(i["fraza"], allowed)

    def test_sharp_turn_draws_two_sharp_and_one_tender_contrast(self):
        c = make(seed=5)
        for stage, harsh in ((1, 1), (2, 2), (3, 2), (4, 2)):
            for _ in range(10):
                ins = c._inspiration(stage, "ostry", harsh)
                kinds = [i["rodzaj"] for i in ins]
                self.assertEqual(kinds.count("kontrast"), 1)
                self.assertEqual(len([k for k in kinds if k in SHARP]), 2)

    def test_harshness_three_brings_the_harsh_bank(self):
        c = make(seed=6)
        hits = 0
        for _ in range(12):
            ins = c._inspiration(4, "ostry", 3)
            hits += any(i["rodzaj"] == P["tone"]["harsh_category"] for i in ins)
        self.assertGreaterEqual(hits, 5)       # the bank runs dry for a while (no repeats), then the sharp categories fill in

    def test_no_repeats_across_tender_and_sharp_turns(self):
        c = make(seed=8)
        seen = []
        for i in range(10):
            seen += [x["fraza"] for x in c._inspiration(3, "czuły" if i % 2 else "ostry", 2)]
        w = P["inspiration"]["avoid_repeat"]
        for i in range(len(seen) - w):
            self.assertEqual(len(set(seen[i:i + w])), w)


class HarderDice(unittest.TestCase):
    def asks_until_granted(self, request, n=1500, **cfg):
        c = make(seed=11, **cfg)
        total = 0
        for i in range(n):
            c._asks.clear()
            asks = 0
            while True:
                granted, _, _ = c._decide(request, type("T", (), {"confession": False, "thanked": False})())
                asks += 1
                if granted:
                    break
                c._asks[request] = c._asks.get(request, 0) + 1
            total += asks
        return total / n

    def test_the_lube_launcher_takes_many_requests(self):
        mean = self.asks_until_granted("lube_fire")
        self.assertGreater(mean, 4.0)
        self.assertLess(mean, 8.01)

    def test_mild_requests_are_easier_than_the_lube_launcher(self):
        self.assertLess(self.asks_until_granted("calm"), 3.3)
        self.assertLess(self.asks_until_granted("calm"), self.asks_until_granted("lube_fire"))

    def test_difficulty_scales_the_chances_but_the_last_ask_is_sure(self):
        base = self.asks_until_granted("lube_fire")
        self.assertGreater(self.asks_until_granted("lube_fire", difficulty=0.5), base)
        self.assertLess(self.asks_until_granted("lube_fire", difficulty=2.0), base)
        self.assertAlmostEqual(self.asks_until_granted("lube_fire", difficulty=0.0, n=50), P["max_asks"], delta=0.3)

    def test_it_is_harder_than_before(self):
        self.assertLessEqual(P["base_p"], 0.12)
        self.assertGreaterEqual(P["max_asks"], 8)
        self.assertLessEqual(P["base_p_overrides"]["lube_fire"], 0.06)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakePani)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()


class Unsolicited(Server):
    def setUp(self):
        FakePani.table, FakePani.calls, FakePani.mode = {}, [], "ok"
        self.runs, self.said = [], []
        self.c = make(runs=self.runs, spontaneous=dict(P["spontaneous"], turn_chance=[1.0] * 5, action_chance=[1.0] * 5, lube_chance=0.0))
        self.c.persona = Persona("x", self.base, "m", api_key="k", timeout=2)
        self.c.speak_ex = lambda line, voice: self.said.append((line, voice))

    def actions(self):
        return [s["action"].split("::", 1)[-1] for s in self.runs[-1]] if self.runs else []

    def test_a_plain_talk_turn_can_bring_an_unrequested_reward(self):
        FakePani.table["kochanie, jestem Twoja"] = turn(request="none", confession=True, grant_actions=[{"step": "faster", "times": 1}, {"step": "halt", "times": 1}])
        self.c.clock.t += 1
        o = self.c.handle("kochanie, jestem Twoja")
        self.assertEqual(o.status, "talk")
        self.assertEqual(o.extra["unsolicited"], "reward")
        self.assertEqual(self.actions(), ["Hismith::Speed::Faster"])       # halt is never hers to give

    def test_the_lube_launcher_only_late_rarely_and_with_a_gap(self):
        plan = [{"step": "lube_fire", "times": 1}, {"step": "lube_fire", "times": 1}, {"step": "faster", "times": 1}]
        for stage, chance, ok in ((2, 1.0, False), (3, 0.0, False), (3, 1.0, True)):
            c = make(runs=[], spontaneous=dict(P["spontaneous"], lube_chance=chance))
            extra = {}
            ran = c._spontaneous(plan, stage, extra, "reward")
            steps = [s["action"] for s in c.run_steps.__self__] if False else None
            used = extra.get("plan", [])
            self.assertEqual("lube_fire" in used, ok, (stage, chance))
            self.assertLessEqual(used.count("lube_fire"), 1)
        c = make(runs=[], spontaneous=dict(P["spontaneous"], lube_chance=1.0))
        self.assertTrue("lube_fire" in (lambda e: (c._spontaneous(plan, 4, e, "x"), e)[1])({}).get("plan", []))
        e = {}
        c.clock.t += 10                                    # a second shot within the gap is dropped
        c._spontaneous(plan, 4, e, "x")
        self.assertNotIn("lube_fire", e.get("plan", []))
        c.clock.t += P["spontaneous"]["lube_gap_seconds"]
        e = {}
        c._spontaneous(plan, 4, e, "x")
        self.assertIn("lube_fire", e.get("plan", []))

    def test_in_a_hold_only_the_lube_launcher(self):
        runs = []
        c = make(runs=runs, spontaneous=dict(P["spontaneous"], lube_chance=1.0))
        c._locked_until = c.clock() + 300
        e = {}
        c._spontaneous([{"step": "faster", "times": 1}, {"step": "mode_5", "times": 1}], 4, e, "tease")
        self.assertEqual(runs, [])
        c._spontaneous([{"step": "faster", "times": 1}, {"step": "lube_fire", "times": 1}], 4, e, "reward")
        self.assertEqual([s["action"].split("::", 1)[-1] for s in runs[-1]], ["Hismith::LubeLauncher::Fire"])

    def test_never_stop_calm_hold_or_a_surprise(self):
        runs = []
        c = make(runs=runs)
        c._spontaneous([{"step": s, "times": 1} for s in ("halt", "calm", "lock", "surprise", "lube_many", "hard")], 4, {}, "x")
        self.assertEqual(runs, [])

    def test_dry_run_sends_nothing_but_reports(self):
        runs = []
        c = make(runs=runs)
        c.dry_run = True
        e = {}
        self.assertTrue(c._spontaneous([{"step": "faster", "times": 1}], 2, e, "reward"))
        self.assertEqual((runs, e["unsolicited"]), ([], "reward"))

    def test_she_may_act_when_she_speaks_first(self):
        self.c.persona.nudge = lambda state, history, step_ids=None: (("Odpuść. Jesteś moja.", [{"step": "slower", "times": 1}])
                                                                       if step_ids else "Odpuść. Jesteś moja.")
        FakePani.table["kochanie, szybciej"] = turn(request="faster")
        self.c.clock.t += 1
        self.c.handle("kochanie, szybciej")
        self.runs.clear()
        self.c.clock.t += 40
        line = self.c.idle_nudge()
        self.assertEqual(line, "Odpuść. Jesteś moja.")
        self.assertEqual(self.actions(), ["Hismith::Speed::Slower"])

    def test_she_does_not_act_when_the_chance_says_no(self):
        self.c.persona_cfg = dict(P, spontaneous=dict(P["spontaneous"], action_chance=[0.0] * 5))
        seen = {}
        self.c.persona.nudge = lambda state, history, step_ids=None: seen.setdefault("ids", step_ids) or "Czemu milczysz?"
        FakePani.table["kochanie, szybciej"] = turn(request="faster")
        self.c.clock.t += 1
        self.c.handle("kochanie, szybciej")
        self.runs.clear()
        self.c.clock.t += 40
        self.c.idle_nudge()
        self.assertIsNone(seen["ids"])
        self.assertEqual(self.runs, [])


class VoiceByTone(unittest.TestCase):
    def test_openai_instructions_follow_the_tone(self):
        got = []

        class Spy:
            def synthesize(self, text, instructions=None):
                got.append(instructions)
                return b"\x00\x00" * 100
        list(stream_openai(Spy(), "x", 1, "SOFT", "SHARP"))
        list(stream_openai(Spy(), "x", 6, "SOFT", "SHARP"))
        self.assertEqual(got, ["SOFT", "SHARP"])
        self.assertEqual(P["tts"]["openai"]["instructions_soft"][:5], "Mów p")
        self.assertNotEqual(P["tts"]["openai"]["instructions_soft"], P["tts"]["openai"]["instructions_sharp"])

    def test_the_outcome_carries_the_voice_index(self):
        class Fake(Persona):
            pass
        c = make(tone=tone_cfg(twist_p=0.0, soft_first_turns=0, sharp_p=[1] * 5, max_sharp_run=9))
        c._heat, c._heat_last = 30, c.clock()
        said = []
        c.speak_ex = lambda line, voice: said.append(voice)
        from controller import Outcome
        state = c._persona_state()
        self.assertEqual(state["ton_tury"], "ostry")
        out = Outcome("talk", "x", extra={"say": "x", "stage": 3, "voice": 3 + 5})
        c._say(out)
        self.assertEqual(said, [8])


if __name__ == "__main__":
    unittest.main()
