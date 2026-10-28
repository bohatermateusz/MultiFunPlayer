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

    def synthesize(self, text: str) -> bytes:
        if not self.api_key:
            raise TtsError("no API key (set the environment variable named in the config)")
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "pcm", "speed": self.speed}
        if self.instructions:
            body["instructions"] = self.instructions
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
