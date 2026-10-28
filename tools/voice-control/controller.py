"""Decides what to do with a transcript: match, cool-down, optional confirmation, dry run.

Two ways to a command: the strict matcher (wake word + closed list of phrases; offline, instant) and,
when that finds nothing, an optional LLM that guesses which configured command was meant (see llm.py).
The LLM only picks one of the configured commands; the tool validates the pick and runs its steps."""
import copy
import json
import random
import threading
import time
from dataclasses import dataclass, field

from matcher import Match, _sim, match, normalize

YES = {"tak", "potwierdzam", "dawaj", "jasne"}
NO = {"nie", "anuluj", "stop", "odwolaj"}

LLM_DEFAULTS = {"min_confidence": 0.8, "max_words": 14, "prefilter_similarity": 0.5, "prefilter_min_length": 5, "context": 3,
                "max_steps": 4, "max_faster": 3, "max_deeper": 3, "max_lube": 1}
PERSONA_DEFAULTS = {"enabled": True, "reply_window": 15.0, "base_p": 0.25, "step_p": 0.2, "confession_p": 0.3, "max_asks": 5,
                    "silence_reset": 180.0, "gated": [], "tease_allowed": [], "max_words": 30,
                    "never_gated": ["halt", "lock"], "fallback_plain": [], "base_p_overrides": {},
                    "stages": ["spokojnie, chłodno, lekko uwodzicielsko"],
                    "escalation": {"enabled": True, "max_stage": 4, "points": {"turn": 1, "denied": 1, "confession": 2, "granted": 2},
                                   "thresholds": [0, 5, 12, 22, 35], "heat_reset": 600, "tease_chance": [0.3, 0.5, 0.7, 0.85, 1.0]},
                    "initiative": {"enabled": True, "idle_prompt_seconds": 25, "max_nudges": 3, "session_seconds": 180}}


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


class _NeedPersona:
    """the utterance goes to the persona (a request she decides on, or conversation); `plain` is the strict match, if
    any, that may run plainly when she cannot be reached"""

    def __init__(self, transcript, state, history, plain=None):
        self.transcript, self.state, self.history, self.plain = transcript, state, history, plain


class Controller:
    def __init__(self, intents, run_steps, dry_run=False, log_path=None, clock=time.monotonic, confirm_timeout=6.0,
                 wake_words=None, wake_min_similarity=0.8, llm=None, llm_cfg=None, lock_cfg=None,
                 persona=None, persona_cfg=None, speak=None, rng=None):
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
        self.lock_cfg = dict({"allow": []}, **(lock_cfg or {}))
        self._locked_until = None         # "hold": until then only the allowed commands run
        # the persona ("Pani"): see persona.py. speak(text) is called after a turn, outside the lock.
        self.persona = persona
        self.persona_cfg = persona_cfg or {}
        self.speak = speak
        self.rng = rng or random.Random()
        self._window_until = 0.0          # until then an utterance without the wake word is an answer to her
        self._asks = {}                   # request id -> how many times it was denied so far
        self._last_ask = 0.0
        self._owed = None                 # request id she will grant once the user does what she demanded
        self._history = []                # [(role, text)] of the conversation with her
        self._heat = 0.0                  # escalation: grows with turns, refusals, confessions, grants
        self._heat_last = 0.0
        self._last_user = 0.0             # last time the user talked to her
        self._last_line = 0.0             # last time she spoke (a line or a nudge)
        self._nudges = 0
        self.busy = lambda: False         # True while she is speaking / listening is muted (set by the main loop)
        self.speak_ex = None              # speak_ex(line, stage): like speak, but knows the escalation stage

    @property
    def persona_cfg(self):
        return self._persona_cfg

    @persona_cfg.setter
    def persona_cfg(self, value):
        # whatever is assigned (also by tests) is completed with the defaults
        self._persona_cfg = dict(PERSONA_DEFAULTS, **(value or {}))

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
            if isinstance(result, Outcome):
                self._log(transcript, result)
                return self._say(result)
        # the (slow) model call runs without the lock: the keyboard shortcuts must not wait for it
        if isinstance(result, _NeedPersona):
            answer = self._ask_persona(result)
            with self._lock:
                outcome = self._finish_persona(transcript, answer)
                self._log(transcript, outcome)
            return self._say(outcome)
        answer = self._ask_llm(result)
        with self._lock:
            outcome = self._finish_llm(transcript, answer)
            self._log(transcript, outcome)
            return self._say(outcome)

    def _say(self, outcome):
        line = outcome.extra.get("say")
        if line and (self.speak or self.speak_ex):
            try:
                if self.speak_ex:
                    self.speak_ex(line, outcome.extra.get("stage", 0))
                else:
                    self.speak(line)
            except Exception:   # a silent persona must never break the loop
                pass
        return outcome

    # ---- escalation: the tool, not the model, decides how far the session has gone ---------------------
    def stage(self) -> int:
        esc = self.persona_cfg["escalation"]
        if not esc.get("enabled", True):
            return 0
        now = self.clock()
        if self._heat_last and now - self._heat_last > float(esc["heat_reset"]):
            self._heat = 0.0
        stage = sum(1 for t in esc["thresholds"] if self._heat >= t) - 1
        return max(0, min(stage, int(esc["max_stage"]), len(esc["thresholds"]) - 1))

    def _heat_up(self, kind):
        points = float(self.persona_cfg["escalation"].get("points", {}).get(kind, 0))
        self._heat += points
        self._heat_last = self.clock()

    def _tone(self, stage):
        stages = self.persona_cfg["stages"]
        return stages[max(0, min(stage, len(stages) - 1))] if stages else ""

    def _tease_roll(self, stage):
        chances = self.persona_cfg["escalation"].get("tease_chance", [1.0])
        return self.rng.random() < float(chances[max(0, min(stage, len(chances) - 1))])

    # ---- initiative: she speaks when the user is silent ----------------------------------------------------
    def idle_nudge(self):
        """Called about once a second by the main loop. When a session is on and the user has been silent for
        idle_prompt_seconds, she says something herself (no commands). -> the line spoken, or None."""
        ini = self.persona_cfg["initiative"]
        if not self.persona_active() or not ini.get("enabled", True) or self.busy():
            return None
        now = self.clock()
        with self._lock:
            if not self._last_user or now - self._last_user > float(ini["session_seconds"]) + float(ini["idle_prompt_seconds"]) * (int(ini["max_nudges"]) + 1):
                return None                      # no session on
            if self._nudges >= int(ini["max_nudges"]):
                return None
            if now - max(self._last_user, self._last_line) < float(ini["idle_prompt_seconds"]):
                return None
            state = dict(self._persona_state(), etap=self.stage(), ton=self._tone(self.stage()))
            history = list(self._history)
            stage = self.stage()
            self._last_line = now                # a failed call must not be retried in a loop
        try:
            line = self.persona.nudge(state, history)
        except Exception:
            return None
        if not line:
            return None
        with self._lock:
            self._nudges += 1
            self._history.append(("assistant", line))
            self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
            self._last_line = self.clock()
            outcome = Outcome("talk", "nudge", "", 0.0, "persona", {"say": line, "stage": stage, "nudge": self._nudges})
            self._log("[silence]", outcome)
        self._say(outcome)
        return line

    # ---- the persona ----------------------------------------------------------------------------
    def persona_active(self) -> bool:
        return self.persona is not None and bool(self.persona_cfg.get("enabled", True))

    def in_reply_window(self) -> bool:
        return self.clock() < self._window_until

    def _gated(self, intent_id: str) -> bool:
        return self.persona_active() and intent_id in self.persona_cfg["gated"]

    def _persona_state(self):
        now = self.clock()
        if now - self._last_ask > float(self.persona_cfg["silence_reset"]):
            self._asks.clear()
            self._owed = None
        stage = self.stage()
        address = self.persona_cfg.get("address", {})
        return {"etap": stage, "ton": self._tone(stage), "zwroty_do_ciebie": list(address.get("titles", [])),
                "zwroty_do_niej": list(address.get("names", [])), "prosby_bez_zgody": dict(self._asks),
                "nalezy_sie_po_wykonaniu_zadania": self._owed, "blokada_5_min": self.lock_remaining() > 0,
                "ostatnie_komendy": list(self._recent)}

    def _need_persona(self, transcript, plain=None):
        return _NeedPersona(transcript, self._persona_state(), list(self._history), plain)

    def _ask_persona(self, need: _NeedPersona):
        try:
            step_ids = [i.id for i in self.intents if i.improvise]
            return self.persona.respond(need.transcript, self.intents, step_ids, need.state, need.history)
        except Exception as e:
            return (e, need.plain)

    def _decide(self, request_id, turn):
        """the honest dice: -> (granted, p, roll)"""
        c = self.persona_cfg
        asks = self._asks.get(request_id, 0)
        if self._owed == request_id and turn.thanked:
            return True, 1.0, 0.0
        base = float(c["base_p_overrides"].get(request_id, c["base_p"]))
        p = base + float(c["step_p"]) * asks + (float(c["confession_p"]) if turn.confession else 0.0)
        if asks + 1 >= int(c["max_asks"]):
            p = max(p, 0.95)
        p = min(p, 0.95)
        roll = self.rng.random()
        return roll < p, p, roll

    def _persona_steps(self, actions, allowed_ids=None):
        steps, used = self._improvised_steps(actions)
        if allowed_ids is not None:
            keep = [u for u in used if u.split("x")[0] in allowed_ids]
            if len(keep) != len(used):
                by_id = {i.id: i for i in self.intents}
                steps = []
                for u in keep:
                    base, _, times = u.partition("x")
                    steps += copy.deepcopy(by_id[base].steps) * (int(times) if times else 1)
                used = keep
        return steps, used

    def _finish_persona(self, transcript, turn) -> Outcome:
        if isinstance(turn, tuple):
            error, plain = turn
            if plain is not None and plain.intent.id in self.persona_cfg["fallback_plain"]:
                # she cannot be reached: a mild command (calm, slower, pause...) must not get stuck with her
                o = self._run_match(plain, self.clock(), None, "persona", {})
                o.detail = f"persona unavailable ({error}), ran plain" + (f"; {o.detail}" if o.detail else "")
                return o
            return Outcome("ignored", f"persona unavailable ({error})", via="persona")
        extra = {"persona_raw": turn.raw, "latency_ms": turn.latency_ms}
        if not turn.addressed:
            return Outcome("ignored", "persona: not said to her", via="persona", extra=extra)

        now = self.clock()
        self._history.append(("user", transcript))
        del self._history[:-24]
        self._last_user, self._nudges = now, 0
        self._heat_up("turn")
        if turn.confession:
            self._heat_up("confession")
        stage = self.stage()

        def spoken(line, outcome):
            if outcome.status in ("executed", "dry-run") and outcome.via == "persona":
                self._heat_up("granted")
            elif outcome.status == "denied":
                self._heat_up("denied")
            if line:
                outcome.extra["say"] = line
                outcome.extra["stage"] = stage
                self._history.append(("assistant", line))
                self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
                self._last_line = self.clock()
            return outcome

        request = turn.request
        intent = next((i for i in self.intents if i.id == request), None)
        locked = self.lock_remaining() > 0

        # not a request: conversation (a confession, an answer to her question, thanks)
        if intent is None and request != "improvise":
            if self._owed and turn.thanked:
                owed = self._owed
                self._owed = None
                owed_intent = next((i for i in self.intents if i.id == owed), None)
                if owed_intent is not None:
                    return spoken(turn.line_if_granted, self._grant(owed_intent, [], extra, now, "owed"))
            if turn.confession:
                extra["confession"] = True
            return spoken(turn.line_if_denied or turn.line_if_granted, Outcome("talk", "persona", "", 0.0, "persona", extra))

        request_id = request
        if intent is not None and not self._gated(request_id):
            # not hers to decide (e.g. a mode or "slower"): runs at once, she may still comment
            o = self._run_match(Match(intent, 1.0, "[persona]"), now, None, "persona", dict(extra))
            return spoken(turn.line_if_granted, o)

        if locked and not request_id.startswith("lube_"):
            o = Outcome("locked", "held: she only talks (and the Lube Launcher works)", request_id, 0.0, "persona", extra)
            return spoken(turn.line_if_denied, o)

        granted, p, roll = self._decide(request_id, turn)
        extra.update(p=round(p, 2), roll=round(roll, 2), asks=self._asks.get(request_id, 0), demand=turn.demand)
        self._last_ask = now
        if granted:
            self._asks.pop(request_id, None)
            self._owed = None
            if intent is None:   # "improvise": whatever she planned, bounded like a scene
                steps, used = self._persona_steps(turn.grant_actions)
                if locked:
                    steps, used = self._persona_steps(turn.grant_actions, {"lube_fire"})
                if not steps:
                    return spoken(turn.line_if_denied or turn.line_if_granted, Outcome("talk", "persona: nothing to grant", "improvise", 0.0, "persona", extra))
                extra["plan"] = used
                improvise = next((i for i in self.intents if i.id == "improvise"), None)
                m = Match(improvise, 1.0, "[persona]")
                return spoken(turn.line_if_granted, self._run_match(m, now, {"steps": steps, "times": 1, "count": 1}, "persona", extra))
            return spoken(turn.line_if_granted, self._grant(intent, turn.grant_actions, extra, now, "granted"))

        self._asks[request_id] = self._asks.get(request_id, 0) + 1
        if turn.demand in ("thank", "repeat", "confess"):
            self._owed = request_id
        o = Outcome("denied", f"persona: {turn.demand}", request_id, 0.0, "persona", extra)
        if not locked and self._tease_roll(stage):
            steps, used = self._persona_steps(turn.tease_actions, set(self.persona_cfg["tease_allowed"]))
            if steps:
                extra["tease"] = used
                if not self.dry_run:
                    self.run_steps(steps)
        return spoken(turn.line_if_denied, o)

    def _grant(self, intent, actions, extra, now, how):
        """run the request itself (its own steps), plus what she added, bounded like a scene"""
        steps = copy.deepcopy(intent.steps)
        added, used = self._persona_steps([a for a in actions if a.get("step") != intent.id])
        if self.lock_remaining() > 0:
            added, used = [], []
        if used:
            extra["plan"] = used
        extra["decision"] = how
        m = Match(intent, 1.0, "[persona]")
        return self._run_match(m, now, {"steps": steps + added, "times": 1, "count": 1}, "persona", dict(extra))

    def press(self, intent_id: str) -> Outcome:
        """A keyboard shortcut: the intent runs at once (no wake word, no cool-down, no confirmation)."""
        with self._lock:
            intent = next((i for i in self.intents if i.id == intent_id), None)
            if intent is None:
                outcome = Outcome("ignored", f"no intent {intent_id}")
            elif (blocked := self._blocked_by_lock(intent)) is not None:
                outcome = blocked
                outcome.via = "key"
            elif self.dry_run:
                self._track_lock(intent)
                outcome = Outcome("dry-run", f"would run {len(intent.steps)} step(s)", intent.id, 1.0, "key")
            else:
                self.run_steps(intent.steps)
                self._track_lock(intent)
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

    def lock_remaining(self) -> float:
        if self._locked_until is None:
            return 0.0
        left = self._locked_until - self.clock()
        if left <= 0:
            self._locked_until = None
            return 0.0
        return left

    def _blocked_by_lock(self, intent):
        """the hold ("utrzymaj to") ignores everything except the configured commands (Lube Launcher, stop, release)"""
        left = self.lock_remaining()
        if left > 0 and intent.id not in self.lock_cfg["allow"]:
            return Outcome("locked", f"held for another {left:.0f}s (only the safe word 'koniec' ends it)", intent.id)
        return None

    def _track_lock(self, intent):
        if intent.id == "lock":
            minutes = next((a for step in intent.steps for a in step.get("args", []) if isinstance(a, int)), 5)
            self._locked_until = self.clock() + max(1, minutes) * 60
        elif intent.id == "halt":
            self._locked_until = None

    def _remember(self, intent_id):
        self._recent.append(intent_id)
        del self._recent[:-max(1, int(self.llm_cfg["context"]))]

    def _llm_candidate(self, transcript: str) -> bool:
        """cheap local filter: only short utterances that start with something like the wake word are
        worth a model call (film dialogue and talk never leave the computer as text)"""
        if not self.persona_active() and (self.llm is None or not self.llm_cfg.get("enabled", True)):
            return False
        tokens = normalize(transcript).split()
        if not tokens or len(tokens) > int(self.llm_cfg["max_words"]):
            return False
        if not self.wake_words:
            return True
        threshold = float(self.llm_cfg["prefilter_similarity"])
        min_length = int(self.llm_cfg["prefilter_min_length"])   # "nie" is 55% of "kochanie", but it is no address
        # a short wake word ("pani") only counts as the very first word ("proszę pani" in a film is no address)
        return any(len(t) >= min(min_length, len(w)) and _sim(w, t) >= threshold
                   for w in self.wake_words for t in tokens[:2 if len(w) >= 5 else 1])

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
            if self.persona_active() and self.in_reply_window():
                # an answer to her: strict commands that are not hers to decide still work without the wake word
                m, _ = match(transcript, self.intents)
                if m is not None and not self._gated(m.intent.id):
                    return self._run_match(m, now)
                if len(normalize(transcript).split()) <= int(self.persona_cfg["max_words"]):
                    return self._need_persona(transcript, m if m is not None and self._gated(m.intent.id) else None)
            return self._maybe_llm(transcript, Outcome("ignored", "no wake word"))
        if not command.strip():
            return Outcome("ignored", "wake word only")

        m, reason = match(command, self.intents)
        if m is None:
            return self._maybe_llm(transcript, Outcome("ignored", reason))
        if self._gated(m.intent.id):
            return self._need_persona(transcript, m)   # hers to decide

        return self._run_match(m, now)

    def _maybe_llm(self, transcript, fallback):
        if self._llm_candidate(transcript):
            if self.persona_active():
                return self._need_persona(transcript)   # she understands loose talk and decides
            return _NeedLlm(transcript, list(self._recent))
        return fallback

    def _run_match(self, m: Match, now: float, override=None, via="fast", extra=None):
        blocked = self._blocked_by_lock(m.intent)
        if blocked is not None:
            blocked.score, blocked.via, blocked.extra = m.score, via, extra or {}
            return blocked

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
        lube = 0
        for item in plan[:int(self.llm_cfg["max_steps"])]:
            intent = by_id.get(item.get("step"))
            if intent is None:
                continue
            if intent.id.startswith("lube_"):
                # a shot is final: at most max_lube per improvised scene, whatever the model asks for
                if lube >= int(self.llm_cfg["max_lube"]):
                    continue
                lube += 1
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
        self._track_lock(m.intent)
        if self.dry_run:
            return Outcome("dry-run", f"would run {len(steps)} step(s)", m.intent.id, m.score, via, extra or {})
        self.run_steps(steps)
        self._remember(m.intent.id if m.intent.id != "improvise" else "improvised " + "+".join((extra or {}).get("plan", [])))
        return Outcome("executed", "", m.intent.id, m.score, via, extra or {})
