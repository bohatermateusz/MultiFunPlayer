"""ElevenLabs Agents (experimental): one audio stream both ways, the most fluent turn-taking, paid per minute.
Our persona stays the agent's brain through a small OpenAI-compatible server (the agent's "custom LLM"): every user
turn reaches it, the dice and the steps run in our controller, and the line she says is streamed back as the answer.
The agent needs to reach that server, so it has to be exposed (a tunnel, e.g. cloudflared). The safe word is also
caught locally from the agent's user transcripts."""
import base64
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from matcher import match

NARRATION_TRIGGER = "(sesja trwa)"


class AgentsLoop:
    """ws events: audio -> player, user_transcript -> safe word locally, ping -> pong, interruption -> clear audio;
    every narration gap a text turn is sent to make her comment (the proxy answers it with a pool line)."""

    def __init__(self, controller, api_key, agent_id, mic, player, connect=None, rate_in=16000, rate_out=16000,
                 overrides=None, narration_gap=None, log=print):
        self.c, self.api_key, self.agent_id = controller, api_key, agent_id
        self.mic, self.player, self.connect = mic, player, connect or self._default_connect
        self.rate_in, self.rate_out, self.overrides, self.log = rate_in, rate_out, overrides or {}, log
        self.narration_gap = narration_gap or (lambda: 60.0)
        self._stop = threading.Event()
        self._ws = None
        self.connected = False
        self.transcripts, self.responses = [], []
        self._next_narration = 0.0

    def _default_connect(self, url, headers):
        import websocket
        return websocket.create_connection(url, header=[f"{k}: {v}" for k, v in headers.items()], timeout=30)

    def url(self):
        return f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={self.agent_id}"

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=lambda: self.mic(self.rate_in, self.on_block, lambda: self._stop.is_set()), daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()

    def stop(self):
        self._stop.set()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self.connected = False
        self.player.clear()

    def on_block(self, pcm):
        ws = self._ws
        if ws is None or not self.connected:
            return
        try:
            ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode("ascii")}))
        except Exception:
            self.connected = False

    def _ticker(self):
        while not self._stop.is_set():
            time.sleep(1.0)
            if not self.connected:
                continue
            now = time.monotonic()
            if not self._next_narration:
                self._next_narration = now + self.narration_gap()
            elif now >= self._next_narration:
                self._next_narration = now + self.narration_gap()
                self.send_text(NARRATION_TRIGGER)

    def send_text(self, text):
        ws = self._ws
        if ws is None or not self.connected:
            return False
        try:
            ws.send(json.dumps({"type": "user_message", "text": text}))
            return True
        except Exception:
            return False

    def _run(self):
        delay = 2.0
        while not self._stop.is_set():
            try:
                ws = self.connect(self.url(), {"xi-api-key": self.api_key})
                self._ws = ws
                init = {"type": "conversation_initiation_client_data"}
                if self.overrides:
                    init["conversation_config_override"] = self.overrides
                ws.send(json.dumps(init, ensure_ascii=False))
                self.connected = True
                delay = 2.0
                while not self._stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    try:
                        self.handle_event(json.loads(raw))
                    except ValueError:
                        continue
            except Exception as e:
                if not self._stop.is_set():
                    self.log(f"  agents: {e} (reconnecting in {delay:.0f}s)")
            self.connected = False
            self._ws = None
            if self._stop.is_set():
                break
            time.sleep(delay)
            delay = min(delay * 2, 20.0)

    def handle_event(self, msg):
        t = msg.get("type", "")
        if t == "ping":
            ev = msg.get("ping_event", {})
            ws = self._ws
            if ws is not None:
                ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
        elif t == "audio":
            b64 = (msg.get("audio_event") or {}).get("audio_base_64", "")
            if b64:
                self.player.push(base64.b64decode(b64))
        elif t == "interruption":
            self.player.clear()
        elif t == "user_transcript":
            text = str((msg.get("user_transcription_event") or {}).get("user_transcript", "")).strip()
            if text:
                self.transcripts.append(text)
                self._safe_word(text)
        elif t == "agent_response":
            text = str((msg.get("agent_response_event") or {}).get("agent_response", "")).strip()
            if text:
                self.responses.append(text)

    def _safe_word(self, text):
        m, _ = match(text, self.c.intents)
        if m is not None and m.intent.id in ("halt", "lock"):
            o = self.c.press(m.intent.id)
            self.log(f'  "{text}" -> {o.status} [{m.intent.id}] (agent transcript)')


class ChunkPlayer:
    """plays PCM chunks as they come (one output stream); clear() drops what is queued (an interruption)"""

    def __init__(self, play_stream, rate=16000):
        self.play_stream, self.rate = play_stream, rate
        self.q = queue.Queue()
        self._gen_lock = threading.Lock()
        self._stop = False
        self.played = []
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, pcm):
        self.q.put(pcm)

    def clear(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _chunks(self):
        while True:
            chunk = self.q.get()
            if chunk is None:
                return
            self.played.append(len(chunk))
            yield chunk

    def _run(self):
        try:
            self.play_stream(self._chunks(), self.rate, stop=lambda: self._stop)
        except Exception:
            pass

    def close(self):
        self._stop = True
        self.q.put(None)


class LlmProxy:
    """The agent's custom LLM: POST /v1/chat/completions (SSE). The last user message is handed to our controller;
    the narration trigger gets a pool line; anything settled without her (a plain command) answers with a short
    confirmation. What is streamed back is what the agent says."""

    def __init__(self, controller, persona, port=8787, host="127.0.0.1", log=print):
        self.c, self.persona, self.port, self.host, self.log = controller, persona, port, host, log
        self.server = None
        self.requests = []

    def answer(self, text):
        """-> the line to speak (may be empty)"""
        c = self.c
        if text.strip() == NARRATION_TRIGGER:
            line, _ = c.narration_line()
            return line or ""
        kind, result = c.prepare(text)
        if kind == "outcome":
            return result.extra.get("say", "") or ""
        if kind == "llm":
            o = c.finish_llm(text, result)
            return o.extra.get("say", "") or ""
        try:
            answer = self.persona.respond(result.transcript, c.intents, [i.id for i in c.intents if i.improvise], result.state, result.history)
        except Exception as e:
            answer = (e, result.plain)
        o = c.finish_persona(text, answer)
        return o.extra.get("say", "") or ""

    def start(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                proxy.requests.append(body)
                text = ""
                for m in reversed(body.get("messages", [])):
                    if m.get("role") == "user":
                        content = m.get("content", "")
                        text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                        break
                try:
                    line = proxy.answer(text)
                except Exception as e:
                    proxy.log(f"  proxy failed: {e}")
                    line = ""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                chunk = {"id": "pani", "object": "chat.completion.chunk", "created": int(time.time()), "model": "pani",
                         "choices": [{"index": 0, "delta": {"role": "assistant", "content": line}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
                done = dict(chunk, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
                self.wfile.write(("data: " + json.dumps(done, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode("utf-8"))

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server = None
