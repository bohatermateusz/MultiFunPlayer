"""Text to speech through an OpenAI-compatible /audio/speech endpoint (standard library only).
Returns raw 16-bit mono PCM at 24 kHz (response_format "pcm")."""
import json
import os
import urllib.error
import urllib.parse
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
        s = dict(self.stage_settings[max(0, min(int(stage), len(self.stage_settings) - 1))])
        if self.model.startswith("eleven_v3"):
            s.pop("style", None)          # v3 takes only stability / similarity / speaker boost
            s.pop("speed", None)
        return s

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

    def _get(self, path):
        if not self.api_key:
            raise TtsError("no ElevenLabs API key (ELEVENLABS_API_KEY)")
        req = urllib.request.Request(self.base_url + path, headers={"xi-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TtsError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise TtsError(f"request failed: {e}") from e

    def voices(self):
        """-> [(voice_id, name, labels)] of the account (labels carry preview_url when the API gives one)"""
        out = []
        for v in self._get("/v1/voices").get("voices", []):
            labels = dict(v.get("labels", {}) or {})
            if v.get("preview_url"):
                labels["preview_url"] = v["preview_url"]
            out.append((v.get("voice_id", ""), v.get("name", ""), labels))
        return out

    def library_voices(self, gender="female", age="young", language="pl", page_size=10):
        """-> [(voice_id, name, labels)] from the shared library (labels carry public_owner_id + preview_url)"""
        q = urllib.parse.urlencode({"gender": gender, "age": age, "language": language, "page_size": page_size})
        out = []
        for v in self._get("/v1/shared-voices?" + q).get("voices", []):
            labels = {"gender": v.get("gender", ""), "age": v.get("age", ""), "accent": v.get("accent", ""),
                      "use_case": v.get("use_case", ""), "public_owner_id": v.get("public_owner_id", ""),
                      "preview_url": v.get("preview_url", ""), "library": True}
            out.append((v.get("voice_id", ""), v.get("name", ""), labels))
        return out

    def add_library_voice(self, public_owner_id, voice_id, name):
        """adds a shared-library voice to the account (free) -> the new voice_id"""
        if not self.api_key:
            raise TtsError("no ElevenLabs API key (ELEVENLABS_API_KEY)")
        url = f"{self.base_url}/v1/voices/add/{public_owner_id}/{voice_id}"
        req = urllib.request.Request(url, data=json.dumps({"new_name": name}).encode("utf-8"), method="POST",
                                     headers={"xi-api-key": self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TtsError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise TtsError(f"request failed: {e}") from e
        return data.get("voice_id", voice_id)

    def pick_voice(self, prefer=None):
        """No voice_id configured: choose a girlish, natural female voice. Order: prefer["names"] among the account's
        voices, then labels (female + young / middle_aged), then any female, then (allow_library) the shared library,
        where the first match is added to the account. -> (voice_id, name) or None. Sets self.voice_id."""
        prefer = prefer or {}
        names = [n.lower() for n in prefer.get("names", [])]
        ages = set(prefer.get("ages", ["young", "middle_aged", "middle aged"]))
        mine = self.voices()
        by_name = {name.lower(): (vid, name) for vid, name, _ in mine}
        for n in names:
            if n in by_name:
                self.voice_id = by_name[n][0]
                return by_name[n]
        female = [(vid, name, lab) for vid, name, lab in mine if str(lab.get("gender", "")).lower() == "female"]
        young = [(vid, name) for vid, name, lab in female if str(lab.get("age", "")).lower() in ages]
        if young:
            self.voice_id = young[0][0]
            return young[0]
        if female:
            self.voice_id = female[0][0]
            return female[0][0], female[0][1]
        if prefer.get("allow_library"):
            for vid, name, lab in self.library_voices(language=self.language or "pl"):
                try:
                    new_id = self.add_library_voice(lab["public_owner_id"], vid, name)
                except TtsError:
                    continue
                self.voice_id = new_id
                return new_id, name
        return None


def pcm_chunks(pcm: bytes, size: int = 4800):
    """a whole PCM buffer as a stream of chunks (the OpenAI speaker answers in one piece)"""
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]


def stream_openai(speaker: "Speaker", text: str, stage: int = 0, soft=None, sharp=None):
    """stage is the voice index: 0-4 tender, 5-9 sharp (the instructions follow the tone when given)"""
    instructions = (sharp if stage >= 5 else soft) if (soft or sharp) else None
    return pcm_chunks(speaker.synthesize(text, instructions))
