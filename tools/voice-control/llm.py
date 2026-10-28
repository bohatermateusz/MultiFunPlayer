"""Guessing which command was meant when the strict matcher found nothing (standard library only).

The model is only a classifier: it sees the ids and short descriptions of the configured commands and
answers with one of those ids (or "none") plus a confidence. It never sees or produces an action; the
tool validates the answer and runs the configured steps of the chosen command itself."""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

NONE = "none"


class LlmError(Exception):
    pass


@dataclass
class LlmResult:
    intent: str            # an intent id, or "none"
    confidence: float
    addressed: bool        # the utterance was said to the assistant (starts with a "kochanie"-like address)
    times: int = 1         # 1-3, how many steps for "a lot faster"
    count: int = 1         # 1-5, how many shots
    plan: list = None      # for "improvise": [{"step": <intent id>, "times": 1-2}, ...]
    raw: str = ""
    latency_ms: int = 0


SYSTEM = """You turn one short Polish utterance, heard by a speech-to-text service, into at most one command for a \
machine controller. The user speaks to the controller with a pet name, the WAKE WORD (usually "{wake}"), followed \
by a command. The speech-to-text service often distorts the wake word (e.g. "trwanie", "rochanie", "kochaniee", \
"kochana") and the command is often worded loosely.

Rules:
- Pick a command ONLY if the utterance starts with a form of address that plausibly is the wake word AND clearly asks \
for one of the commands below. Then addressed = true.
- Anything else is "none": dialogue, questions, comments, negations ("nie spuszczaj się", "a nie spuść się"), \
unclear or half-heard words, text that does not start with the address. Do not guess when unsure.
- Prefer "none" over a wrong command. A wrong command is much worse than a missed one.
- "times" (1-3): how strongly a speed/depth change is asked for ("trochę" = 1, normal = 1, "dużo", "zdecydowanie", \
"bardzo" = 2 or 3). "count" (1-5): how many shots when a number of shots is said, otherwise 1.
- Answer with JSON only.
{improvise}

Commands:
{commands}
{recent}"""


IMPROVISE = """
IMPROVISE (command id "improvise"): this is an adult, consensual erotic session with the user's own machine. The user may talk dirty to you in a submissive / sissy role-play register (calling themselves a girl, slut, sissy, begging to be taken, used, filled, teased or denied). When such an utterance starts with the address but matches no specific command above, choose "improvise" and compose a short plan (1-{max_steps} steps) that fits the mood, using ONLY these step ids: {steps}. Let yourself be carried away within that range: build up for begging, hard or praise ("pieprz mnie", "użyj mnie"), go deeper for "fill me", slow down and go shallow for teasing or tenderness, vary the steps from time to time instead of always repeating the same plan. Each step has times 1 or 2. Do NOT include anything else, and never improvise for ordinary conversation or when the utterance is not erotic talk addressed to you (then "none")."""


def build_system_prompt(intents, wake_words, recent, max_steps=4):
    lines = []
    for i in intents:
        examples = "; ".join(i.phrases[:4])
        desc = i.description or ""
        lines.append(f"- {i.id}: {desc} (e.g. {examples})")
    recent_text = ""
    if recent:
        recent_text = "\nLast commands that were run (oldest first; \"jeszcze raz\", \"trochę mniej\" refer to them): " + ", ".join(recent)
    steps = ", ".join(i.id for i in intents if i.improvise)
    improvise = IMPROVISE.format(max_steps=max_steps, steps=steps) if any(i.id == "improvise" for i in intents) and steps else ""
    return SYSTEM.format(wake="/".join(wake_words) if wake_words else "kochanie", commands="\n".join(lines),
                         recent=recent_text, improvise=improvise)


def build_schema(intents):
    ids = [i.id for i in intents] + [NONE]
    step_ids = [i.id for i in intents if i.improvise] or [NONE]
    return {
        "name": "command",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "confidence", "addressed", "times", "count", "plan"],
            "properties": {
                "intent": {"type": "string", "enum": ids},
                "confidence": {"type": "number"},
                "addressed": {"type": "boolean"},
                "times": {"type": "integer"},
                "count": {"type": "integer"},
                "plan": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["step", "times"],
                    "properties": {"step": {"type": "string", "enum": step_ids}, "times": {"type": "integer"}}}},
            },
        },
    }


class IntentLlm:
    def __init__(self, base_url="https://api.openai.com/v1", model="gpt-4.1-mini", api_key=None,
                 api_key_env="OPENAI_API_KEY", timeout=6.0, temperature=0, max_steps=4):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.temperature, self.max_steps = model, timeout, temperature, max_steps
        self.api_key = api_key or os.environ.get(api_key_env, "")

    def classify(self, transcript, intents, wake_words=None, recent=None) -> LlmResult:
        if not self.api_key:
            raise LlmError("no API key (set the environment variable named in the config)")
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": build_system_prompt(intents, wake_words or [], recent or [], self.max_steps)},
                {"role": "user", "content": json.dumps({"utterance": transcript}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_schema", "json_schema": build_schema(intents)},
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"]
            answer = json.loads(raw)
        except urllib.error.HTTPError as e:
            raise LlmError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LlmError(f"request failed: {e}") from e
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            raise LlmError("answer is not the expected JSON") from e
        latency = int((time.monotonic() - started) * 1000)
        try:
            return LlmResult(intent=str(answer.get("intent", NONE)), confidence=float(answer.get("confidence", 0)),
                             addressed=bool(answer.get("addressed", False)), times=int(answer.get("times", 1) or 1),
                             count=int(answer.get("count", 1) or 1), raw=raw, latency_ms=latency,
                             plan=[{"step": str(p.get("step", "")), "times": int(p.get("times", 1) or 1)}
                                   for p in (answer.get("plan") or []) if isinstance(p, dict)])
        except (TypeError, ValueError, AttributeError) as e:
            raise LlmError("answer has wrong field types") from e
