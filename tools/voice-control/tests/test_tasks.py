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
    base = dict(P, base_p=0.0, base_p_overrides={}, step_p_overrides={}, step_p=0.0, lube=dict(P["lube"], rear_share=0.5),
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
        singles = [d for d in self.c._task["done"] if len(d.split()) == 1] or TD["done_words"][:1]
        self.assertIn(singles[0], self.c.task_done_words())


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


class Rewards(Server):
    def order(self, line="Wypnij pupę. Wyżej.", done=("wypinam",), until=1e9):
        self.c._spont_task = {"line": line, "done": list(done), "side": self.c._order_side(line), "until": until}

    def test_reporting_her_own_order_pays_a_shot_or_a_push(self):
        kinds = set()
        for seed in range(12):
            self.c.rng.seed(seed)
            self.runs.clear()
            self.order()
            o = self.say("wypinam")
            self.assertEqual((o.status, o.intent), ("executed", "task_reward"))
            kinds.add(o.extra["reward"])
            actions = [st["action"] for steps in self.runs for st in steps]
            if o.extra["reward"] == "lube":
                self.assertEqual(actions, ["{target}::Hismith::Rear::LubeLauncher::Fire"])
            else:
                self.assertEqual(actions, ["{target}::Hismith::Rear::Speed::Faster"] * 2)
                self.assertTrue(10 <= o.extra["seconds"] <= 15)
                for t in self.c._reward_timers:
                    t.cancel()
            self.assertTrue(o.extra["say"])
        self.assertEqual(kinds, {"lube", "push"})

    def test_the_push_goes_back_after_its_time(self):
        self.c.persona_cfg = dict(P, narration=dict(P["narration"], tasks=dict(P["narration"]["tasks"],
                                  rewards=dict(P["narration"]["tasks"]["rewards"], lube_p={"rear": 0.0}, push_seconds=[0.05, 0.05]))))
        self.order()
        o = self.say("gotowe")
        self.assertEqual(o.extra["reward"], "push")
        self.c._reward_timers[-1].join(2)
        actions = [st["action"] for steps in self.runs for st in steps]
        self.assertEqual(actions, ["{target}::Hismith::Rear::Speed::Faster"] * 2 + ["{target}::Hismith::Rear::Speed::Slower"] * 2)

    def test_a_mouth_order_is_the_front_one(self):
        self.assertEqual(self.c._order_side("Otwórz usta szeroko i pokaż język."), "front")
        self.assertEqual(self.c._order_side("Ściskaj! Puść! Ssij głębiej!"), "front")
        self.assertEqual(self.c._order_side("Rozłóż nogi szerzej."), "rear")

    def test_too_late_or_unrelated_pays_nothing(self):
        self.order(until=0)
        self.assertNotEqual(self.say("gotowe").intent, "task_reward")
        self.order()
        self.assertNotEqual(self.say("kochanie, szybciej").intent, "task_reward")
        self.assertIsNotNone(self.c._spont_task)

    def test_her_refusal_order_comes_first(self):
        self.c.persona_cfg = cfg(1.0)
        self.say("kochanie, ruchaj mnie mocniej", request="hard")
        self.order()
        o = self.say(self.c._task["done"][0], request="none")
        self.assertEqual(o.intent, "hard")


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


class OrdersInNarration(Server):
    def setUp(self):
        super().setUp()
        import copy
        self.c.persona_cfg = copy.deepcopy(P)
        n = self.c.persona_cfg["narration"]
        n["min_gap_seconds"] = [0, 0]
        n["address"]["enabled"] = False
        n["tasks"]["enabled"] = True                             # (another test module switches them off in the shared config)
        n["tasks"]["rewards"]["enabled"] = True
        n["tasks"]["share"] = [1.0]                              # every line an order
        self.c.persona.narrate = lambda *a, **k: "Linia."
        self.say("kochanie, jestem twoja", request="none")      # a session is on
        self.said.clear()

    def first_line(self):
        for _ in range(400):
            self.clock.t += 1
            line = self.c.idle_narration()
            if line:
                return line
        self.fail("no narration")

    def test_her_order_says_it_pays_and_is_remembered(self):
        rewards = self.c.persona_cfg["narration"]["tasks"]["rewards"]
        line = self.first_line()
        self.assertTrue(line.endswith(rewards["tail"]), line)
        self.assertIsNotNone(self.c._spont_task)
        self.assertIn(self.c._spont_task["line"], line)
        o = self.say("gotowe")
        self.assertEqual(o.intent, "task_reward")
        for t in self.c._reward_timers:
            t.cancel()

    def test_bank_orders_carry_their_own_done_words(self):
        self.c.persona_cfg["narration"]["tasks"]["rewards"]["bank_share"] = 1.0
        self.first_line()
        bank = [t["line"] for v in TD["tasks"].values() for t in v]
        self.assertIn(self.c._spont_task["line"], bank)
        self.assertTrue(self.c._spont_task["done"])


class SessionFixes(Server):
    """from the session 2026-11-20 10:50-11:05"""

    def test_a_tease_goes_back_after_its_time(self):
        self.c.persona_cfg = cfg(0.0, escalation=dict(P["escalation"], tease_chance=[1.0], tease_seconds=[0.05, 0.05]))
        o = self.say("kochanie, spuść się na mnie", request="lube_fire", tease_actions=[{"step": "slower", "times": 1}])
        self.assertEqual(o.extra.get("tease"), ["slower"])
        self.c._reward_timers[-1].join(2)
        actions = [st["action"] for steps in self.runs for st in steps]
        self.assertEqual(len(actions), 2)
        self.assertTrue(actions[0].endswith("Speed::Slower") and actions[1].endswith("Speed::Faster"), actions)

    def test_repeating_her_sentence_is_doing_her_order(self):
        # 10:58 "Powtórz, głośno: jestem posłuszną sissi." -> "Jestem posłuszną sissy." went to the dice
        self.c._spont_task = {"line": "Powtórz, głośno: jestem posłuszną sissi.", "done": [], "side": "rear", "until": 1e9}
        o = self.say("Jestem posłuszną sissy.")
        self.assertEqual(o.intent, "task_reward")
        for t in self.c._reward_timers:
            t.cancel()

    def test_a_promised_shot_is_fired(self):
        # 11:02:48 "...masz teraz gorącą falę na twarz" in a plain talk turn - nothing was fired
        o = self.say("kochanie, jestem twoją szmatą", request="none", granted="Za to masz teraz gorącą falę na twarz, moja dziwko.")
        self.assertEqual((o.status, o.intent), ("executed", "lube_fire"))
        self.assertTrue(o.extra["promise"])
        self.assertEqual(len(self.runs), 1)

    def test_plain_talk_fires_nothing(self):
        o = self.say("kochanie, jestem twoja", request="none", granted="Grzeczna. Jesteś moja.")
        self.assertEqual(o.status, "talk")
        self.assertEqual(self.runs, [])

    def test_a_wish_with_nie_is_not_defiance(self):
        # 11:05:12 "Nie oszczędzaj mnie." -> punished for 90 s
        self.say("kochanie, nie oszczędzaj mnie", request="hard", defiance=True)
        self.assertEqual(self.c.penalty_remaining(), 0)
        self.say("kochanie, pierdol mnie mocniej", request="hard", defiance=True)
        self.assertEqual(self.c.penalty_remaining(), 0)
        self.say("nie chcę", request="none", defiance=True)
        self.assertGreater(self.c.penalty_remaining(), 0)

    def test_her_order_tells_the_word_and_repeat_orders_keep_the_sentence(self):
        r = P["narration"]["tasks"]["rewards"]
        self.assertIn("zrobione", r["tail"])
        from controller import repeat_target
        self.assertEqual(repeat_target(r["head_repeat"] + " Powtórz, głośno: jestem posłuszną sissi."), "jestem posłuszną sissi.")


class CumAndAddress(Server):
    """user, 2026-11-20: every cum = 5 shots, half of them behind, no "moja własność\""""

    def test_every_shot_becomes_five(self):
        self.c.shots_per_cum = 5
        self.c._run_raw([{"action": "{target}::Hismith::Rear::LubeLauncher::Fire", "args": []},
                         {"action": "{target}::Hismith::LubeLauncher::FireMany", "args": [3]},
                         {"action": "{target}::Hismith::Speed::Faster", "args": []}])
        self.assertEqual(self.runs[-1], [{"action": "{target}::Hismith::Rear::LubeLauncher::FireMany", "args": [5]},
                                         {"action": "{target}::Hismith::LubeLauncher::FireMany", "args": [5]},
                                         {"action": "{target}::Hismith::Speed::Faster", "args": []}])

    def test_a_granted_cum_without_a_side_goes_behind_about_half_the_time(self):
        self.c.shots_per_cum = 5
        self.c.persona_cfg = cfg(0.0, base_p_overrides={"lube_fire": 1.0}, base_p=1.0)
        sides = []
        for seed in range(40):
            self.c.rng.seed(seed)
            self.runs.clear()
            self.clock.t += 30
            self.say("kochanie, proszę, spuść się", request="lube_fire", side="front")
            actions = [st["action"] for steps in self.runs for st in steps if "LubeLauncher" in st["action"]]
            if actions:
                sides.append("Rear" if "::Rear::" in actions[0] else "Front")
        self.assertGreater(sides.count("Rear"), 8)
        self.assertGreater(sides.count("Front"), 8)

    def test_she_is_told_where_it_goes(self):
        self.c.persona_cfg = cfg(0.0)
        need = self.c._need_persona("kochanie, spuść się")
        self.assertIn(need.state["wytrysk_gdzie"], ("tyl", "przod"))

    def test_the_words_decide_when_he_says_it(self):
        self.c.shots_per_cum = 5
        self.c.persona_cfg = cfg(0.0, base_p_overrides={"lube_fire": 1.0}, base_p=1.0, last_second=dict(P["last_second"], enabled=False))
        for seed in range(10):
            self.c.rng.seed(seed)
            self.runs.clear()
            self.clock.t += 30
            o = self.say("kochanie, proszę, spuść mi się do ust", request="lube_fire", side="rear")
            if o.status != "executed":
                continue                                     # the dice (at most 95 %)
            actions = [st["action"] for steps in self.runs for st in steps if "LubeLauncher" in st["action"]]
            self.assertTrue(actions and "::Front::" in actions[0], actions)

    def test_moja_wlasnosc_is_never_said_as_her_name(self):
        from lines import scrub
        self.assertEqual(scrub("Dobrze, moja własność.")[0], "Dobrze, moja dziewczynko.")
        self.assertEqual(scrub("Nie teraz, moja właścicielko. Czekaj.")[0], "Nie teraz, moja dziewczynko. Czekaj.")
        self.assertEqual(scrub("Jesteś moją własnością.")[0], "Jesteś moją własnością.")
        self.assertNotIn("moja własność", CFG["persona"]["address"]["names"])


class SecondSession(Server):
    """from the session 2026-11-20 11:28-11:37"""

    def test_a_mode_she_reads_into_proszę_is_not_run(self):
        # 11:32:29 "Proszę cię, ładnie." -> mode_8 ran (the model guessed it from the history)
        self.c.persona_cfg = cfg(0.0, base_p=1.0, max_p=1.0)
        o = self.say("kochanie, proszę cię, ładnie", request="mode_8")
        self.assertNotEqual(o.intent, "mode_8")
        self.assertEqual(o.extra.get("mode_request_dropped"), "mode_8")
        self.assertFalse(any("Mode::" in st["action"] for steps in self.runs for st in steps))

    def test_his_own_mode_request_runs(self):
        self.c.persona_cfg = cfg(0.0, base_p=1.0, max_p=1.0)
        o = self.say("kochanie, włącz tryb ósmy", request="mode_8")
        self.assertEqual((o.status, o.intent), ("executed", "mode_8"))
        o = self.say("kochanie, wróć do filmu", request="script")
        self.assertEqual((o.status, o.intent), ("executed", "script"))

    def test_her_plan_never_switches_the_mode(self):
        # 11:34:44 "Uwielbiam, jak mnie jebią w dupę." -> improvise ran mode_8 + faster x2
        self.c.persona_cfg = cfg(0.0, base_p=1.0, max_p=1.0)
        self.say("kochanie, proszę, uwielbiam, jak mnie jebią w dupę", request="improvise",
                 grant_actions=[{"step": "mode_8", "times": 1}, {"step": "faster", "times": 2}])
        actions = [st["action"] for steps in self.runs for st in steps]
        self.assertTrue(actions)
        self.assertFalse(any("Mode::" in a for a in actions), actions)

    def test_every_ask_is_granted_with_the_live_config(self):
        from test_manners import CFG as _C  # noqa: F401  (the pinned test config is not the live one)
        from voice_control import load_config
        live, _ = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))
        self.c.persona_cfg = live["persona"]
        self.c.wake_cfg_optional_for_commands = True             # as in the tool (wake.optional_for_commands)
        for text, req in (("kochanie, spuść się", "lube_fire"), ("kochanie, ruchaj mnie mocniej", "hard"),
                          ("spuść się na mnie", "lube_fire"), ("kochanie, wróć do filmu", "script")):
            self.clock.t += 60
            o = self.say(text, request=req, demand="none")
            self.assertEqual(o.status, "executed", (text, o.detail))

    def test_a_claim_of_a_change_that_did_not_happen_is_not_said(self):
        # 11:35:15 talk: "Masz, misiu. Tryb ósmy i podwójne przyspieszenie. Bierz." - nothing ran
        o = self.say("kochanie, jestem ich dziwką", request="none", granted="Masz, misiu. Tryb ósmy i podwójne przyspieszenie. Bierz.")
        self.assertEqual(o.status, "talk")
        self.assertNotIn("Tryb", o.extra.get("say", ""))
        self.assertTrue(o.extra.get("claim_dropped"))
        self.assertEqual(self.runs, [])


class FilmStage(Server):
    """user, 2026-11-20: gentle while the film warms up, then from the middle of the scale up with the script's pace"""

    class FakeScene:
        def __init__(self):
            self.pos, self.dur, self.tempo = 0.0, 1800.0, None

        def progress(self):
            return (self.pos, self.dur)

        def now(self):
            return {"act": "fucked", "tempo": self.tempo} if self.tempo else None

        def pending(self):
            return None

    def setUp(self):
        super().setUp()
        import copy
        self.c.persona_cfg = copy.deepcopy(self.c.persona_cfg)
        esc = self.c.persona_cfg["escalation"]
        esc["film"] = {"enabled": True, "warmup_share": 0.1, "gentle_share": 0.35, "warmup_seconds": [90, 300],
                       "by_tempo": {"still": 2, "slow": 2, "medium": 3, "fast": 4, "max": 4}, "default_stage": 2,
                       "drop_after_seconds": 45}
        self.s = self.FakeScene()
        self.c.scene = self.s

    def test_gentle_while_it_warms_up(self):
        self.c._heat = 1000.0                                   # however hot the talk was
        self.s.pos = 40                                         # 30 min film: warm-up 3 min, gentle the first ~63 s
        self.assertEqual(self.c.stage(), 0)
        self.s.pos = 120
        self.assertEqual(self.c.stage(), 1)
        self.s.pos = 200
        self.assertEqual(self.c.stage(), 2)                     # no pace known: the middle

    def test_then_the_pace_decides_from_the_middle_up(self):
        self.s.pos = 900
        for tempo, stage in (("slow", 2), ("medium", 3), ("fast", 4)):
            self.s.tempo = tempo
            self.assertEqual(self.c.stage(), stage)

    def test_it_comes_down_slowly(self):
        self.s.pos, self.s.tempo = 900, "fast"
        self.assertEqual(self.c.stage(), 4)
        self.s.tempo = "slow"
        self.clock.t += 10
        self.assertEqual(self.c.stage(), 4)
        self.clock.t += 50
        self.assertEqual(self.c.stage(), 3)
        self.clock.t += 50
        self.assertEqual(self.c.stage(), 2)

    def test_no_film_the_heat_decides(self):
        self.c.scene = None
        self.c._heat = 1000.0
        self.assertEqual(self.c.stage(), 4)
