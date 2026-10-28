"""The LLM fallback against a fake chat-completions server (no network, no key)."""
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import Controller  # noqa: E402
from llm import IntentLlm, LlmError, build_schema, build_system_prompt  # noqa: E402
from llm_eval import run_eval  # noqa: E402
from voice_control import load_config  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "commands.json")
CFG, INTENTS = load_config(CONFIG)


class FakeChat(BaseHTTPRequestHandler):
    """answers from FakeChat.table[utterance]; FakeChat.calls records every request"""
    table = {}
    calls = []
    mode = "ok"          # ok | http500 | garbage | slow

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        utterance = json.loads(body["messages"][-1]["content"])["utterance"]
        FakeChat.calls.append({"utterance": utterance, "body": body, "auth": self.headers.get("Authorization"), "path": self.path})
        if FakeChat.mode == "http500":
            self.send_response(500); self.end_headers(); return
        if FakeChat.mode == "garbage":
            content = "this is not json"
        else:
            answer = {"intent": "none", "confidence": 0.0, "addressed": False, "times": 1, "count": 1}
            answer.update(FakeChat.table.get(utterance, {}))
            content = json.dumps(answer)
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"))

    def log_message(self, *a):
        pass


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class LlmTests(unittest.TestCase):
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
        self.runs = []
        self.clock = Clock()
        self.llm = IntentLlm(self.base, "test-model", api_key="secret", timeout=2)
        self.c = self.make()

    def make(self, **kw):
        wake = CFG["wake"]
        return Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=wake["words"],
                          wake_min_similarity=wake["min_similarity"], llm=self.llm, llm_cfg=CFG["llm"], **kw)

    def say(self, text, **answer):
        if answer:
            FakeChat.table[text] = answer
        return self.c.handle(text)

    # ---- the examples from the field -------------------------------------------------------------
    def test_distorted_wake_word_is_understood(self):
        o = self.say("trwanie, spuścić się", intent="lube_fire", confidence=0.95, addressed=True)
        self.assertEqual((o.status, o.intent, o.via), ("executed", "lube_fire", "llm"))
        self.assertEqual(self.runs[0][0]["action"], "{target}::Hismith::LubeLauncher::Fire")

    def test_no_address_at_all_never_reaches_the_model(self):
        for t in ["a nie spuść się", "spuść się", "szybciej", "Dzięki za oglądanie!", "nie, nie przestawaj"]:
            self.assertEqual(self.say(t).status, "ignored", t)
        self.assertEqual(FakeChat.calls, [])
        self.assertEqual(self.runs, [])

    def test_fast_path_does_not_call_the_model(self):
        self.assertEqual(self.say("kochanie, tryb numer trzy").intent, "mode_3")
        self.assertEqual(self.say("kochanie spuść się").intent, "lube_fire")
        self.assertEqual(FakeChat.calls, [])

    def test_loosely_worded_command(self):
        o = self.say("kochanie, mógłbyś przyspieszyć", intent="faster", confidence=0.9, addressed=True)
        self.assertEqual((o.status, o.intent, o.via), ("executed", "faster", "llm"))

    # ---- the model is only believed when it is sure and well-formed ------------------------------
    def test_unknown_intent_low_confidence_or_not_addressed_run_nothing(self):
        cases = {
            "kochanie, zrób coś dziwnego": dict(intent="format_disk", confidence=1.0, addressed=True),
            "kochanie, chyba szybciej?": dict(intent="faster", confidence=0.5, addressed=True),
            "kochanie, mówił że szybciej": dict(intent="faster", confidence=0.99, addressed=False),
            "kochanie, jak się masz": dict(intent="none", confidence=0.99, addressed=True),
        }
        for text, answer in cases.items():
            o = self.say(text, **answer)
            self.assertEqual(o.status, "ignored", text)
        self.assertEqual(self.runs, [])

    def test_lube_needs_higher_confidence(self):
        o = self.say("trwanie, strzel", intent="lube_fire", confidence=0.85, addressed=True)
        self.assertEqual(o.status, "ignored")
        self.assertIn("0.90", o.detail)
        o = self.say("trwanie, strzel jeszcze", intent="lube_fire", confidence=0.95, addressed=True)
        self.assertEqual(o.status, "executed")

    def test_negation_answered_none_is_respected(self):
        o = self.say("kochanie, nie spuszczaj się", intent="none", confidence=0.9, addressed=True)
        self.assertEqual(o.status, "ignored")
        self.assertEqual(self.runs, [])

    # ---- failures never break the tool ------------------------------------------------------------
    def test_server_error_garbage_and_missing_key(self):
        for mode in ("http500", "garbage"):
            FakeChat.mode = mode
            o = self.say("trwanie, spuścić się")
            self.assertEqual(o.status, "ignored", mode)
            self.assertIn("llm unavailable", o.detail)
        FakeChat.mode = "ok"
        self.assertEqual(self.say("kochanie szybciej").status, "executed")   # still works afterwards
        self.c.llm = IntentLlm(self.base, "m", api_key="", api_key_env="NO_SUCH_VARIABLE_X")
        self.assertIn("llm unavailable", self.say("trwanie, strzel").detail)

    def test_unreachable_server_is_ignored(self):
        self.c.llm = IntentLlm("http://127.0.0.1:9/v1", "m", api_key="k", timeout=1)
        self.assertIn("llm unavailable", self.say("trwanie, spuścić się").detail)

    # ---- parameters from the model are clamped ----------------------------------------------------
    def test_count_replaces_the_fire_many_argument_and_is_clamped(self):
        o = self.say("kochanie, strzel dziewięć razy", intent="lube_many", confidence=0.95, addressed=True, count=9)
        self.assertEqual(self.runs[-1][0]["args"], [5])
        self.clock.t += 20
        self.say("kochanie, strzel cztery razy", intent="lube_many", confidence=0.95, addressed=True, count=4)
        self.assertEqual(self.runs[-1][0]["args"], [4])

    def test_times_repeats_only_repeatable_commands(self):
        self.say("kochanie, dużo szybciej", intent="faster", confidence=0.95, addressed=True, times=3)
        self.assertEqual(len(self.runs[-1]), 3)
        self.clock.t += 5
        self.say("kochanie, zdecydowanie szybciej", intent="faster", confidence=0.95, addressed=True, times=7)
        self.assertEqual(len(self.runs[-1]), 3)                       # clamped
        self.clock.t += 5
        self.say("kochanie, tryb czwarty koniecznie", intent="mode_4", confidence=0.95, addressed=True, times=3)
        self.assertEqual(len(self.runs[-1]), 1)                       # a mode is not repeated
        self.assertEqual(self.runs[-1][0]["args"], [4])

    def test_intent_steps_are_not_modified_for_later_commands(self):
        self.say("kochanie, strzel dziewięć razy", intent="lube_many", confidence=0.95, addressed=True, count=5)
        lube_many = next(i for i in INTENTS if i.id == "lube_many")
        self.assertEqual(lube_many.steps[0]["args"], [3])

    # ---- shared rules still apply -----------------------------------------------------------------
    def test_cooldown_applies_to_the_llm_path(self):
        answer = dict(intent="lube_fire", confidence=0.99, addressed=True)
        self.assertEqual(self.say("trwanie, spuścić się", **answer).status, "executed")
        self.assertEqual(self.say("trwanie, spuścić się znowu", **answer).status, "cooldown")
        self.assertEqual(len(self.runs), 1)

    def test_dry_run_sends_nothing(self):
        self.c.dry_run = True
        o = self.say("trwanie, spuścić się", intent="lube_fire", confidence=0.99, addressed=True)
        self.assertEqual(o.status, "dry-run")
        self.assertEqual(self.runs, [])

    def test_keys_are_not_blocked_by_a_model_call(self):
        gate, started = threading.Event(), threading.Event()
        original = self.llm.classify

        def slow(*a, **k):
            started.set()
            gate.wait(3)
            return original(*a, **k)

        self.llm.classify = slow
        FakeChat.table["trwanie, strzel"] = dict(intent="lube_fire", confidence=0.99, addressed=True)
        t = threading.Thread(target=self.c.handle, args=("trwanie, strzel",))
        t.start()
        self.assertTrue(started.wait(2))
        done = []
        k = threading.Thread(target=lambda: done.append(self.c.press("faster")))
        k.start()
        k.join(1.0)
        self.assertEqual(len(done), 1, "the key press had to wait for the model")
        gate.set()
        t.join(3)

    def test_recent_commands_are_sent_as_context(self):
        self.say("kochanie szybciej")
        self.say("kochanie, jeszcze trochę", intent="faster", confidence=0.9, addressed=True)
        system = FakeChat.calls[-1]["body"]["messages"][0]["content"]
        self.assertIn("faster", system.split("Last commands")[1])

    def test_log_records_the_model_answer(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            self.c.log_path = path
            self.say("trwanie, spuścić się", intent="lube_fire", confidence=0.95, addressed=True)
            rec = json.loads(open(path, encoding="utf-8").read())
            self.assertEqual(rec["via"], "llm")
            self.assertIn("lube_fire", rec["llm_raw"])
            self.assertIn("latency_ms", rec)


class RequestShape(unittest.TestCase):
    def test_prompt_and_schema_contain_every_command(self):
        schema = build_schema(INTENTS)["schema"]["properties"]["intent"]["enum"]
        self.assertEqual(sorted(schema), sorted([i.id for i in INTENTS] + ["none"]))
        prompt = build_system_prompt(INTENTS, ["kochanie"], ["faster"])
        for i in INTENTS:
            self.assertIn(i.id, prompt)
            self.assertTrue(i.description, f"{i.id} needs a description for the model")
        self.assertIn("kochanie", prompt)

    def test_request_is_deterministic_json_schema_with_bearer_key(self):
        FakeChat.table, FakeChat.calls, FakeChat.mode = {}, [], "ok"
        server = HTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            llm = IntentLlm(f"http://127.0.0.1:{server.server_address[1]}/v1", "m", api_key="secret", timeout=2)
            llm.classify("trwanie, spuścić się", INTENTS, ["kochanie"], [])
        finally:
            server.shutdown()
        call = FakeChat.calls[0]
        self.assertEqual(call["path"], "/v1/chat/completions")
        self.assertEqual(call["auth"], "Bearer secret")
        self.assertEqual(call["body"]["temperature"], 0)
        self.assertEqual(call["body"]["response_format"]["type"], "json_schema")
        self.assertTrue(call["body"]["response_format"]["json_schema"]["strict"])

    def test_missing_key_is_an_error(self):
        with self.assertRaises(LlmError):
            IntentLlm(api_key="", api_key_env="NO_SUCH_VARIABLE_X").classify("x", INTENTS)


class EvalTool(unittest.TestCase):
    def test_eval_counts_right_wrong_and_false_triggers(self):
        import tempfile
        FakeChat.table, FakeChat.calls, FakeChat.mode = {}, [], "ok"
        server = HTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        FakeChat.table["trwanie, spuścić się"] = dict(intent="lube_fire", confidence=0.95, addressed=True)
        FakeChat.table["kochanie, idź głębiej"] = dict(intent="deeper", confidence=0.95, addressed=True)
        FakeChat.table["kochanie, kup mleko"] = dict(intent="faster", confidence=0.95, addressed=True)   # a false trigger
        cases = [{"say": "trwanie, spuścić się", "intent": "lube_fire"}, {"say": "kochanie, idź głębiej", "intent": "deeper"},
                 {"say": "kochanie, kup mleko", "intent": "none"}, {"say": "kochanie, zwolnij trochę", "intent": "slower"}]
        try:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "cases.json")
                json.dump(cases, open(path, "w", encoding="utf-8"), ensure_ascii=False)
                llm = IntentLlm(f"http://127.0.0.1:{server.server_address[1]}/v1", "m", api_key="k", timeout=2)
                wake = CFG["wake"]
                c = Controller(INTENTS, lambda steps: None, wake_words=wake["words"], llm=llm, llm_cfg=CFG["llm"])
                out = io.StringIO()
                code = run_eval(c, path, out)
        finally:
            server.shutdown()
        text = out.getvalue()
        self.assertEqual(code, 1)                              # a false trigger makes the exit code 1
        self.assertIn("2/4 right", text)
        self.assertIn("FALSE TRIGGERS (a command ran that should not have): 1", text)
        self.assertIn("missed or wrong command: 1", text)

    def test_every_case_in_the_shipped_file_points_at_a_real_intent(self):
        ids = {i.id for i in INTENTS} | {"none"}
        cases = json.load(open(os.path.join(os.path.dirname(__file__), "llm_cases.json"), encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 50)
        for case in cases:
            self.assertIn(case["intent"], ids, case["say"])


if __name__ == "__main__":
    unittest.main()
