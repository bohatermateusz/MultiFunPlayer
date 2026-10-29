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

from matcher import Match, _sim, match, normalize, phrase_score

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
                    "initiative": {"enabled": True, "idle_prompt_seconds": 25, "max_nudges": 3, "session_seconds": 180},
                    "narration": {"enabled": False, "gap_seconds": [[90, 150]], "sharp_p": [0.7], "max_words": 16, "themes": [], "examples": []},
                    "vocabulary": {}, "inspiration": {"count": 3, "avoid_repeat": 14}, "confession_phrases": [], "obey_phrases": [],
                    "difficulty": 1.0, "step_p_overrides": {},
                    "manners": {"politeness_factor": 0.6, "polite_markers": ["proszę", "błagam", "pani", "dziękuję"], "ignore_demand_factor": 0.4,
                                "penalty_seconds": 90, "refusal_phrases": [], "apology_phrases": []},
                    "tone": {"sharp_p": [0.0, 0.08, 0.22, 0.38, 0.5], "max_sharp_run": 2, "soft_after_sharp": 0.7, "twist_p": 0.08,
                             "harsh_p": 0.3, "soft_first_turns": 2,
                             "soft_categories": ["czułość", "odpuszczenie", "przejęcie", "opieka", "własność_czuła", "pochwały"],
                             "sharp_categories": ["rozkazy", "upokorzenia", "drwiny", "wyznania", "własność"],
                             "harsh_category": "bardzo_ostre"},
                    "spontaneous": {"turn_chance": [0.0, 0.03, 0.08, 0.12, 0.18], "action_chance": [0.0, 0.05, 0.12, 0.2, 0.3],
                                    "lube_min_stage": 3, "lube_chance": 0.12, "lube_gap_seconds": 300,
                                    "lube_surprise": {"enabled": False, "chance": [0.0, 0.0, 0.06, 0.12, 0.2], "min_stage": 2,
                                                      "max_per_window": 3, "window_seconds": 1800, "hearing_boost": 1.5}},
                    "lube": {"max_shots": 5},
                    "program": {"min_every": 30, "max_every": 900, "max_start_after": 900, "max_duration": 3600, "lube_every_min": 60},
                    "last_second": {"enabled": False, "chance": 0.18, "min_stage": 1, "retry_bonus": 0.25,
                                    "applies_to": ["lube_fire", "lube_two", "lube_many"]}}


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
        self._vocab_recent = []           # inspiration phrases handed to the model lately (no repeats)
        self._tones = []                  # the tone of her recent turns: "czuły" (tender) / "ostry" (sharp)
        self._tone_now = ("czuły", 0)     # (tone, harshness 0-3) of the turn being prepared
        self._last_lube = -1e9
        self._surprises = []              # when she surprised the user with the Lube Launcher unasked
        self._teased_last = False         # the last grant was a last-second refusal (never twice in a row)
        self._retry = set()               # requests that were refused at the last second: the next ask is likelier
        self._program = None              # her running program (e.g. a random mode every 3 minutes), see tick()
        self._narration_due = 0.0         # when her next unprompted line is due (0 = not drawn yet)
        self._themes_recent = []          # narration themes used lately (no repeats)
        self._penalty_until = 0.0         # an open refusal costs: nothing is granted until you apologise or the time is up
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
                    self.speak_ex(line, outcome.extra.get("voice", outcome.extra.get("stage", 0)))
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

    def _roll_tone(self, stage):
        """The tool, not the model, decides how this turn sounds: tender (care, love, letting go) or sharp (orders,
        humiliation, vulgar). Sharp comes mostly late in the session, never more than max_sharp_run in a row, tender
        often follows it, and an occasional twist reverses the roll (a surprise). -> ("czuły"|"ostry", harshness 0-3)"""
        t = self.persona_cfg["tone"]
        sharp_p = t["sharp_p"]
        p = float(sharp_p[max(0, min(stage, len(sharp_p) - 1))])
        run = 0
        for x in reversed(self._tones):
            if x != "ostry":
                break
            run += 1
        if run >= int(t["max_sharp_run"]):
            p = 0.0
        elif run:
            p = min(p, 1.0 - float(t["soft_after_sharp"]))
        sharp = self.rng.random() < p
        if len(self._tones) < int(t["soft_first_turns"]):
            sharp = False
        if self.rng.random() < float(t["twist_p"]):
            sharp = not sharp
            if run >= int(t["max_sharp_run"]):
                sharp = False                       # a twist never breaks the run limit
        tone = "ostry" if sharp else "czuły"
        harsh = 0
        if sharp:
            if stage <= 1:
                harsh = 1
            elif stage == 2:
                harsh = self.rng.choice([1, 2])
            else:
                harsh = 2
            if stage >= 4 and self.rng.random() < float(t["harsh_p"]):
                harsh = 3
        self._tones.append(tone)
        del self._tones[:-20]
        self._tone_now = (tone, harsh)
        return self._tone_now

    def _pick(self, category, levels):
        voc = self.persona_cfg.get("vocabulary", {}).get(category, {})
        pool = [t for lvl in levels for t in voc.get(str(lvl), []) if t not in self._vocab_recent]
        if not pool:
            pool = [t for lvl in range(4, -1, -1) for t in voc.get(str(lvl), []) if t not in self._vocab_recent]
        return self.rng.choice(pool) if pool else None

    def _inspiration(self, stage, tone="czuły", harshness=0):
        """phrases for this turn: tender -> three tender ones; sharp -> two sharp ones (the harshest bank when harshness is 3)
        and one tender 'contrast'; none used in the last avoid_repeat draws"""
        cfg = self.persona_cfg
        t, ins = cfg["tone"], cfg.get("inspiration", {})
        soft = [c for c in t["soft_categories"] if c in cfg.get("vocabulary", {})]
        sharp = [c for c in t["sharp_categories"] if c in cfg.get("vocabulary", {})]
        soft_levels = [stage, stage - 1]
        sharp_levels = {1: [1, 2], 2: [2, 3], 3: [3, 4]}.get(harshness, [1, 2])
        picked = []
        if tone == "ostry" and sharp:
            cats = self.rng.sample(sharp, min(2, len(sharp)))
            for cat in cats:
                phrase = self._pick(cat, sharp_levels)
                if phrase:
                    picked.append((cat, phrase))
            if harshness >= 3 and t.get("harsh_category") in cfg.get("vocabulary", {}):
                phrase = self._pick(t["harsh_category"], [4, 3])
                if phrase:
                    picked[:1] = [(t["harsh_category"], phrase)]
            if soft:
                phrase = self._pick(self.rng.choice(soft), soft_levels)
                if phrase:
                    picked.append(("kontrast", phrase))
        else:
            for cat in self.rng.sample(soft, min(int(ins.get("count", 3)), len(soft))):
                phrase = self._pick(cat, soft_levels)
                if phrase:
                    picked.append((cat, phrase))
        self._vocab_recent.extend(text for _, text in picked)
        del self._vocab_recent[:-int(ins.get("avoid_repeat", 14))]
        return [{"rodzaj": cat, "fraza": text} for cat, text in picked]

    def _says_any(self, transcript, phrases, threshold=0.78):
        tokens = normalize(transcript).split()
        if not tokens:
            return False
        for phrase in phrases:
            pt = normalize(phrase).split()
            if pt and any(phrase_score(tokens[i:], pt, 6) >= threshold for i in (0, 1)):
                return True        # i=1: the wake word in front must not hide a one-word phrase
        return False

    def _tone(self, stage):
        stages = self.persona_cfg["stages"]
        return stages[max(0, min(stage, len(stages) - 1))] if stages else ""

    def _tease_roll(self, stage):
        chances = self.persona_cfg["escalation"].get("tease_chance", [1.0])
        return self.rng.random() < float(chances[max(0, min(stage, len(chances) - 1))])

    # ---- initiative: she speaks when the user is silent ----------------------------------------------------
    def _spontaneous(self, plan, stage, extra, kind):
        """Her own, unrequested move (a surprise). The plan is bounded like every scene; the Lube Launcher only from
        lube_min_stage, only with lube_chance, and not again within lube_gap_seconds of the last shot; in a hold only the
        Lube Launcher (under the same dice). -> True when something ran."""
        sp = self.persona_cfg["spontaneous"]
        now = self.clock()
        allowed = {i.id for i in self.intents if i.improvise}
        lube_ok = (stage >= int(sp["lube_min_stage"]) and self.rng.random() < float(sp["lube_chance"])
                   and now - self._last_lube >= float(sp["lube_gap_seconds"]))
        if not lube_ok:
            allowed.discard("lube_fire")
        if self.lock_remaining() > 0:
            allowed &= {"lube_fire"}
        steps, used = self._persona_steps(plan, allowed)
        if not steps:
            return False
        extra["unsolicited"] = kind
        extra["plan"] = used
        if not self.dry_run:
            self.run_steps(steps)
        if any("LubeLauncher" in st["action"] for st in steps):
            self._last_lube = now
        return True

    def _lube_surprise_roll(self, stage, boost=1.0):
        """her unasked Lube Launcher shot: own dice, own limits (stage, gap since the last shot, a few per window, not while
        punished). -> True when she does it now."""
        cfg = self.persona_cfg["spontaneous"]["lube_surprise"]
        if not cfg.get("enabled") or stage < int(cfg["min_stage"]) or self.penalty_remaining() > 0:
            return False
        now = self.clock()
        self._surprises = [t for t in self._surprises if now - t < float(cfg["window_seconds"])]
        if len(self._surprises) >= int(cfg["max_per_window"]) or now - self._last_lube < float(self.persona_cfg["spontaneous"]["lube_gap_seconds"]):
            return False
        chances = cfg["chance"]
        p = min(float(chances[max(0, min(stage, len(chances) - 1))]) * boost, 0.95)
        return self.rng.random() < p

    def _fire_lube_surprise(self, extra):
        intent = next((i for i in self.intents if i.id == "lube_fire"), None)
        if intent is None:
            return False
        now = self.clock()
        extra["unsolicited"] = "lube_surprise"
        extra["plan"] = ["lube_fire"]
        if not self.dry_run:
            self.run_steps(copy.deepcopy(intent.steps))
        self._last_lube = now
        self._surprises.append(now)
        return True

    def _session_on(self, now):
        ini = self.persona_cfg["initiative"]
        return bool(self._last_user) and now - self._last_user <= float(ini["session_seconds"]) + float(ini["idle_prompt_seconds"]) * (int(ini["max_nudges"]) + 1)

    def _draw_narration_gap(self, stage):
        gaps = self.persona_cfg["narration"]["gap_seconds"]
        lo, hi = gaps[max(0, min(stage, len(gaps) - 1))]
        return self.rng.uniform(float(lo), float(hi))

    def idle_narration(self):
        """Called about once a second by the main loop. While a session is on she comments at random moments, whether
        the user talks or not: explicit, degrading, on a rotating theme; the gap is drawn from narration.gap_seconds
        by stage. Never while she is already speaking. -> the line, or None."""
        cfg = self.persona_cfg["narration"]
        if not self.persona_active() or not cfg.get("enabled") or self.busy() or not cfg.get("themes"):
            return None
        now = self.clock()
        with self._lock:
            if not self._session_on(now):
                self._narration_due = 0.0
                return None
            stage = self.stage()
            if not self._narration_due:
                self._narration_due = now + self._draw_narration_gap(stage)
                return None
            if now < self._narration_due or now - self._last_line < 8.0:
                return None
            sharp = self.persona_cfg["narration"]["sharp_p"]
            tone = "ostry" if self.rng.random() < float(sharp[max(0, min(stage, len(sharp) - 1))]) else "czuły"
            harsh = (3 if stage >= 4 and self.rng.random() < 0.4 else 2) if tone == "ostry" else 0
            self._tone_now = (tone, harsh)
            self._tones.append(tone)
            del self._tones[:-12]
            themes = [t for t in cfg["themes"] if t not in self._themes_recent[-max(1, len(cfg["themes"]) - 2):]] or list(cfg["themes"])
            theme = self.rng.choice(themes)
            self._themes_recent.append(theme)
            del self._themes_recent[:-len(cfg["themes"])]
            state = dict(self._persona_state(), etap=stage, ton=self._tone(stage), ton_tury=tone, ostrosc=harsh)
            history = list(self._history)
            self._last_line = now                 # a failed call must not be retried in a loop
            self._narration_due = now + self._draw_narration_gap(stage)
        try:
            line = self.persona.narrate(state, history, theme, list(cfg.get("examples", [])), int(cfg.get("max_words", 16)))
        except Exception:
            return None
        if not line:
            return None
        with self._lock:
            self._history.append(("assistant", line))
            del self._history[:-24]
            self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
            self._last_line = self.clock()
            outcome = Outcome("talk", "narration", "", 0.0, "persona", {"say": line, "stage": stage, "tone": tone, "harsh": harsh,
                                                                        "theme": theme, "voice": stage + (5 if tone == "ostry" else 0)})
            self._log("[narration]", outcome)
        self._say(outcome)
        return line

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
            sp = self.persona_cfg["spontaneous"]["action_chance"]
            act = self.rng.random() < float(sp[max(0, min(stage, len(sp) - 1))])
            step_ids = [i.id for i in self.intents if i.improvise] if act else None
            surprise = self._lube_surprise_roll(stage, float(self.persona_cfg["spontaneous"]["lube_surprise"]["hearing_boost"]))
            if surprise:
                state["niespodzianka"] = "lube"      # she is about to shoot unasked: the line should fit
            self._last_line = now                # a failed call must not be retried in a loop
        try:
            result = self.persona.nudge(state, history, step_ids) if step_ids else self.persona.nudge(state, history)
        except Exception:
            return None
        line, plan = result if isinstance(result, tuple) else (result, [])
        if not line:
            return None
        with self._lock:
            self._nudges += 1
            tone, harsh = self._tone_now
            self._history.append(("assistant", line))
            self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
            self._last_line = self.clock()
            outcome = Outcome("talk", "nudge", "", 0.0, "persona", {"say": line, "stage": stage, "nudge": self._nudges, "tone": tone,
                                                                    "harsh": harsh, "voice": stage + (5 if tone == "ostry" else 0)})
            if surprise:
                self._fire_lube_surprise(outcome.extra)
            elif act and plan:
                self._spontaneous(plan, stage, outcome.extra, "nudge")
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
        tone, harsh = self._roll_tone(stage)
        address = self.persona_cfg.get("address", {})
        return {"etap": stage, "ton": self._tone(stage), "ton_tury": tone, "ostrosc": harsh,
                "inspiracja": self._inspiration(stage, tone, harsh),
                "zwroty_do_ciebie": list(address.get("titles", [])),
                "zwroty_do_niej": list(address.get("names", [])), "prosby_bez_zgody": dict(self._asks),
                "nalezy_sie_po_wykonaniu_zadania": self._owed, "kara_sekundy": round(self.penalty_remaining()),
                "blokada_5_min": self.lock_remaining() > 0,
                "ostatnie_komendy": list(self._recent), "program_aktywny": self.program_summary()}

    def _need_persona(self, transcript, plain=None):
        return _NeedPersona(transcript, self._persona_state(), list(self._history), plain)

    def _ask_persona(self, need: _NeedPersona):
        try:
            step_ids = [i.id for i in self.intents if i.improvise]
            return self.persona.respond(need.transcript, self.intents, step_ids, need.state, need.history)
        except Exception as e:
            return (e, need.plain)

    def _decide(self, request_id, turn, polite=True):
        """the honest dice: -> (granted, p, roll). Harder than a coin: a low first chance that grows slowly with every
        refusal (and with a confession), scaled by persona.difficulty; only at max_asks asks it is (almost) sure."""
        c = self.persona_cfg
        asks = self._asks.get(request_id, 0)
        if self._owed == request_id and turn.thanked:
            return True, 1.0, 0.0
        base = float(c["base_p_overrides"].get(request_id, c["base_p"]))
        step = float(c["step_p_overrides"].get(request_id, c["step_p"]))
        m = c["manners"]
        p = base + step * asks + (float(c["confession_p"]) if turn.confession else 0.0)
        p = min(p * float(c.get("difficulty", 1.0)), 0.95)
        if not polite:
            p *= float(m["politeness_factor"])              # being polite pays
        if self._owed is not None and not turn.thanked:
            p *= float(m["ignore_demand_factor"])           # ignoring what she demanded costs
        elif asks + 1 >= int(c["max_asks"]):
            p = max(p, 0.95)
        if request_id in self._retry:
            p = min(p + float(c["last_second"]["retry_bonus"]), 0.95)   # she held it back at the last second: now more likely
        if self.penalty_remaining() > 0:
            p = 0.0                                          # punished: nothing until you apologise
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
        # recognised locally as well, so that a confession / obedience counts even if the model misses it
        if self._says_any(transcript, self.persona_cfg["confession_phrases"]):
            turn.confession = True
        if self._says_any(transcript, self.persona_cfg["obey_phrases"], 0.88):
            turn.thanked = True
        m = self.persona_cfg["manners"]
        defiant = turn.defiance or self._says_any(transcript, m["refusal_phrases"], 0.92)
        if not defiant and self.penalty_remaining() > 0 and self._says_any(transcript, m["apology_phrases"], 0.8):
            self._penalty_until = 0.0                        # an apology ends the punishment
            self._heat_up("turn")
        if defiant:
            self._penalty_until = now + float(m["penalty_seconds"])
            self._asks.clear()                               # an open refusal wipes the progress of the asks
            self._owed = None
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
                tone, harsh = self._tone_now
                outcome.extra["say"] = line
                outcome.extra["stage"] = stage
                outcome.extra["tone"] = tone
                outcome.extra["harsh"] = harsh
                outcome.extra["voice"] = stage + (5 if tone == "ostry" else 0)
                self._history.append(("assistant", line))
                self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
                self._last_line = self.clock()
            return outcome

        request = turn.request
        intent = next((i for i in self.intents if i.id == request), None)
        locked = self.lock_remaining() > 0

        if turn.program and turn.program.get("cancel"):
            # "przestań zmieniać": calling her program off is his to say, no dice
            extra["program"] = "cancelled" if self._program else "none"
            self._program = None
            return spoken(turn.line_if_granted or turn.line_if_denied, Outcome("talk", "persona: program off", "", 0.0, "persona", extra))

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
            boost = float(self.persona_cfg["spontaneous"]["lube_surprise"]["hearing_boost"]) if (turn.confession or turn.thanked) else 1.0
            if self._lube_surprise_roll(stage, boost):
                self._fire_lube_surprise(extra)
                return spoken(turn.line_if_granted, Outcome("talk", "persona (unsolicited)", "", 0.0, "persona", extra))
            chances = self.persona_cfg["spontaneous"]["turn_chance"]
            if self.rng.random() < float(chances[max(0, min(stage, len(chances) - 1))]):
                reward = turn.confession or self.rng.random() < 0.5
                if self._spontaneous(turn.grant_actions if reward else turn.tease_actions, stage, extra, "reward" if reward else "tease"):
                    return spoken(turn.line_if_granted if reward else turn.line_if_denied, Outcome("talk", "persona (unsolicited)", "", 0.0, "persona", extra))
            return spoken(turn.line_if_denied or turn.line_if_granted, Outcome("talk", "persona", "", 0.0, "persona", extra))

        request_id = request
        if intent is not None and not self._gated(request_id):
            # not hers to decide (e.g. a mode or "slower"): runs at once, she may still comment
            o = self._run_match(Match(intent, 1.0, "[persona]"), now, None, "persona", dict(extra))
            return spoken(turn.line_if_granted, o)

        if locked and not request_id.startswith("lube_"):
            o = Outcome("locked", "held: she only talks (and the Lube Launcher works)", request_id, 0.0, "persona", extra)
            return spoken(turn.line_if_denied, o)

        granted, p, roll = self._decide(request_id, turn, self._is_polite(transcript))
        extra.update(p=round(p, 2), roll=round(roll, 2), asks=self._asks.get(request_id, 0), demand=turn.demand,
                     polite=self._is_polite(transcript))
        if self.penalty_remaining() > 0:
            extra["punished"] = round(self.penalty_remaining())
        self._last_ask = now
        asks_before = self._asks.get(request_id, 0)
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
            ls = self.persona_cfg["last_second"]
            if (ls.get("enabled") and request_id in ls["applies_to"] and not locked and stage >= int(ls["min_stage"])
                    and not self._teased_last and asks_before + 1 < int(self.persona_cfg["max_asks"]) and self.rng.random() < float(ls["chance"])):
                # held back at the last second: nothing is shot, no punishment, the next ask is likelier
                self._asks[request_id] = max(0, asks_before - 1)
                self._retry.add(request_id)
                self._teased_last = True
                extra["last_second"] = True
                return spoken(turn.line_if_denied or turn.line_if_granted, Outcome("denied", "persona: last second", request_id, 0.0, "persona", extra))
            self._teased_last = False
            self._retry.discard(request_id)
            if turn.program and not locked:
                # a wish with timing ("co 3 minuty", "za 5 minut"): her program, run by tick(); the first step now when due
                self._install_program(intent, turn, extra)
                if self._program and self._program["next_at"] <= now:
                    self._run_program_step(now, extra)
                return spoken(turn.line_if_granted, Outcome("executed", "persona: program", request_id, 0.0, "persona", extra))
            intent, override = self._lube_count(intent, turn.shots, extra)
            return spoken(turn.line_if_granted, self._grant(intent, turn.grant_actions, extra, now, "granted", override))

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

    def _lube_count(self, intent, shots, extra):
        """"zalej mnie" means several shots: the model says how many (shots), capped by persona.lube.max_shots; two or
        more go through lube_many's FireMany with that count. -> (intent, override steps or None)"""
        if not intent.id.startswith("lube_") or not shots:
            return intent, None
        n = max(1, min(int(shots), int(self.persona_cfg["lube"]["max_shots"])))
        extra["shots"] = n
        if n == 1:
            one = next((i for i in self.intents if i.id == "lube_fire"), intent)
            return one, None
        many = next((i for i in self.intents if i.id == "lube_many"), None)
        if many is None:
            return intent, None
        return many, {"count": n, "times": 1}

    # ---- her program: a wish with timing, run by tick() ----------------------------------------------
    def _program_allowed(self):
        return {i.id for i in self.intents if i.improvise or i.id.startswith("mode_")} - {"halt", "calm", "lock"}

    def _install_program(self, intent, turn, extra):
        """the model's program (clamped to persona.program) becomes her running program; a new one replaces the old"""
        cfg = self.persona_cfg["program"]
        pr = turn.program
        now = self.clock()
        every = int(pr.get("every_seconds", 0) or 0)
        if every:
            every = max(int(cfg["min_every"]), min(every, int(cfg["max_every"])))
        start_after = max(0, min(int(pr.get("start_after_seconds", 0) or 0), int(cfg["max_start_after"])))
        duration = max(0, min(int(pr.get("duration_seconds", 0) or 0), int(cfg["max_duration"])))
        random_modes = bool(pr.get("random_modes"))
        plan = list(pr.get("plan") or [])
        if not plan and not random_modes and intent is not None:
            plan = [{"step": intent.id, "times": 1}]
        steps, used = self._persona_steps(plan, self._program_allowed()) if plan else ([], [])
        if random_modes:
            rnd = next((i for i in self.intents if i.id == "mode_random"), None)
            if rnd is not None:
                steps, used = copy.deepcopy(rnd.steps) + steps, ["mode_random"] + used
        if not steps:
            self._program = None
            extra["program"] = "empty"
            return
        if duration and not every and not start_after:
            # "na minutę szybciej": the step now, and the opposite (slower) when the time is up
            back = {"faster": "slower", "slower": "faster", "deeper": "shallower", "shallower": "deeper"}
            undo = [{"step": back[u.split("x")[0]], "times": int(u.split("x")[1]) if "x" in u else 1} for u in used if u.split("x")[0] in back]
            undo_steps, undo_used = self._persona_steps(undo, self._program_allowed()) if undo else ([], [])
            self._program = {"next_at": now, "every": 0, "until": 0, "steps": steps, "used": used, "random_modes": False,
                             "then": {"at": now + duration, "steps": undo_steps, "used": undo_used}, "source": intent.id if intent else "program"}
        else:
            self._program = {"next_at": now + start_after, "every": every, "until": (now + start_after + duration) if duration else 0,
                             "steps": steps, "used": used, "random_modes": random_modes, "then": None,
                             "source": intent.id if intent else "program"}
        extra["program"] = self.program_summary()

    def program_summary(self):
        pr = self._program
        if not pr:
            return None
        now = self.clock()
        what = "losowy tryb" if pr["random_modes"] else "+".join(pr["used"])
        if pr["every"]:
            text = f"{what} co {pr['every']} s"
        elif pr.get("then") and pr["then"]["steps"]:
            text = f"{what}, potem {'+'.join(pr['then']['used'])} za {max(0, round(pr['then']['at'] - now))} s"
        else:
            text = f"{what} za {max(0, round(pr['next_at'] - now))} s"
        if pr["until"]:
            text += f", jeszcze {max(0, round((pr['until'] - now) / 60))} min"
        return text

    def _run_program_step(self, now, extra=None):
        """one firing of her program (under the lock): the steps, with the Lube Launcher at most once and not more often
        than lube_every_min; then the next time, or the end"""
        pr = self._program
        if pr is None:
            return False
        steps = copy.deepcopy(pr["steps"])
        lube_gap = float(self.persona_cfg["program"]["lube_every_min"])
        kept, lube = [], 0
        for st in steps:
            if "LubeLauncher" in st["action"]:
                if lube or now - self._last_lube < lube_gap:
                    continue
                lube += 1
                if st["action"].endswith("FireMany"):
                    st["args"] = [1]
            kept.append(st)
        if kept and not self.dry_run:
            self.run_steps(kept)
        if lube:
            self._last_lube = now
        if extra is not None:
            extra["program_step"] = list(pr["used"])
        if pr["every"]:
            pr["next_at"] = now + pr["every"]
            if pr["until"] and pr["next_at"] > pr["until"]:
                self._program = None
        elif pr.get("then") and pr["then"]["steps"]:
            pr["steps"], pr["used"], pr["next_at"], pr["then"] = pr["then"]["steps"], pr["then"]["used"], pr["then"]["at"], None
        else:
            self._program = None
        return bool(kept)

    def tick(self):
        """Called about once a second by the main loop: runs her program when its time has come. A hold ("utrzymaj to")
        makes it wait, the safe word ends it. -> True when a step ran."""
        with self._lock:
            pr = self._program
            if pr is None:
                return False
            now = self.clock()
            if now < pr["next_at"] or self.lock_remaining() > 0:
                return False
            extra = {"program": self.program_summary()}
            ran = self._run_program_step(now, extra)
            self._log("[program]", Outcome("executed" if ran else "talk", "program step", pr["source"], 0.0, "program", extra))
            return ran

    def _grant(self, intent, actions, extra, now, how, override=None):
        """run the request itself (its own steps), plus what she added, bounded like a scene"""
        steps = self._steps_for(intent, override) if override else copy.deepcopy(intent.steps)
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

    def penalty_remaining(self) -> float:
        return max(0.0, self._penalty_until - self.clock())

    def _is_polite(self, transcript) -> bool:
        markers = {normalize(m) for m in self.persona_cfg["manners"]["polite_markers"]}
        return bool(markers & set(normalize(transcript).split()))

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
            self._program = None

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
        if any("LubeLauncher" in st["action"] for st in steps):
            self._last_lube = now
        if self.dry_run:
            return Outcome("dry-run", f"would run {len(steps)} step(s)", m.intent.id, m.score, via, extra or {})
        self.run_steps(steps)
        self._remember(m.intent.id if m.intent.id != "improvise" else "improvised " + "+".join((extra or {}).get("plan", [])))
        return Outcome("executed", "", m.intent.id, m.score, via, extra or {})
