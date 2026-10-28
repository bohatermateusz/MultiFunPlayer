"""Text to speech through an OpenAI-compatible /audio/speech endpoint (standard library only).
Returns raw 16-bit mono PCM at 24 kHz (response_format "pcm")."""
import json
import os
import urllib.error
import urllib.request

PCM_RATE = 24000


class TtsError(Exception):
    pass


class Speaker:
    def __init__(self, base_url="https://api.openai.com/v1", model="gpt-4o-mini-tts", voice="sage", instructions="",
                 api_key=None, api_key_env="OPENAI_API_KEY", timeout=15.0, speed=1.0):
        self.url = base_url.rstrip("/") + "/audio/speech"
        self.model, self.voice, self.instructions, self.timeout, self.speed = model, voice, instructions, timeout, speed
        self.api_key = api_key or os.environ.get(api_key_env, "")

    def synthesize(self, text: str, instructions=None) -> bytes:
        if not self.api_key:
            raise TtsError("no API key (set the environment variable named in the config)")
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "pcm", "speed": self.speed}
        instructions = self.instructions if instructions is None else instructions
        if instructions:
            body["instructions"] = instructions
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                pcm = resp.read()
        except urllib.error.HTTPError as e:
            raise TtsError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TtsError(f"request failed: {e}") from e
        if len(pcm) < 2:
            raise TtsError("empty answer")
        return pcm[: len(pcm) - len(pcm) % 2]


class ElevenLabsSpeaker:
    """ElevenLabs text to speech, streamed: stream(text, stage) yields 16-bit mono PCM chunks (24 kHz) as they
    arrive, so playback can start after the first chunk. voice_settings follow the escalation stage."""

    def __init__(self, voice_id="", model="eleven_flash_v2_5", api_key=None, api_key_env="ELEVENLABS_API_KEY",
                 base_url="https://api.elevenlabs.io", timeout=15.0, stage_settings=None, language="pl", chunk_bytes=4096):
        self.voice_id, self.model, self.timeout, self.language, self.chunk_bytes = voice_id, model, timeout, language, chunk_bytes
        self.base_url = base_url.rstrip("/")
        self.stage_settings = stage_settings or []
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.last_response = None    # the open HTTP response, so that a barge-in can close it

    def _settings(self, stage):
        if not self.stage_settings:
            return None
        return self.stage_settings[max(0, min(int(stage), len(self.stage_settings) - 1))]

    def _headers(self):
        return {"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/pcm"}

    def stream(self, text: str, stage: int = 0):
        if not self.api_key:
            raise TtsError("no ElevenLabs API key (ELEVENLABS_API_KEY)")
        if not self.voice_id:
            raise TtsError("no voice_id (persona.tts.voice_id); list your voices with: python voice_control.py voices")
        body = {"text": text, "model_id": self.model}
        settings = self._settings(stage)
        if settings:
            body["voice_settings"] = settings
        if self.language:
            body["language_code"] = self.language
        url = f"{self.base_url}/v1/text-to-speech/{self.voice_id}/stream?output_format=pcm_24000"
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=self._headers())
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise TtsError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TtsError(f"request failed: {e}") from e
        self.last_response = resp
        return self._chunks(resp)

    def _chunks(self, resp):
        try:
            got = False
            while True:
                chunk = resp.read(self.chunk_bytes)
                if not chunk:
                    break
                got = True
                yield chunk
            if not got:
                raise TtsError("empty answer")
        except (OSError, TimeoutError, ValueError) as e:
            raise TtsError(f"stream failed: {e}") from e
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def voices(self):
        """-> [(voice_id, name, labels)] of the account"""
        if not self.api_key:
            raise TtsError("no ElevenLabs API key (ELEVENLABS_API_KEY)")
        req = urllib.request.Request(self.base_url + "/v1/voices", headers={"xi-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TtsError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise TtsError(f"request failed: {e}") from e
        return [(v.get("voice_id", ""), v.get("name", ""), v.get("labels", {})) for v in data.get("voices", [])]


def pcm_chunks(pcm: bytes, size: int = 4800):
    """a whole PCM buffer as a stream of chunks (the OpenAI speaker answers in one piece)"""
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]


def stream_openai(speaker: "Speaker", text: str, stage: int = 0, soft=None, sharp=None):
    """stage is the voice index: 0-4 tender, 5-9 sharp (the instructions follow the tone when given)"""
    instructions = (sharp if stage >= 5 else soft) if (soft or sharp) else None
    return pcm_chunks(speaker.synthesize(text, instructions))
