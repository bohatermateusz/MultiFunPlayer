"""Speech recognition as you speak: the OpenAI Realtime transcription session over a WebSocket. Audio (pcm16, 24 kHz)
is appended continuously; the server's voice activity detection cuts the turns and sends partial transcripts (deltas)
and the completed transcript of every turn. Callbacks run on the reader thread. The socket is injectable (tests)."""
import base64
import json
import threading
import time


class RealtimeStt:
    def __init__(self, api_key, model="gpt-4o-mini-transcribe", language="pl", prompt="", silence_ms=500, threshold=0.5,
                 connect=None, url="wss://api.openai.com/v1/realtime?intent=transcription", api="ga", rate=24000,
                 on_partial=None, on_final=None, on_speech_start=None, on_speech_stop=None, log=print):
        self.api_key, self.model, self.language, self.prompt = api_key, model, language, prompt
        self.silence_ms, self.threshold, self.url = int(silence_ms), float(threshold), url
        self.api, self.rate = api, int(rate)            # "ga" (the current API) or "beta" (the old shape, retired)
        self.connect = connect or self._default_connect
        self.on_partial, self.on_final = on_partial, on_final
        self.on_speech_start, self.on_speech_stop = on_speech_start, on_speech_stop
        self.log = log
        self._ws = None
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.turns = {}               # item_id -> partial text
        self.failures = 0

    @staticmethod
    def available():
        try:
            import websocket  # noqa: F401
            return True
        except ImportError:
            return False

    def _default_connect(self, url, headers):
        import websocket
        return websocket.create_connection(url, header=[f"{k}: {v}" for k, v in headers.items()], timeout=30)

    def session_update(self):
        vad = {"type": "server_vad", "threshold": self.threshold, "prefix_padding_ms": 300, "silence_duration_ms": self.silence_ms}
        transcription = {"model": self.model, "language": self.language, "prompt": self.prompt}
        if self.api == "beta":
            return {"type": "session.update", "session": {
                "input_audio_format": "pcm16", "input_audio_transcription": transcription, "turn_detection": vad,
                "input_audio_noise_reduction": {"type": "near_field"}}}
        # the GA shape: a transcription session, audio input settings under session.audio.input
        return {"type": "session.update", "session": {
            "type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": self.rate},
                                "noise_reduction": {"type": "near_field"},
                                "transcription": transcription,
                                "turn_detection": vad}}}}

    def headers(self):
        h = {"Authorization": f"Bearer {self.api_key}"}
        if self.api == "beta":
            h["OpenAI-Beta"] = "realtime=v1"       # the beta header selects the retired API shape
        return h

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self.connected = False

    def send_audio(self, pcm):
        """pcm16 mono 24 kHz bytes; dropped while not connected"""
        ws = self._ws
        if ws is None or not self.connected:
            return False
        try:
            ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode("ascii")}))
            return True
        except Exception:
            self.connected = False
            return False

    def set_language(self, language, prompt=""):
        self.language, self.prompt = language, prompt
        ws = self._ws
        if ws is not None and self.connected:
            try:
                ws.send(json.dumps(self.session_update()))
            except Exception:
                pass

    # ---- the reader thread --------------------------------------------------------------------------
    def _run(self):
        delay = 1.0
        while not self._stop.is_set():
            try:
                ws = self.connect(self.url, self.headers())
                self._ws = ws
                ws.send(json.dumps(self.session_update()))
                self.connected = True
                delay = 1.0
                self._read(ws)
            except Exception as e:
                self.failures += 1
                if not self._stop.is_set():
                    self.log(f"  realtime stt: {e} (reconnecting in {delay:.0f}s)")
            self.connected = False
            self._ws = None
            if self._stop.is_set():
                break
            time.sleep(delay)
            delay = min(delay * 2, 15.0)

    def _read(self, ws):
        while not self._stop.is_set():
            raw = ws.recv()
            if raw is None or raw == "":
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            self.handle_event(msg)

    def handle_event(self, msg):
        t = msg.get("type", "")
        if t == "input_audio_buffer.speech_started":
            if self.on_speech_start:
                self.on_speech_start()
        elif t == "input_audio_buffer.speech_stopped":
            if self.on_speech_stop:
                self.on_speech_stop()
        elif t == "conversation.item.input_audio_transcription.delta":
            item = msg.get("item_id", "")
            self.turns[item] = self.turns.get(item, "") + str(msg.get("delta", ""))
            if self.on_partial:
                self.on_partial(item, self.turns[item])
        elif t == "conversation.item.input_audio_transcription.completed":
            item = msg.get("item_id", "")
            text = str(msg.get("transcript", "")).strip()
            self.turns.pop(item, None)
            if self.on_final and text:
                self.on_final(item, text)
        elif t == "error":
            err = msg.get("error", msg)
            self.log(f"  realtime stt error: {err}")
            code = str((err or {}).get("code", "")) if isinstance(err, dict) else ""
            if code == "beta_api_shape_disabled" and self.api == "beta":
                self.api = "ga"                     # switch to the current API shape and reconnect
                raise RuntimeError("beta shape retired, switching to GA")
