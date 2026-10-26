"""Decides what to do with a transcript: match, cool-down, optional confirmation, dry run."""
import json
import time
from dataclasses import dataclass

from matcher import Match, match, normalize

YES = {"tak", "potwierdzam", "dawaj", "jasne"}
NO = {"nie", "anuluj", "stop", "odwolaj"}


@dataclass
class Outcome:
    status: str              # executed | dry-run | ignored | cooldown | confirm | cancelled
    detail: str = ""
    intent: str = ""
    score: float = 0.0


class Controller:
    def __init__(self, intents, run_steps, dry_run=False, log_path=None, clock=time.monotonic, confirm_timeout=6.0):
        self.intents = intents
        self.run_steps = run_steps        # callable(list of steps)
        self.dry_run = dry_run
        self.log_path = log_path
        self.clock = clock
        self.confirm_timeout = confirm_timeout
        self._last = {}                   # intent id -> time of last execution
        self._pending = None              # (Match, deadline)

    def _log(self, transcript, outcome):
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "transcript": transcript,
                                "status": outcome.status, "intent": outcome.intent, "score": round(outcome.score, 3),
                                "detail": outcome.detail}, ensure_ascii=False) + "\n")

    def handle(self, transcript: str) -> Outcome:
        outcome = self._handle(transcript)
        self._log(transcript, outcome)
        return outcome

    def _handle(self, transcript: str) -> Outcome:
        now = self.clock()

        if self._pending:
            m, deadline = self._pending
            self._pending = None
            if now <= deadline:
                words = set(normalize(transcript).split())
                if words & YES and not words & NO:
                    return self._execute(m, now)
                return Outcome("cancelled", "no confirmation", m.intent.id, m.score)
            # confirmation timed out: treat the new utterance as a normal one

        m, reason = match(transcript, self.intents)
        if m is None:
            return Outcome("ignored", reason)

        last = self._last.get(m.intent.id)
        if last is not None and now - last < m.intent.cooldown:
            return Outcome("cooldown", f"{now - last:.1f}s < {m.intent.cooldown}s", m.intent.id, m.score)

        if m.intent.confirm:
            self._pending = (m, now + self.confirm_timeout)
            return Outcome("confirm", "say 'tak' to confirm", m.intent.id, m.score)

        return self._execute(m, now)

    def _execute(self, m: Match, now: float) -> Outcome:
        self._last[m.intent.id] = now
        if self.dry_run:
            return Outcome("dry-run", f"would run {len(m.intent.steps)} step(s)", m.intent.id, m.score)
        self.run_steps(m.intent.steps)
        return Outcome("executed", "", m.intent.id, m.score)
