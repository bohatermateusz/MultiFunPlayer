"""ElevenLabs text to speech over a WebSocket (stream-input): sentences go in as they are written, audio comes out as
it is made, so her first word plays while the model is still writing the rest. The socket is an injectable object with
send(str) / recv() -> str / close() (websocket-client's connection by default), so the protocol is testable offline."""
import base64
import json
import re
import threading

from tts import SPEAKER_KEYS, TtsError, choose_delivery, shape_text

SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def ws_connect(url, headers):
    try:
        import websocket
    except ImportError as e:
        raise TtsError("websocket-client is not installed (pip install websocket-client)") from e
    return websocket.create_connection(url, header=[f"{k}: {v}" for k, v in headers.items()], timeout=20)


def split_sentences(text):
    """-> (complete sentences, the unfinished tail)"""
    parts = SENTENCE_END.split(text)
    if not parts:
        return [], ""
    if re.search(r"[.!?…]\s*$", text):
        return [p for p in parts if p.strip()], ""
    return [p for p in parts[:-1] if p.strip()], parts[-1]


class ElevenLabsWsSpeaker:
    """say_stream(sentences, meta) -> generator of PCM chunks; `sentences` is an iterable (a queue-backed generator is
    fine) of text pieces - each piece is sent when it is a whole sentence, the rest is flushed at the end."""

    def __init__(self, speaker, connect=ws_connect):
        self.speaker = speaker            # the HTTP ElevenLabsSpeaker: voice_id, model, api_key, delivery, language
        self.connect = connect
        self.last_messages = []

    def _url(self):
        sp = self.speaker
        model = sp.model if not str(sp.model).startswith("eleven_v3") else "eleven_multilingual_v2"   # no v3 over WS
        url = f"{sp.base_url.replace('https://', 'wss://').replace('http://', 'ws://')}/v1/text-to-speech/{sp.voice_id}/stream-input" \
              f"?model_id={model}&output_format=pcm_24000"
        if sp.language:
            url += f"&language_code={sp.language}"
        return url, model

    def say_stream(self, sentences, meta=None, stop=None):
        sp = self.speaker
        if not sp.api_key:
            raise TtsError("no ElevenLabs API key (ELEVENLABS_API_KEY)")
        if not sp.voice_id:
            raise TtsError("no voice_id")
        url, model = self._url()
        settings = sp._settings(int((meta or {}).get("stage", 0) or 0))
        tags = []
        if sp.delivery is not None and meta:
            _, settings, tags = choose_delivery(sp.delivery, meta)
        try:
            ws = self.connect(url, {"xi-api-key": sp.api_key})
        except Exception as e:
            raise TtsError(f"websocket failed: {e}") from e
        self.last_messages = []
        first = {"text": " ", "generation_config": {"chunk_length_schedule": [50, 90, 120, 150]}}
        if settings:
            first["voice_settings"] = {k: v for k, v in settings.items() if k in SPEAKER_KEYS and k not in ("speed",)}
        self._send(ws, first)
        audio = []
        done = threading.Event()
        error = []

        def reader():
            try:
                while True:
                    raw = ws.recv()
                    if not raw:
                        break
                    msg = json.loads(raw)
                    if msg.get("audio"):
                        audio.append(base64.b64decode(msg["audio"]))
                    if msg.get("isFinal"):
                        break
                    if msg.get("error"):
                        error.append(str(msg.get("error")))
                        break
            except Exception as e:
                error.append(str(e))
            finally:
                done.set()
        threading.Thread(target=reader, daemon=True).start()

        def writer():
            tail = ""
            try:
                for piece in sentences:
                    if stop and stop():
                        break
                    tail += piece
                    whole, tail = split_sentences(tail)
                    for s in whole:
                        self._send(ws, {"text": shape_text(s, model, sp.delivery) + " "})
                if tail.strip() and not (stop and stop()):
                    self._send(ws, {"text": shape_text(tail.strip(), model, sp.delivery) + " ", "flush": True})
                self._send(ws, {"text": ""})          # end of input
            except Exception as e:
                error.append(str(e))
                done.set()
        threading.Thread(target=writer, daemon=True).start()

        got = False
        try:
            while True:
                if stop and stop():
                    break
                if audio:
                    got = True
                    yield audio.pop(0)
                    continue
                if done.is_set():
                    while audio:
                        got = True
                        yield audio.pop(0)
                    break
                done.wait(0.05)
            if error and not got:
                raise TtsError(f"stream failed: {error[0]}")
            if not got and not (stop and stop()):
                raise TtsError("empty answer")
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _send(self, ws, msg):
        self.last_messages.append(msg)
        ws.send(json.dumps(msg, ensure_ascii=False))
