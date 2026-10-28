"""Decides what to do with a transcript: match, cool-down, optional confirmation, dry run.

Two ways to a command: the strict matcher (wake word + closed list of phrases; offline, instant) and,
when that finds nothing, an optional LLM that guesses which configured command was meant (see llm.py).
The LLM only picks one of the configured commands; the tool validates the pick and runs its steps."""
import copy
import json
import threading
import time
from dataclasses import dataclass, field

from matcher import Match, _sim, match, normalize

YES = {"tak", "potwierdzam", "dawaj", "jasne"}
NO = {"nie", "anuluj", "stop", "odwolaj"}

LLM_DEFAULTS = {"min_confidence": 0.8, "max_words": 14, "prefilter_similarity": 0.5, "prefilter_min_length": 5, "context": 3,
                "max_steps": 4, "max_faster": 3, "max_deeper": 3}


@dataclass
class Outcome:
    status: str              # executed | dry-run | ignored | cooldown | confirm | cancelled
    detail: str = ""
    intent: str = ""
    score: float = 0.0
    via: str = "fast"        # fast | llm
    extra: dict = field(default_factory=dict)


class _NeedLlm:
    """the strict matcher found nothing, but the utterance may still be a command"""

    def __init__(self, transcript, recent):
        self.transcript, self.recent = transcript, recent


class Controller:
    def __init__(self, intents, run_steps, dry_run=False, log_path=None, clock=time.monotonic, confirm_timeout=6.0,
                 wake_words=None, wake_min_similarity=0.8, llm=None, llm_cfg=None):
        self.intents = intents
        self.wake_words = [normalize(w) for w in (wake_words or []) if normalize(w)]
        self.wake_min_similarity = wake_min_similarity
        self.llm = llm
        self.llm_cfg = dict(LLM_DEFAULTS, **(llm_cfg or {}))
        self._lock = threading.RLock()   # voice and keyboard threads share the cool-downs
        self.run_steps = run_steps        # callable(list of steps)
        self.dry_run = dry_run
        self.log_path = log_path
        self.clock = clock
        self.confirm_timeout = confirm_timeout
        self._last = {}                   # intent id -> time of last execution
        self._pending = None              # (Match, deadline, override)
        self._recent = []                 # ids of the last executed commands

    def _log(self, transcript, outcome):
        if not self.log_path:
            return
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "transcript": transcript, "status": outcome.status,
                  "intent": outcome.intent, "score": round(outcome.score, 3), "detail": outcome.detail}
        if outcome.via != "fast":
            record["via"] = outcome.via
        record.update(outcome.extra)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def handle(self, transcript: str) -> Outcome:
        with self._lock:
            result = self._handle(transcript)
            if not isinstance(result, _NeedLlm):
                self._log(transcript, result)
                return result
        # the (slow) model call runs without the lock: the keyboard shortcuts must not wait for it
        answer = self._ask_llm(result)
        with self._lock:
            outcome = self._finish_llm(transcript, answer)
            self._log(transcript, outcome)
            return outcome

    def press(self, intent_id: str) -> Outcome:
        """A keyboard shortcut: the intent runs at once (no wake word, no cool-down, no confirmation)."""
        with self._lock:
            intent = next((i for i in self.intents if i.id == intent_id), None)
            if intent is None:
                outcome = Outcome("ignored", f"no intent {intent_id}")
            elif self.dry_run:
                outcome = Outcome("dry-run", f"would run {len(intent.steps)} step(s)", intent.id, 1.0, "key")
            else:
                self.run_steps(intent.steps)
                self._remember(intent.id)
                outcome = Outcome("executed", "key", intent.id, 1.0, "key")
            self._log(f"[key] {intent_id}", outcome)
            return outcome

    def strip_wake_word(self, transcript: str):
        """-> the transcript without the leading wake word, or None when it does not start with one.
        Without configured wake words the transcript is returned unchanged."""
        if not self.wake_words:
            return transcript
        tokens = normalize(transcript).split()
        if not tokens:
            return None
        if not any(_sim(w, tokens[0]) >= self.wake_min_similarity for w in self.wake_words):
            return None
        return " ".join(tokens[1:])

    def _remember(self, intent_id):
        self._recent.append(intent_id)
        del self._recent[:-max(1, int(self.llm_cfg["context"]))]

    def _llm_candidate(self, transcript: str) -> bool:
        """cheap local filter: only short utterances that start with something like the wake word are
        worth a model call (film dialogue and talk never leave the computer as text)"""
        if self.llm is None or not self.llm_cfg.get("enabled", True):
            return False
        tokens = normalize(transcript).split()
        if not tokens or len(tokens) > int(self.llm_cfg["max_words"]):
            return False
        if not self.wake_words:
            return True
        threshold = float(self.llm_cfg["prefilter_similarity"])
        min_length = int(self.llm_cfg["prefilter_min_length"])   # "nie" is 55% of "kochanie", but it is no address
        return any(len(t) >= min_length and _sim(w, t) >= threshold for w in self.wake_words for t in tokens[:2])

    def _handle(self, transcript: str):
        now = self.clock()

        if self._pending:
            m, deadline, override = self._pending
            self._pending = None
            if now <= deadline:
                words = set(normalize(transcript).split())
                if words & YES and not words & NO:
                    return self._execute(m, now, override)
                return Outcome("cancelled", "no confirmation", m.intent.id, m.score)
            # confirmation timed out: treat the new utterance as a normal one

        command = self.strip_wake_word(transcript)
        if command is None:
            return self._maybe_llm(transcript, Outcome("ignored", "no wake word"))
        if not command.strip():
            return Outcome("ignored", "wake word only")

        m, reason = match(command, self.intents)
        if m is None:
            return self._maybe_llm(transcript, Outcome("ignored", reason))

        return self._run_match(m, now)

    def _maybe_llm(self, transcript, fallback):
        if self._llm_candidate(transcript):
            return _NeedLlm(transcript, list(self._recent))
        return fallback

    def _run_match(self, m: Match, now: float, override=None, via="fast", extra=None):
        last = self._last.get(m.intent.id)
        if last is not None and now - last < m.intent.cooldown:
            return Outcome("cooldown", f"{now - last:.1f}s < {m.intent.cooldown}s", m.intent.id, m.score, via, extra or {})

        if m.intent.confirm:
            self._pending = (m, now + self.confirm_timeout, override)
            return Outcome("confirm", "say 'tak' to confirm", m.intent.id, m.score, via, extra or {})

        return self._execute(m, now, override, via, extra)

    def _ask_llm(self, need: _NeedLlm):
        try:
            return self.llm.classify(need.transcript, self.intents, self.wake_words, need.recent)
        except Exception as e:   # network, key, bad answer: the utterance is simply not a command
            return e

    def _finish_llm(self, transcript, answer) -> Outcome:
        if isinstance(answer, Exception):
            return Outcome("ignored", f"llm unavailable ({answer})", via="llm")
        extra = {"llm_raw": answer.raw, "latency_ms": answer.latency_ms}
        intent = next((i for i in self.intents if i.id == answer.intent), None)
        if intent is None:
            return Outcome("ignored", "llm: not a command", via="llm", extra=extra)
        needed = intent.llm_min_confidence if intent.llm_min_confidence is not None else float(self.llm_cfg["min_confidence"])
        if not answer.addressed:
            return Outcome("ignored", "llm: not said to me", intent.id, answer.confidence, "llm", extra)
        if answer.confidence < needed:
            return Outcome("ignored", f"llm: confidence {answer.confidence:.2f} < {needed:.2f}", intent.id, answer.confidence, "llm", extra)
        override = {"times": max(1, min(3, answer.times)), "count": max(1, min(5, answer.count))}
        if intent.id == "improvise":
            steps, used = self._improvised_steps(answer.plan or [])
            if not steps:
                return Outcome("ignored", "llm: empty plan", intent.id, answer.confidence, "llm", extra)
            override["steps"] = steps
            extra = dict(extra, plan=used)
        m = Match(intent, answer.confidence, "[llm]")
        return self._run_match(m, self.clock(), override, "llm", extra)

    def _improvised_steps(self, plan):
        """the model's plan -> (steps, [used step ids]); only commands flagged "improvise" are allowed, the number of
        steps and of speed / depth increases is capped, whatever the model asks for"""
        by_id = {i.id: i for i in self.intents if i.improvise}
        steps, used = [], []
        increases = {"faster": 0, "deeper": 0}
        for item in plan[:int(self.llm_cfg["max_steps"])]:
            intent = by_id.get(item.get("step"))
            if intent is None:
                continue
            times = max(1, min(2, int(item.get("times", 1)))) if intent.repeatable else 1
            if intent.id in increases:
                room = int(self.llm_cfg["max_" + intent.id]) - increases[intent.id]
                times = min(times, room)
                if times <= 0:
                    continue
                increases[intent.id] += times
            steps += copy.deepcopy(intent.steps) * times
            used.append(f"{intent.id}x{times}" if times > 1 else intent.id)
        return steps, used

    def _steps_for(self, intent, override):
        if override and "steps" in override:
            return copy.deepcopy(override["steps"])
        steps = copy.deepcopy(intent.steps)
        if not override:
            return steps
        for step in steps:
            if step["action"].endswith("LubeLauncher::FireMany"):
                step["args"] = [override["count"]]
        if intent.repeatable and override["times"] > 1:
            steps = steps * override["times"]
        return steps

    def _execute(self, m: Match, now: float, override=None, via="fast", extra=None) -> Outcome:
        self._last[m.intent.id] = now
        steps = self._steps_for(m.intent, override)
        if self.dry_run:
            return Outcome("dry-run", f"would run {len(steps)} step(s)", m.intent.id, m.score, via, extra or {})
        self.run_steps(steps)
        self._remember(m.intent.id if m.intent.id != "improvise" else "improvised " + "+".join((extra or {}).get("plan", [])))
        return Outcome("executed", "", m.intent.id, m.score, via, extra or {})
