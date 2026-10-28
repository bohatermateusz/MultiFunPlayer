"""The persona ("Pani") against a fake chat server: requests go through her dice, conversation, the reply
window, the hold, and the bounds on what she may run. No network, no key."""
import json
import os
import random
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from persona import Persona, PersonaError, build_schema, build_system_prompt  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)
STEP_IDS = [i.id for i in INTENTS if i.improvise]


def turn(request="none", granted="Dobra dziewczynka. Masz.", denied="Jeszcze nie. Poproś ładniej.", **kw):
    t = {"addressed": True, "request": request, "confession": False, "thanked": False, "line_if_granted": granted,
         "line_if_denied": denied, "demand": "beg", "grant_actions": [], "tease_actions": []}
    t.update(kw)
    return t


class FakePani(BaseHTTPRequestHandler):
    table, calls, mode = {}, [], "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        utterance = body["messages"][-1]["content"]
        FakePani.calls.append({"utterance": utterance, "body": body})
        if FakePani.mode == "http500":
            self.send_response(500); self.end_headers(); return
        content = "garbage" if FakePani.mode == "garbage" else json.dumps(FakePani.table.get(utterance, turn()), ensure_ascii=False)
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"))

    def log_message(self, *a):
        pass


class Clock:
    t = 5000.0

    def __call__(self):
        return self.t


class PersonaTests(unittest.TestCase):
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
        self.persona = Persona(CFG["persona"]["character"], self.base, "m", api_key="k", timeout=2)
        self.c = self.make(seed=1)

    def make(self, seed=1, **kw):
        w = CFG["wake"]
        return Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=w["words"], wake_min_similarity=w["min_similarity"],
                          lock_cfg=CFG["lock"], persona=self.persona, persona_cfg=CFG["persona"], speak=self.said.append,
                          rng=random.Random(seed), **kw)

    def say(self, text, **t):
        if t:
            FakePani.table[text] = turn(**t)
        self.clock.t += 0.5
        return self.c.handle(text)

    def actions(self):
        return [s["action"].split("::", 1)[-1] for s in self.runs[-1]] if self.runs else []

    # ---- a gated request goes through her dice -----------------------------------------------------
    def test_lube_request_is_decided_by_the_dice_not_fired_at_once(self):
        self.c.rng = random.Random(3)
        outcomes = []
        for i in range(6):
            o = self.say("kochanie, spuść się na mnie" + " " * i, request="lube_fire")
            outcomes.append(o.status)
            if o.status == "executed":
                break
            self.assertEqual(o.status, "denied")
            self.assertEqual(self.said[-1], "Jeszcze nie. Poproś ładniej.")
        self.assertIn("executed", outcomes)
        self.assertEqual(self.actions(), ["Hismith::LubeLauncher::Fire"])
        self.assertEqual(self.said[-1], "Dobra dziewczynka. Masz.")
        self.assertEqual(outcomes.count("denied") + 1, len(outcomes))

    def test_fifth_ask_is_almost_surely_granted_and_the_counter_resets(self):
        self.c.rng = random.Random(0)
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, step_p=0.0, confession_p=0.0)   # only the max_asks rule
        for i in range(4):
            self.assertEqual(self.say(f"kochanie, proszę {i}", request="lube_fire").status, "denied")
        self.assertEqual(self.c._asks["lube_fire"], 4)
        o = self.say("kochanie, błagam, Pani", request="lube_fire")
        self.assertEqual(o.status, "executed")
        self.assertGreaterEqual(o.extra["p"], 0.95)
        self.assertNotIn("lube_fire", self.c._asks)

    def test_confession_raises_the_odds(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, step_p=0.0, confession_p=0.3)
        self.c.rng = random.Random(0)
        o = self.say("kochanie, spuść się", request="lube_fire")
        self.assertEqual((o.status, o.extra["p"]), ("denied", 0.0))
        o = self.say("kochanie, jestem Twoją dziwką, spuść się na mnie", request="lube_fire", confession=True)
        self.assertEqual(o.extra["p"], 0.3)

    def test_what_she_demanded_is_owed_after_the_user_obeys(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, step_p=0.0)
        o = self.say("kochanie, spuść się na mnie", request="lube_fire", demand="confess", denied="Najpierw powiedz, kim jesteś.")
        self.assertEqual(o.status, "denied")
        self.assertEqual(self.c._owed, "lube_fire")
        # the answer comes inside the reply window, without the wake word, and is not a request itself
        o = self.say("jestem Twoją małą dziwką, Pani", request="none", thanked=True, confession=True, granted="Dobra dziewczynka. Masz.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.actions(), ["Hismith::LubeLauncher::Fire"])
        self.assertEqual(o.extra["decision"], "owed")

    def test_denial_may_tease_within_the_allowed_steps(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, step_p=0.0)
        o = self.say("kochanie, szybciej", request="faster",
                     tease_actions=[{"step": "slower", "times": 2}, {"step": "lube_fire", "times": 1}, {"step": "faster", "times": 1}, {"step": "halt", "times": 1}])
        self.assertEqual(o.status, "denied")
        self.assertEqual(self.actions(), ["Hismith::Speed::Slower", "Hismith::Speed::Slower"])
        self.assertEqual(o.extra["tease"], ["slowerx2"])

    def test_grant_runs_the_request_plus_her_extras_bounded(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, pieprz mnie", request="hard", grant_actions=[{"step": "deeper", "times": 2}, {"step": "halt", "times": 1}, {"step": "lube_fire", "times": 1}])
        self.assertEqual(o.status, "executed")
        a = self.actions()
        self.assertEqual(a[:3], ["Hismith::Speed::Faster", "Hismith::Speed::Faster", "Hismith::Depth::Deeper"])
        self.assertNotIn("Hismith::Halt", a)
        self.assertEqual(a.count("Hismith::LubeLauncher::Fire"), 1)

    def test_improvise_request_runs_her_plan(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, użyj mnie jak chcesz", request="improvise", grant_actions=[{"step": "mode_a", "times": 1}, {"step": "deeper", "times": 1}])
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.actions(), ["Hismith::Mode::Set", "Hismith::Depth::Deeper"])

    # ---- what is not hers to decide ------------------------------------------------------------------
    def test_only_the_safe_word_and_the_hold_run_without_her(self):
        for text, action in [("kochanie, koniec", "Hismith::Halt"), ("kochanie, utrzymaj to", "Hismith::Lock::Start")]:
            self.c._locked_until = None
            o = self.say(text)
            self.assertEqual(o.status, "executed", text)
            self.assertEqual(self.actions()[0], action, text)
        self.assertEqual(FakePani.calls, [])

    def test_everything_else_goes_through_her_even_enough_and_the_film(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, base_p_overrides={}, step_p=0.0)
        for text in ["kochanie, dość", "kochanie, wolniej", "kochanie, tryb trzy", "kochanie, wróć do filmu", "kochanie, pauza", "kochanie, zapętl to"]:
            self.c._last.clear()
            before = len(FakePani.calls)
            o = self.say(text)
            self.assertEqual(len(FakePani.calls), before + 1, text)
            self.assertNotEqual(o.status, "executed", text)   # her dice (p = 0) refuse

    def test_she_may_refuse_enough_and_grant_it_later(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, base_p_overrides={"calm": 0.0}, step_p=1.0)
        o = self.say("kochanie, dość", request="calm", denied="Nie. Jeszcze nie skończyłam.", granted="Dobrze. Odpocznij.")
        self.assertEqual(o.status, "denied")
        self.assertEqual(self.said[-1], "Nie. Jeszcze nie skończyłam.")
        self.assertEqual(self.runs, [])
        o = self.say("kochanie, błagam, dość", request="calm", denied="Nie.", granted="Dobrze. Odpocznij.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.actions(), ["Hismith::Calm"])

    def test_mild_requests_have_friendlier_odds(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, base_p_overrides={"calm": 0.5}, step_p=0.0)
        self.c.rng = random.Random(0)
        o = self.say("kochanie, dość", request="calm")
        self.assertEqual(o.extra["p"], 0.5)
        o = self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.assertEqual(o.extra["p"], 0.0)

    def test_film_goes_through_her_too(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, pauza", request="pause", granted="Dobrze. Chwila przerwy.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.runs[-1], [{"action": "Media::PlayPause::Set", "args": [False]}])

    def test_keys_bypass_her(self):
        self.assertEqual(self.c.press("lube_fire").status, "executed")
        self.assertEqual(self.c.press("faster").status, "executed")
        self.assertEqual(FakePani.calls, [])

    def test_a_mode_request_is_hers_to_grant_and_she_comments(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, może ten trzeci program", request="mode_3", granted="Trójka. Siedź cicho.")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.runs[-1][0]["args"], [3])
        self.assertEqual(self.said[-1], "Trójka. Siedź cicho.")

    # ---- the reply window ------------------------------------------------------------------------------
    def test_reply_window_after_her_line_then_wake_word_needed_again(self):
        self.c.persona_cfg = dict(CFG["persona"], base_p=0.0, reply_window=15)
        self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.clock.t += 10
        o = self.say("proszę, Pani, bardzo proszę", request="lube_fire")
        self.assertIn(o.status, ("denied", "executed"))
        self.clock.t += 20
        o = self.say("proszę, Pani, bardzo proszę jeszcze raz", request="lube_fire")
        self.assertEqual(o.status, "ignored")
        self.assertEqual(o.detail, "no wake word")

    def test_in_the_window_the_safe_word_needs_no_wake_word_but_enough_goes_to_her(self):
        self.say("kochanie, spuść się na mnie", request="lube_fire")
        calls = len(FakePani.calls)
        self.c._last.clear()
        o = self.say("koniec")
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.actions()[0], "Hismith::Halt")
        self.assertEqual(len(FakePani.calls), calls)
        self.say("dość", request="calm")
        self.assertEqual(len(FakePani.calls), calls + 1)

    def test_conversation_without_a_request_is_talk(self):
        o = self.say("kochanie, jestem Twoja", request="none", confession=True, denied="Wiem. Powtórz.")
        self.assertEqual(o.status, "talk")
        self.assertEqual(self.said[-1], "Wiem. Powtórz.")
        self.assertEqual(self.runs, [])
        self.assertTrue(self.c.in_reply_window())

    def test_not_addressed_and_long_dialogue_do_not_reach_her(self):
        o = self.say("kochanie, mówię do kolegi", addressed=False)
        self.assertEqual(o.status, "ignored")
        self.say("kochanie, spuść się na mnie", request="lube_fire")
        calls = len(FakePani.calls)
        o = self.say(" ".join(["słowo"] * 40))
        self.assertEqual(o.status, "ignored")
        self.assertEqual(len(FakePani.calls), calls)

    # ---- the hold -----------------------------------------------------------------------------------------
    def test_during_the_hold_she_only_talks_and_lube_still_goes_through_her(self):
        self.assertEqual(self.say("kochanie, utrzymaj to").status, "executed")
        o = self.say("kochanie, szybciej", request="faster", denied="Nie. Trzymasz.")
        self.assertEqual(o.status, "locked")
        self.assertEqual(self.said[-1], "Nie. Trzymasz.")
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, spuść się na mnie", request="lube_fire", grant_actions=[{"step": "faster", "times": 1}])
        self.assertEqual(o.status, "executed")
        self.assertEqual(self.actions(), ["Hismith::LubeLauncher::Fire"])   # her extras are dropped in the hold

    # ---- failures and modes ----------------------------------------------------------------------------
    def test_server_errors_mild_commands_run_plain_the_rest_is_ignored(self):
        for mode in ("http500", "garbage"):
            FakePani.mode = mode
            self.c._last.clear()
            o = self.say("kochanie, dość")
            self.assertEqual(o.status, "executed", mode)
            self.assertIn("ran plain", o.detail)
            self.assertEqual(self.actions(), ["Hismith::Calm"])
            for text in ["kochanie, spuść się na mnie", "kochanie, pieprz mnie", "kochanie, tryb trzy", "kochanie, na maksa", "kochanie, szybciej"]:
                o = self.say(text)
                self.assertEqual(o.status, "ignored", text)
                self.assertIn("persona unavailable", o.detail)
        FakePani.mode = "ok"
        self.assertEqual(self.say("kochanie, koniec").status, "executed")
        self.c._last.clear()
        FakePani.mode = "http500"
        self.assertEqual(self.say("kochanie, utrzymaj to").status, "executed")

    def test_no_key_behaves_like_an_outage(self):
        self.c.persona = Persona("x", api_key="", api_key_env="NO_SUCH_VARIABLE_X")
        self.assertEqual(self.say("kochanie, wolniej").status, "executed")
        self.assertEqual(self.say("kochanie, spuść się na mnie").status, "ignored")

    def test_dry_run_speaks_but_sends_nothing(self):
        self.c.dry_run = True
        self.c.persona_cfg = dict(CFG["persona"], base_p=1.0)
        o = self.say("kochanie, spuść się na mnie", request="lube_fire")
        self.assertEqual(o.status, "dry-run")
        self.assertEqual(self.runs, [])
        self.assertEqual(self.said[-1], "Dobra dziewczynka. Masz.")

    def test_persona_off_restores_plain_commands(self):
        self.c.persona = None
        self.assertEqual(self.say("kochanie, spuść się na mnie").status, "executed")
        self.assertEqual(FakePani.calls, [])

    def test_log_and_history(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.c.log_path = os.path.join(d, "log.jsonl")
            self.c.persona_cfg = dict(CFG["persona"], base_p=0.0)
            self.say("kochanie, spuść się na mnie", request="lube_fire")
            rec = json.loads(open(self.c.log_path, encoding="utf-8").read())
            for key in ("say", "p", "roll", "via", "asks", "demand"):
                self.assertIn(key, rec)
            self.say("proszę", request="lube_fire")
        history = FakePani.calls[-1]["body"]["messages"][1:-1]
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])


class RequestShape(unittest.TestCase):
    def test_prompt_and_schema(self):
        prompt = build_system_prompt(CFG["persona"]["character"], INTENTS, STEP_IDS, {"x": 1})
        self.assertIn("Jesteś Panią", prompt)
        self.assertIn("line_if_denied", prompt)
        schema = build_schema(INTENTS, STEP_IDS)["schema"]["properties"]
        self.assertIn("lube_fire", schema["request"]["enum"])
        self.assertIn("improvise", schema["request"]["enum"])
        self.assertNotIn("halt", schema["grant_actions"]["items"]["properties"]["step"]["enum"])

    def test_missing_key(self):
        with self.assertRaises(PersonaError):
            Persona("x", api_key="", api_key_env="NO_SUCH_VARIABLE_X").respond("y", INTENTS, STEP_IDS, {}, [])

    def test_gated_is_everything_except_the_safe_word_and_the_hold(self):
        ids = {i.id for i in INTENTS}
        gated, never = set(CFG["persona"]["gated"]), set(CFG["persona"]["never_gated"])
        self.assertEqual(never, {"halt", "lock"})
        self.assertFalse(gated & never)
        self.assertEqual(gated | never, ids)
        for x in ("calm", "slower", "shallower", "pause", "play", "script", "normal", "mode_3", "mode_b", "lube_fire", "surprise", "loop_on"):
            self.assertIn(x, gated, x)
        for x in CFG["persona"]["tease_allowed"] + CFG["persona"]["fallback_plain"]:
            self.assertIn(x, ids, x)
        for never_tease in ("halt", "calm", "lock"):
            self.assertNotIn(never_tease, CFG["persona"]["tease_allowed"])
        self.assertTrue(set(CFG["persona"]["fallback_plain"]) <= gated)
        for hard in ("lube_fire", "lube_two", "lube_many", "hard", "fill", "use_me", "faster", "deeper", "mode_a", "surprise"):
            self.assertNotIn(hard, CFG["persona"]["fallback_plain"], hard)


if __name__ == "__main__":
    unittest.main()
