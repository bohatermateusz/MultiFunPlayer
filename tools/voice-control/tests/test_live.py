"""The live mode: streamed answers, partial transcripts, the websocket voice, realtime recognition, the agents loop
and its LLM proxy, the F11 switch - all against fakes, no network."""
import json
import os
import queue
import random
import sys
import threading
import time
import types
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents import NARRATION_TRIGGER, AgentsLoop, ChunkPlayer, LlmProxy  # noqa: E402
from controller import Controller, _NeedPersona  # noqa: E402
from conversation import ClassicLoop, ConversationManager, LiveLoop, SentenceStream  # noqa: E402
from persona import HEAD_KEYS, Persona, PersonaError, build_schema  # noqa: E402
from realtime_stt import RealtimeStt  # noqa: E402
from streamjson import StreamObject  # noqa: E402
from test_manners import CFG, INTENTS, P  # noqa: E402
from test_persona import Clock  # noqa: E402
from tts import ElevenLabsSpeaker, TtsError  # noqa: E402
from tts_ws import ElevenLabsWsSpeaker, split_sentences  # noqa: E402

STEP_IDS = [i.id for i in INTENTS if i.improvise]


def answer(request="none", granted="Masz, mała. Dobra dziewczynka.", denied="Jeszcze nie. Poproś ładniej.", **kw):
    a = {"addressed": True, "request": request, "side": "auto", "shots": 0, "program": None, "confession": False, "thanked": False,
         "defiance": False, "demand": "beg", "grant_actions": [], "tease_actions": [], "line_if_granted": granted, "line_if_denied": denied}
    a.update(kw)
    return a


class FakeChat(BaseHTTPRequestHandler):
    """streams the answer for the last user message as SSE in small pieces (or whole for non-stream requests)"""
    table, calls, mode, piece = {}, [], "ok", 7

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeChat.calls.append(body)
        text = body["messages"][-1]["content"]
        a = FakeChat.table.get(text) or answer()
        raw = json.dumps(a, ensure_ascii=False)
        if FakeChat.mode == "500":
            self.send_response(500); self.end_headers(); return
        if not body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": raw}}]}).encode()); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for i in range(0, len(raw), FakeChat.piece):
            if FakeChat.mode == "cut" and i > len(raw) // 2:
                return
            chunk = {"choices": [{"delta": {"content": raw[i:i + FakeChat.piece]}}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8")); self.wfile.flush()
            time.sleep(0.002)
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


class StreamJson(unittest.TestCase):
    def test_fields_in_order_and_line_deltas(self):
        o, ev = StreamObject(), []
        raw = json.dumps(answer("faster", program={"start_after_seconds": 0, "every_seconds": 60, "duration_seconds": 0, "plan": [], "random_modes": True, "cancel": False}), ensure_ascii=False)
        for i in range(0, len(raw), 3):
            ev += o.feed(raw[i:i + 3])
        closed = [e[1] for e in ev if e[0] == "field"]
        self.assertEqual(closed, list(HEAD_KEYS) + ["line_if_granted", "line_if_denied"])
        self.assertEqual("".join(e[2] for e in ev if e[0] == "delta" and e[1] == "line_if_granted"), "Masz, mała. Dobra dziewczynka.")
        self.assertEqual(o.fields["program"]["every_seconds"], 60)
        self.assertTrue(o.done)

    def test_escapes(self):
        o = StreamObject()
        ev = o.feed('{"a": "x\\"y\\u0142z", "b": [1, "}"], "c": null}')
        self.assertEqual(o.fields, {"a": 'x"yłz', "b": [1, "}"], "c": None})

    def test_schema_puts_the_lines_last(self):
        req = build_schema(INTENTS, STEP_IDS)["schema"]["required"]
        self.assertEqual(req[-2:], ["line_if_granted", "line_if_denied"])
        self.assertEqual(tuple(req[:-2]), HEAD_KEYS)


class ChatBase(unittest.TestCase):
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
        self.runs, self.said, self.clock = [], [], Clock()
        self.persona = Persona("x", self.base, "m", api_key="k", timeout=3)
        self.c = Controller(INTENTS, self.runs.append, clock=self.clock, wake_words=CFG["wake"]["words"], lock_cfg=CFG["lock"],
                            persona=self.persona, persona_cfg=dict(P, base_p=1.0, base_p_overrides={}, step_p_overrides={},
                                                                   manners=dict(P["manners"], politeness_factor=1.0, ignore_demand_factor=1.0)),
                            rng=random.Random(1))
        self.c.persona_cfg["last_second"]["enabled"] = False
        self.c.speak_ex = lambda line, voice, meta=None: self.said.append(line)
        self.sentences = []

    def speak_sentence(self, s, meta, final):
        self.sentences.append((s, final))


class Streamed(ChatBase):
    def test_respond_stream_events(self):
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        ev = list(self.persona.respond_stream("kochanie, szybciej", INTENTS, STEP_IDS, {}, []))
        kinds = [e[0] for e in ev]
        self.assertEqual(kinds[0], "head")
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(ev[0][1].request, "faster")
        self.assertEqual(ev[0][1].line_if_granted, "")
        self.assertEqual("".join(e[2] for e in ev if e[0] == "delta" and e[1] == "line_if_denied"), "Jeszcze nie. Poproś ładniej.")
        self.assertEqual(ev[-1][1].line_if_granted, "Masz, mała. Dobra dziewczynka.")
        self.assertTrue(FakeChat.calls[-1]["stream"])

    def test_granted_speaks_sentence_by_sentence_while_the_steps_ran_first(self):
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        kind, need = self.c.prepare("kochanie, szybciej")
        self.assertEqual(kind, "persona")
        events = self.persona.respond_stream("kochanie, szybciej", INTENTS, STEP_IDS, need.state, need.history)
        o = self.c.handle_streamed("kochanie, szybciej", events, self.speak_sentence, need)
        self.assertEqual(o.status, "executed")
        self.assertTrue(o.extra["streamed"])
        self.assertEqual([s for s, f in self.sentences if s], ["Masz, mała.", "Dobra dziewczynka."])
        self.assertTrue(self.sentences[-1][1])
        self.assertEqual(o.extra["say"], "Masz, mała. Dobra dziewczynka.")
        self.assertIn(("assistant", "Masz, mała. Dobra dziewczynka."), self.c._history)
        self.assertEqual(len(self.runs), 1)
        self.assertNotIn("\x00", "".join(t for _, t in self.c._history))

    def test_denied_waits_for_the_second_line(self):
        self.c.persona_cfg = dict(self.c.persona_cfg, base_p=0.0)
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        kind, need = self.c.prepare("kochanie, szybciej")
        o = self.c.handle_streamed("kochanie, szybciej", self.persona.respond_stream("kochanie, szybciej", INTENTS, STEP_IDS, need.state, need.history),
                                   self.speak_sentence, need)
        self.assertEqual(o.status, "denied")
        self.assertEqual([s for s, f in self.sentences if s], ["Jeszcze nie.", "Poproś ładniej."])
        self.assertEqual(self.runs, [])

    def test_a_cut_stream_before_the_head_falls_back_to_the_whole_answer(self):
        FakeChat.mode = "cut"
        FakeChat.piece = 400                      # the first piece has everything but the stream is cut -> fallback path
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        kind, need = self.c.prepare("kochanie, szybciej")

        def broken():
            raise PersonaError("stream failed")
            yield
        o = self.c.handle_streamed("kochanie, szybciej", broken(), self.speak_sentence, need)
        FakeChat.piece = 7
        self.assertIn(o.status, ("executed", "denied"))
        self.assertTrue(self.said or o.status == "executed")
        self.assertNotIn("\x00", "".join(t for _, t in self.c._history))

    def test_partial_transcripts(self):
        self.clock.t += 20
        self.assertIsNone(self.c.handle_partial("t1", "kochanie"))
        self.assertIsNone(self.c.handle_partial("t1", "kochanie, utrzymaj"))        # the phrase is not complete yet
        o = self.c.handle_partial("t1", "kochanie, utrzymaj to")                  # ungated: runs at once
        self.assertEqual((o.status, o.via), ("executed", "live"))
        self.assertIsNone(self.c.handle_partial("t1", "kochanie, utrzymaj to proszę"))  # the same turn: not again
        done = self.c.handle_final("t1", "kochanie, utrzymaj to, proszę")
        self.assertEqual(done.status, "done")
        self.assertEqual(len(self.runs), 1)
        self.clock.t += 20
        need = self.c.handle_partial("t2", "kochanie, szybciej")                  # gated: hers, speculatively
        self.assertIsInstance(need, _NeedPersona)
        self.assertIsNone(self.c.handle_partial("t2", "kochanie, szybciej, proszę"))
        self.clock.t += 20
        o = self.c.handle_partial("t3", "trąbka")                                 # the safe word, no wake word
        self.assertEqual((o.intent, o.status), ("halt", "executed"))

    def test_speculation_fits(self):
        self.assertTrue(self.c.speculation_fits("kochanie, szybciej", "kochanie, szybciej"))
        self.assertTrue(self.c.speculation_fits("kochanie, szybciej", "kochanie, szybciej, proszę"))
        self.assertFalse(self.c.speculation_fits("kochanie, szybciej", "kochanie, wolniej"))
        self.assertFalse(self.c.speculation_fits("", "kochanie, szybciej"))


class FakeSocket:
    """a websocket stand-in: what the code sends is collected; recv() hands out scripted messages"""

    def __init__(self, incoming=None, on_send=None):
        self.sent, self.incoming, self.on_send = [], queue.Queue(), on_send
        for m in incoming or []:
            self.incoming.put(m)
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))
        if self.on_send:
            self.on_send(self, self.sent[-1])

    def recv(self):
        item = self.incoming.get(timeout=3)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True
        self.incoming.put("")

    def settimeout(self, t):
        pass


class WsVoice(unittest.TestCase):
    def test_sentences_in_audio_out(self):
        import base64
        sock = FakeSocket()

        def on_send(s, msg):
            if msg.get("text", "").strip():
                s.incoming.put(json.dumps({"audio": base64.b64encode(b"\x01\x00" * 4).decode(), "isFinal": False}))
            elif msg.get("text") == "":
                s.incoming.put(json.dumps({"audio": "", "isFinal": True}))
        sock.on_send = on_send
        sp = ElevenLabsSpeaker("v1", "eleven_multilingual_v2", api_key="k", stage_settings=P["tts"]["stage_settings"])
        sp.delivery = P["tts"]["delivery"]
        ws = ElevenLabsWsSpeaker(sp, connect=lambda url, headers: sock)
        chunks = list(ws.say_stream(iter(["Masz, ", "mała. Dobra ", "dziewczynko, moja"]), {"tone": "czuły", "kind": "answer", "stage": 1}))
        self.assertEqual(len(chunks), 2)
        texts = [m["text"] for m in sock.sent if "text" in m]
        self.assertEqual(texts[0], " ")
        self.assertIn("Masz, mała. ", texts[1])
        self.assertTrue(any(m.get("flush") and "dziewczynko, moja" in m["text"] for m in sock.sent))   # the unfinished tail
        self.assertEqual(texts[-1], "")
        self.assertIn("voice_settings", sock.sent[0])
        self.assertNotIn("speed", sock.sent[0]["voice_settings"])
        self.assertTrue(sock.closed)

    def test_v3_is_swapped_for_v2_over_the_socket(self):
        sp = ElevenLabsSpeaker("v1", "eleven_v3", api_key="k")
        url, model = ElevenLabsWsSpeaker(sp)._url()
        self.assertEqual(model, "eleven_multilingual_v2")
        self.assertIn("stream-input", url)

    def test_connection_failure_is_a_tts_error(self):
        sp = ElevenLabsSpeaker("v1", "eleven_multilingual_v2", api_key="k")

        def boom(url, headers):
            raise OSError("down")
        ws = ElevenLabsWsSpeaker(sp, connect=boom)
        with self.assertRaises(TtsError):
            list(ws.say_stream(iter(["Masz."]), {}))

    def test_split(self):
        self.assertEqual(split_sentences("Masz. Dobra… jeszcze"), (["Masz.", "Dobra…"], "jeszcze"))
        self.assertEqual(split_sentences("Masz."), (["Masz."], ""))


class Realtime(unittest.TestCase):
    def test_events_and_session(self):
        sock = FakeSocket([
            json.dumps({"type": "input_audio_buffer.speech_started"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "kochanie, "}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "szybciej"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.completed", "item_id": "i1", "transcript": "kochanie, szybciej"}),
            ""])
        seen = []
        rt = RealtimeStt("k", language="pl", connect=lambda url, headers: sock, on_partial=lambda i, t: seen.append(("p", i, t)),
                         on_final=lambda i, t: seen.append(("f", i, t)), on_speech_start=lambda: seen.append(("start",)), log=lambda *a: None)
        rt.start()
        for _ in range(50):
            if ("f", "i1", "kochanie, szybciej") in seen:
                break
            time.sleep(0.02)
        rt.stop()
        self.assertEqual(seen[0], ("start",))
        self.assertIn(("p", "i1", "kochanie, "), seen)
        self.assertIn(("p", "i1", "kochanie, szybciej"), seen)
        self.assertEqual(seen[-1], ("f", "i1", "kochanie, szybciej"))
        self.assertEqual(sock.sent[0]["type"], "session.update")
        session = sock.sent[0]["session"]
        self.assertEqual(session["type"], "transcription")                                     # the GA shape
        self.assertEqual(session["audio"]["input"]["turn_detection"]["silence_duration_ms"], 500)
        self.assertEqual(session["audio"]["input"]["transcription"]["language"], "pl")
        self.assertEqual(session["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertNotIn("OpenAI-Beta", rt.headers())

    def test_beta_shape_and_the_switch_to_ga(self):
        rt = RealtimeStt("k", api="beta", connect=lambda u, h: FakeSocket([""]), log=lambda *a: None)
        self.assertIn("input_audio_transcription", rt.session_update()["session"])
        self.assertEqual(rt.headers()["OpenAI-Beta"], "realtime=v1")
        with self.assertRaises(RuntimeError):
            rt.handle_event({"type": "error", "error": {"code": "beta_api_shape_disabled"}})
        self.assertEqual(rt.api, "ga")

    def test_audio_is_appended_only_while_connected(self):
        rt = RealtimeStt("k", connect=lambda u, h: FakeSocket([""]), log=lambda *a: None)
        self.assertFalse(rt.send_audio(b"\x00\x00"))


class Live(ChatBase):
    def setUp(self):
        super().setUp()
        self.spoken = []

        class FakeVoice:
            _stop = False

            def say(me, line, stage=0, meta=None, pcm=None, stream=None):
                self.spoken.append(("stream" if stream is not None else line, meta))
                if stream is not None:
                    list(stream)

            def interrupt(me):
                self.spoken.append(("interrupt", None))

            def speaking(me):
                return False
        self.voice = FakeVoice()
        self.rt = RealtimeStt("k", connect=lambda u, h: FakeSocket([""]), log=lambda *a: None)
        self.mic_calls = []
        self.loop = LiveLoop(self.c, self.rt, lambda rate, on_block, stop: self.mic_calls.append(rate), self.voice, ws_speaker=None,
                             headphones=True, log=lambda *a: None)
        self.c.speak_ex = None

    def test_partial_runs_a_plain_command_and_the_final_does_not_repeat_it(self):
        self.clock.t += 20
        self.loop.on_partial("t1", "kochanie, utrzymaj to")
        self.assertEqual(len(self.runs), 1)
        self.loop.on_final("t1", "kochanie, utrzymaj to")
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.loop.last_outcome.status, "done")

    def test_a_request_is_answered_streamed_and_spoken(self):
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        o = self.loop.on_final("t2", "kochanie, szybciej")
        self.assertEqual(o.status, "executed")
        self.assertTrue(o.extra.get("streamed"))
        self.assertEqual(self.spoken[-1][0], "Masz, mała. Dobra dziewczynka.")       # no websocket voice: the whole line
        self.assertEqual(self.spoken[-1][1]["kind"], "answer")

    def test_speculation_is_used_when_the_final_matches(self):
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        self.loop.on_partial("t3", "kochanie, szybciej")
        self.assertIn("t3", self.loop._spec)
        o = self.loop.on_final("t3", "kochanie, szybciej")
        self.assertTrue(o.extra.get("speculative"))
        self.assertEqual(o.status, "executed")
        self.assertEqual(len([c for c in FakeChat.calls if not c.get("stream")]), 1)

    def test_speech_start_interrupts_her(self):
        self.loop.on_speech_start()
        self.assertEqual(self.spoken[-1][0], "interrupt")

    def test_fallback_to_local_segments_when_the_session_is_down(self):
        from audio import SegmentDetector
        self.loop.detector = SegmentDetector(sample_rate=24000, threshold_db=-30, end_silence=0.1, min_seconds=0.05, calibration_seconds=0)
        self.loop.fallback_transcribe = lambda pcm: "trąbka"
        import struct
        loud = struct.pack("<" + "h" * 960, *([8000] * 960))
        quiet = b"\x00\x00" * 960
        for _ in range(6):
            self.loop.on_block(loud)
        for _ in range(6):
            self.loop.on_block(quiet)
        self.assertTrue(any(r and "Halt" in r[0]["action"] for r in self.runs))

    def test_manager_switches_and_announces(self):
        started, said = [], []
        self.c.speak_ex = lambda line, voice, meta=None: said.append(line)

        class L:
            def __init__(s, n):
                s.n = n

            def start(s):
                started.append(("start", s.n))

            def stop(s):
                started.append(("stop", s.n))
        m = ConversationManager({"classic": L("c"), "live": L("l")}, "classic", on_switch=lambda mode: self.c.say_now("Teraz na żywo." if mode == "live" else "Klasycznie."), log=lambda *a: None)
        m.start()
        self.assertEqual(started, [("start", "c")])
        m.next()
        self.assertEqual(started[-2:], [("stop", "c"), ("start", "l")])
        self.assertEqual(said[-1], "Teraz na żywo.")
        m.next()
        self.assertEqual(m.mode, "classic")
        self.assertFalse(m.switch("agents"))


class Agents(ChatBase):
    def test_loop_events(self):
        import base64
        played, cleared = [], []

        class Player:
            def push(s, pcm):
                played.append(pcm)

            def clear(s):
                cleared.append(1)
        sock = FakeSocket([
            json.dumps({"type": "conversation_initiation_metadata"}),
            json.dumps({"type": "ping", "ping_event": {"event_id": 7}}),
            json.dumps({"type": "audio", "audio_event": {"audio_base_64": base64.b64encode(b"\x01\x00").decode()}}),
            json.dumps({"type": "interruption", "interruption_event": {"event_id": 1}}),
            json.dumps({"type": "user_transcript", "user_transcription_event": {"user_transcript": "trąbka"}}),
            json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "Dobrze, mała."}}),
            ""])
        loop = AgentsLoop(self.c, "k", "agent-1", lambda rate, on_block, stop: None, Player(), connect=lambda u, h: sock,
                          narration_gap=lambda: 1000, log=lambda *a: None)
        loop.start()
        for _ in range(50):
            if loop.responses:
                break
            time.sleep(0.02)
        loop.stop()
        self.assertEqual(sock.sent[0]["type"], "conversation_initiation_client_data")
        self.assertIn({"type": "pong", "event_id": 7}, sock.sent)
        self.assertEqual(played, [b"\x01\x00"])
        self.assertEqual(cleared[:1], [1])
        self.assertTrue(any("Halt" in st["action"] for r in self.runs for st in r))     # the safe word from the agent's transcript
        self.assertEqual(loop.responses, ["Dobrze, mała."])

    def test_proxy_answers_as_sse_with_her_line(self):
        self.clock.t += 20
        FakeChat.table["kochanie, szybciej"] = answer("faster")
        self.c.external_voice = True
        proxy = LlmProxy(self.c, self.persona, port=0, log=lambda *a: None)
        port = proxy.start()
        try:
            body = {"messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "kochanie, szybciej"}], "stream": True}
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            text = urllib.request.urlopen(req, timeout=5).read().decode()
            self.assertIn("Masz, mała. Dobra dziewczynka.", text)
            self.assertIn("[DONE]", text)
            self.assertEqual(len(self.runs), 1)                                              # the steps ran in our controller
            self.assertEqual(self.said, [])                                                  # external voice: the agent speaks
        finally:
            proxy.stop()

    def test_proxy_narration_trigger_takes_a_pool_line(self):
        class Pool:
            def take(s, theme):
                return "Z puli."

            def left(s, theme):
                return 1

            def remember(s, line):
                pass

            def is_repeat(s, line):
                return False
        self.c.pool = Pool()
        self.c.external_voice = True
        proxy = LlmProxy(self.c, self.persona, log=lambda *a: None)
        self.assertEqual(proxy.answer(NARRATION_TRIGGER), "Z puli.")
        self.assertIn(("assistant", "Z puli."), self.c._history)

    def test_chunk_player(self):
        got = []

        def play_stream(chunks, rate, stop=None):
            for c in chunks:
                got.append(c)
        p = ChunkPlayer(play_stream, 16000)
        p.push(b"a"); p.push(b"b")
        time.sleep(0.05)
        p.clear()
        p.close()
        self.assertEqual(got[:2], [b"a", b"b"])


class Config(unittest.TestCase):
    def test_keys_and_conversation_block(self):
        self.assertEqual(CFG["keys"]["f11"], "@mode")
        self.assertEqual(CFG["conversation"]["mode"], "classic")
        self.assertIn("live", CFG["conversation"])
        self.assertIn("agents", CFG["conversation"])
        req = open(os.path.join(os.path.dirname(__file__), "..", "requirements.txt"), encoding="utf-8").read()
        self.assertIn("websocket-client", req)


if __name__ == "__main__":
    unittest.main()
