"""Speech to text through an OpenAI-compatible /audio/transcriptions endpoint (standard library only)."""
import io
import json
import os
import uuid
import urllib.error
import urllib.request
import wave


class SttError(Exception):
    pass


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _multipart(fields: dict, file_field: str, filename: str, content: bytes, content_type: str):
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
              f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    out.write(content)
    out.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return boundary, out.getvalue()


class CloudStt:
    def __init__(self, base_url="https://api.openai.com/v1", model="whisper-1", language="pl",
                 api_key=None, api_key_env="OPENAI_API_KEY", timeout=15.0, prompt=""):
        self.url = base_url.rstrip("/") + "/audio/transcriptions"
        self.model, self.language, self.timeout, self.prompt = model, language, timeout, prompt
        self.api_key = api_key or os.environ.get(api_key_env, "")

    def transcribe_wav(self, wav: bytes) -> str:
        if not self.api_key:
            raise SttError("no API key (set the environment variable named in the config)")
        fields = {"model": self.model, "language": self.language, "response_format": "json", "temperature": "0"}
        if self.prompt:
            fields["prompt"] = self.prompt
        boundary, body = _multipart(fields, "file", "speech.wav", wav, "audio/wav")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SttError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SttError(f"request failed: {e}") from e
        except json.JSONDecodeError as e:
            raise SttError("answer is not JSON") from e
        return str(data.get("text", "")).strip()
