"""Decides what to do with a transcript: match, cool-down, optional confirmation, dry run.

Two ways to a command: the strict matcher (wake word + closed list of phrases; offline, instant) and,
when that finds nothing, an optional LLM that guesses which configured command was meant (see llm.py).
The LLM only picks one of the configured commands; the tool validates the pick and runs its steps."""
import copy
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field

from lines import similar
from matcher import Match, _sim, match, normalize, phrase_score

YES = {"tak", "potwierdzam", "dawaj", "jasne"}
NO = {"nie", "anuluj", "stop", "odwolaj"}

LLM_DEFAULTS = {"min_confidence": 0.8, "max_words": 14, "prefilter_similarity": 0.5, "prefilter_min_length": 5, "context": 3,
                "max_steps": 4, "max_faster": 3, "max_deeper": 3, "max_lube": 1}
REPLACE_WHOLE = {("narration", "pool", "seed"), ("narration", "tasks", "by_stage"), ("narration", "tasks", "care"),
                 ("vocabulary",), ("address",)}


POLISH_MARK = re.compile(r"[ąćęłńśźżó]|\b(nie|jeszcze|proszę|poproś|przeproś|dostajesz|jesteś|mała|teraz|ładniej|kim|powiedz|dziękuj)\b", re.I)
ENGLISH_MARK = re.compile(r"\b(the|you|your|not|yet|please|now|good|girl|beg|say|what|who|nicer|apologize|thank)\b", re.I)


def in_language(line, language):
    """is the line in the session's language? (a cheap guess: Polish letters / common words of each language)"""
    pl = len(POLISH_MARK.findall(line))
    en = len(ENGLISH_MARK.findall(line))
    if language == "en":
        return not (pl and pl >= en)
    return not (en >= 2 and not pl)


SIDE_WORDS = {
    "front": [" do ust", " w usta", " ust ", " usta ", " buzi", " buzię", " gardło", " gardła", " z przodu", " przód ", " przodem",
              " ssij", " ssać", " ssanie", " obciąg", " loda", " na twarz", " twarz", " język",
              " mouth", " throat", " face", " front", " suck", " blowjob", " lips"],
    "rear": [" od tyłu", " z tyłu", " tył ", " tyłek", " tyłka", " dupy", " dupę", " dupa", " dupie", " cipk", " cipę", " cipy",
             " do środka", " w środek", " w środku", " anal", " pośladk",
             " behind", " ass ", " butt", " rear", " inside", " pussy", " from the back"],
}
NO_WAKE_WORD = ("halt", "cooldown")   # said alone, at any moment: the safe word and the cooldown password
SIDED_ACTIONS = {"Speed", "Depth", "Mode", "Calm", "Boost", "LubeLauncher", "Cooldown"}
# the same words as the matcher sees them (no accents), each with a space in front: " do ust", " od tylu", ...
SIDE_NORM = {k: [" " + normalize(w) + ("" if not w.endswith(" ") else " ") for w in v] for k, v in SIDE_WORDS.items()}
SIDE_PHRASES = sorted({normalize(w) for v in SIDE_WORDS.values() for w in v if len(normalize(w).split()) >= 1
                       and normalize(w) not in ("ust", "usta", "twarz", "jezyk", "ssij", "ssac", "ssanie", "loda", "obciag", "suck",
                                                "blowjob", "lips", "face", "mouth", "throat")}, key=lambda p: -len(p))


def strip_side_words(text):
    """the command without the words that only say where ("szybciej od tyłu" -> "szybciej"), for the strict matcher"""
    low = " " + normalize(text) + " "
    for p in SIDE_PHRASES:
        low = low.replace(" " + p + " ", " ")
    return " ".join(low.split())


REPEAT_RE = re.compile(r"(?:powtórz|powiedz\s+dokładnie|powiedz\s+głośno|repeat|say\s+it)(?:[\s,]+\w+){0,3}\s*:\s*['\"„“]?(.+?)['\"”]?\s*$", re.I)


def repeat_target(line):
    """the sentence she told him to repeat ("Powtórz za mną: sama się zgłosiłam."), or None"""
    m = REPEAT_RE.search(line)
    return m.group(1).strip() if m and len(m.group(1).split()) >= 2 else None


REFUSAL_MARKERS = re.compile(r"\b(nie|nigdy|spierdalaj|spadaj|odwal|odpierdol|pierdol\w*|won|głup\w*|idiot\w*|chuj\w*|"
                             r"no|never|won't|refuse|stupid|fuck\s+off)\b", re.I)
MOOD_MINUTES = (6, 14)   # her mood shifts on its own after this many minutes (random in the range)
SAFE_WORD_COMPOUNDS = {"pętli", "petli", "sekwencji", "trybu", "blokady", "filmu", "loop", "sequence", "mode", "the"}


def deep_merge(base, overlay, path=()):
    """dicts merged key by key, everything else (lists, scalars) replaced by the overlay; the blocks in REPLACE_WHOLE
    (a language's seeds, orders, vocabulary, addresses) are taken from the overlay as they are"""
    out = dict(base)
    for k, v in (overlay or {}).items():
        here = path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict) and here not in REPLACE_WHOLE:
            out[k] = deep_merge(out[k], v, here)
        else:
            out[k] = v
    return out


PERSONA_DEFAULTS = {"enabled": True, "reply_window": 15.0, "base_p": 0.25, "step_p": 0.2, "confession_p": 0.3, "max_asks": 5,
                    "silence_reset": 180.0, "gated": [], "tease_allowed": [], "max_words": 30,
                    "never_gated": ["halt", "lock", "cooldown"], "fallback_plain": [], "base_p_overrides": {},
                    "cooldown": {"seconds": 300, "lines": [], "themes": []},
                    "stages": ["spokojnie, chłodno, lekko uwodzicielsko"],
                    "escalation": {"enabled": True, "max_stage": 4, "points": {"turn": 1, "denied": 1, "confession": 2, "granted": 2},
                                   "thresholds": [0, 5, 12, 22, 35], "heat_reset": 600, "tease_chance": [0.3, 0.5, 0.7, 0.85, 1.0]},
                    "initiative": {"enabled": True, "idle_prompt_seconds": 25, "max_nudges": 3, "session_seconds": 180},
                    "narration": {"enabled": False, "gap_seconds": [[90, 150]], "sharp_p": [0.7], "max_words": 16, "themes": [], "examples": [],
                                  "style": "", "tasks": {"enabled": False, "share": [0.0], "by_stage": {}, "care": []}},
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
                 wake_words_by_lang=None, language="pl",
                 persona=None, persona_cfg=None, speak=None, rng=None):
        self.intents = intents
        self._base_intents = list(intents)
        self.wake_words = [normalize(w) for w in (wake_words or []) if normalize(w)]
        self.wake_words_by_lang = {k: list(v) for k, v in (wake_words_by_lang or {}).items()}
        self.language = language
        self.pools = {}                   # LinePool per language (set by the main loop)
        self.on_language = None           # on_language(lang): the main loop switches the speech recognition and the voice
        self.wake_min_similarity = wake_min_similarity
        self.llm = llm
        self.llm_cfg = dict(LLM_DEFAULTS, **(llm_cfg or {}))
        self._lock = threading.RLock()   # voice and keyboard threads share the cool-downs
        self._run_raw = run_steps          # callable(list of steps)
        self.run_steps = self._run_sided  # the same, with the side of the current turn (front / rear) applied
        self.sides_enabled = True         # the machines are told apart (MFP device roles); False: every command to all
        self._turn_side = None            # "front" / "rear" for the turn being handled, None = every side
        self._last_side = None            # the side used last (when nothing says which, she goes on with it)
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
        self.pool = None                  # LinePool of the current language (see pools / set_language)
        self._said_recent = []            # her last lines (any kind), handed to the model as "do not repeat"
        # her mood now: drawn at the start, again whenever another woman takes over (new_mood from the voice rotation)
        # and now and then on its own (MOOD_MINUTES), never one of the last few - so no two evenings sound alike
        self.session_mood, self._moods_recent, self._mood_until = "", [], 0.0
        self.new_mood()
        self._tasks_used = []             # physical orders already given this session (no repeats)
        self._consumed = {}               # live mode: turn id -> (intent, text) already run from a partial transcript
        self._speculated = {}             # live mode: turn id -> partial text handed to the persona ahead of time
        self._consumed_log = []
        self._themes_recent = []          # narration themes used lately (no repeats)
        self._penalty_until = 0.0         # an open refusal costs: nothing is granted until you apologise or the time is up
        self.busy = lambda: False         # True while she is speaking / listening is muted (set by the main loop)
        self.speak_ex = None              # speak_ex(line, voice, meta): like speak, but knows the stage, tone and kind of line
        self.audio_cache = None           # AudioCache: narration lines synthesized ahead (set by the main loop)
        self.external_voice = False       # agents mode: the agent speaks her lines, the tool only keeps the books
        self.on_halt = None               # on_halt(): the safe word ran - her voice is cut off at once
        self._last_heard = 0.0            # last time anything was heard from the user (narration keeps quiet after it)
        self._repeat_target = None        # the sentence she told him to repeat ("Powtórz za mną: ...")
        self._last_demand = None          # (request id, demand) of her last refusal: what she wants before she gives it
        self._beg_met = None              # a request whose "ask nicer" demand he has just met (a bonus on the dice)
        self._cooldown_until = 0.0        # "uspokój mnie": until then she is only tender, her program waits

    @property
    def persona_cfg(self):
        return self._persona_cfg

    @persona_cfg.setter
    def persona_cfg(self, value):
        # whatever is assigned (also by tests) is completed with the defaults; the overlay of the current language
        # (persona.languages.<lang>) is merged on top, so that in English she addresses, hears and comments in English
        self._persona_base = dict(value or {})
        self._apply_language()

    def _apply_language(self):
        merged = dict(PERSONA_DEFAULTS, **self._persona_base)
        lang = getattr(self, "language", "pl")
        overlay = (self._persona_base.get("languages") or {}).get(lang)
        if overlay:
            merged = deep_merge(merged, overlay)
        self._persona_cfg = merged

    def set_language(self, lang):
        """Switch everything to that language: the command phrases, the wake words, the persona overlay (addresses,
        confessions, manners, narration, voices), the line pool, the orders given so far. -> True when it changed."""
        lang = str(lang or "pl").lower()
        if lang == getattr(self, "language", "pl") and hasattr(self, "_persona_cfg"):
            return False
        with self._lock:
            self.language = lang
            self.intents = [i.for_language(lang) for i in self._base_intents]
            if lang in self.wake_words_by_lang:
                self.wake_words = [normalize(w) for w in self.wake_words_by_lang[lang] if normalize(w)]
            self._apply_language()
            self.pool = self.pools.get(lang)
            self._tasks_used.clear()
            self._themes_recent.clear()
            self._narration_due = 0.0
        if self.on_language:
            self.on_language(lang)
        return True

    def say_now(self, line):
        """a line of hers outside any turn (a confirmation after a key): spoken, remembered, logged"""
        with self._lock:
            stage = self.stage()
            tone, _ = self._tone_now
            outcome = Outcome("talk", "key", "", 0.0, "key", {"say": line, "stage": stage, "voice": stage + (5 if tone == "ostry" else 0)})
            self._history.append(("assistant", line))
            self._window_until = self.clock() + float(self.persona_cfg["reply_window"])   # he may answer without the wake word
            self._last_line = self.clock()
            self._log("[key]", outcome)
        self._say(outcome)

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
            self._last_heard = self.clock()
            words = self.side_of(transcript)
            self._turn_side = words if words in ("front", "rear") else (None if words == "both" else self._last_side)
            self._turn_side_from_words = words is not None
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

    # ---- the live mode: partial transcripts, streamed answers -------------------------------------------
    def _line_in_language(self, outcome):
        """a replacement in the session's language: a refusal for her pending demand, else a plain line of hers"""
        m = self.persona_cfg["manners"]
        if outcome.status == "denied" or self.penalty_remaining() > 0 or self._last_demand:
            demand = (self._last_demand or (None, "beg"))[1]
            line = self._varied_denial("", demand)
            if line:
                return line
        options = list(m.get("plain_lines") or [])
        from lines import similar
        recent = [t for r, t in self._history[-12:] if r == "assistant"]
        fresh = [o for o in options if not any(similar(o, t) >= 0.75 for t in recent)] or options
        return self.rng.choice(fresh) if fresh else None

    def seed_said(self, lines):
        """her lines from earlier sessions (the log): they count as said, so a new session does not open with the same
        sentences - handed to the model as "nie_powtarzaj" and checked before she speaks"""
        for line in lines[-40:]:
            if line and line not in self._said_recent:
                self._said_recent.append(line)
        del self._said_recent[:-40]

    def _fresh_line(self, line, outcome):
        """the model copies its favourite sentence ("Dobra dziewczynka. Masz.") across turns and sessions: a grant or a
        refusal too close to one of her last lines (this session or the ones before) is swapped for a variant from
        manners.grants / manners.denials she has not said lately"""
        from lines import similar
        if not line or outcome.status not in ("executed", "dry-run", "denied") or outcome.via != "persona":
            return line
        if self.language != "pl" or outcome.intent in self.persona_cfg.get("never_gated", []):
            return line                    # the variants are Polish; fixed lines (cooldown, safe word) stay as they are
        recent = [t for r, t in self._history[-12:] if r == "assistant"] + self._said_recent[-40:]
        opening = lambda t: " ".join(normalize(t).split()[:2])
        same_start = sum(1 for t in recent[-8:] if opening(t) == opening(line))   # "Jeszcze nie, ..." again and again
        if not any(similar(line, t) >= 0.75 for t in recent) and same_start < 1:
            return line
        manners = self.persona_cfg["manners"]
        if outcome.status == "denied":
            options = [o for v in (manners.get("denials") or {}).values() for o in v]
        else:
            options = list(manners.get("grants") or [])
        fresh = [o for o in options if not any(similar(o, t) >= 0.75 for t in recent)]
        if not fresh:
            return line
        outcome.extra["varied_from"] = line
        return self.rng.choice(fresh)

    def _varied_denial(self, line, demand):
        """her refusal must not be the same sentence over and over: a line too close to one of her last ones is swapped
        for a variant from manners.denials[demand] that she has not said lately"""
        from lines import similar
        recent = [t for r, t in self._history[-12:] if r == "assistant"]
        if line and not any(similar(line, t) >= 0.75 for t in recent):
            return line
        options = list(self.persona_cfg["manners"].get("denials", {}).get(demand) or []) + \
            list(self.persona_cfg["manners"].get("denials", {}).get("beg") or [])
        fresh = [o for o in options if not any(similar(o, t) >= 0.75 for t in recent + self._said_recent[-20:])]
        if fresh:
            return self.rng.choice(fresh)
        return line or (options[0] if options else "")

    def _demand_met(self, transcript):
        """did he do what she demanded last? beg -> a polite word; thank -> thanks; confess -> a confession ("jestem ...");
        repeat -> close enough to the sentence she gave (misheard words allowed)"""
        if not self._last_demand:
            return False
        dem = self._last_demand[1]
        low = normalize(transcript)
        words = set(low.split())
        markers = {normalize(x) for x in self.persona_cfg["manners"].get("polite_markers", [])} | {"błagam", "proszę", "please", "beg"}
        if dem == "beg":
            return bool(words & markers)
        if dem == "thank":
            return any(w.startswith(("dzięk", "dziek", "thank")) for w in words)
        if dem == "confess":
            return (low.startswith(("jestem", "i am", "i m", "należę", "naleze")) or "jestem" in words
                    or self._says_any(transcript, self.persona_cfg["confession_phrases"], 0.6))
        if dem == "repeat":
            from lines import similar
            target = self._repeat_target or ""
            return bool(target) and similar(transcript, target) >= 0.55
        return False

    def mentions_safe_word(self, transcript) -> bool:
        """the safe word anywhere in the utterance (fuzzy): an utterance heard while she speaks counts only with it"""
        halt = next((i for i in self.intents if i.id == "halt"), None)
        if halt is None:
            return False
        singles = {normalize(p) for p in halt.phrases if len(normalize(p).split()) == 1}
        return any(_sim(w, t) >= 0.8 for t in normalize(transcript).split() for w in singles)

    def addressed_to_her(self, transcript) -> bool:
        """starts with the wake word ("kochanie, ...", "Pani, ..."): said to her on purpose, not her echo"""
        tokens = normalize(transcript).split()[:2]
        return any(_sim(w, t) >= self.wake_min_similarity for t in tokens for w in self.wake_words)

    def short_answer_over_her(self, transcript, her_lines, words) -> bool:
        """a short answer said while she speaks ("proszę", "błagam", "przepraszam"): it holds one of `words` and is not
        an echo of what she is saying (her recent lines) -> True"""
        tokens = normalize(transcript).split()
        answer = {normalize(w) for w in words if normalize(w)}
        if not tokens or len(tokens) > 6 or not any(t in answer for t in tokens):
            return False
        for line in her_lines:
            said = set(normalize(line).split())
            if similar(transcript, line) >= 0.6 or all(t in said for t in tokens):
                return False                   # she said these words herself: her own voice
        return True

    def _safe_word_first(self, transcript):
        """-> a Match of the safe word when the utterance starts with it (after the wake word, if any)"""
        halt = next((i for i in self.intents if i.id == "halt"), None)
        if halt is None:
            return None
        singles = {normalize(p) for p in halt.phrases if len(normalize(p).split()) == 1}
        tokens = normalize(transcript).split()
        if tokens and self.wake_words and any(_sim(w, tokens[0]) >= self.wake_min_similarity for w in self.wake_words):
            tokens = tokens[1:]
        if tokens and tokens[0] in singles:
            if len(tokens) > 1 and (tokens[1] in SAFE_WORD_COMPOUNDS or tokens[1].startswith(("uspak", "uspok"))):   # "trąbka uspakajająca"
                return None                    # "koniec pętli", "koniec sekwencji", ... are commands of their own
            return Match(halt, 1.0, tokens[0])
        return None

    def prepare(self, transcript):
        """the first half of handle(): -> ("outcome", Outcome) when it is settled (and spoken), ("persona", _NeedPersona)
        or ("llm", _NeedLlm) when a model has to answer (the live mode streams that answer)"""
        with self._lock:
            result = self._handle(transcript)
            if isinstance(result, Outcome):
                self._log(transcript, result)
                return "outcome", self._say(result)
        if isinstance(result, _NeedPersona):
            return "persona", result
        return "llm", result

    def finish_llm(self, transcript, need):
        answer = self._ask_llm(need)
        with self._lock:
            outcome = self._finish_llm(transcript, answer)
            self._log(transcript, outcome)
        return self._say(outcome)

    def finish_persona(self, transcript, answer):
        """a persona answer obtained elsewhere (e.g. speculatively) -> Outcome, spoken"""
        with self._lock:
            outcome = self._finish_persona(transcript, answer)
            self._log(transcript, outcome)
        return self._say(outcome)

    def _narration_themes(self, cfg, soft=False):
        """the themes she may comment on now (not the ones used lately); while winding down only the tender ones"""
        allowed = list(cfg["themes"])
        if soft:
            tender = [t for t in self.persona_cfg["cooldown"].get("themes", []) if t in allowed]
            allowed = tender or [t for t in allowed if t.lower().startswith(("po sesji", "aftercare"))] or allowed
        recent = self._themes_recent[-max(1, len(allowed) - 2):]
        return [t for t in allowed if t not in recent] or allowed

    def narration_line(self):
        """a narration line for an external voice (the agents mode): chosen like idle_narration, not spoken here.
        -> (line, meta) or (None, None)"""
        cfg = self.persona_cfg["narration"]
        if not cfg.get("enabled") or not cfg.get("themes"):
            return None, None
        with self._lock:
            stage = self.stage()
            soft = self.cooldown_active()
            if soft:
                self._tone_now = ("czuły", 0)
            themes = self._narration_themes(cfg, soft)
            theme = self.rng.choice(themes)
            self._themes_recent.append(theme)
            del self._themes_recent[:-len(cfg["themes"])]
            tasks = cfg.get("tasks", {})
            share = tasks.get("share", [0.0])
            line, source = None, "pool"
            if tasks.get("enabled") and not soft and self.rng.random() < float(share[max(0, min(stage, len(share) - 1))]):
                line, source = self._draw_task(stage), "task"
        if not line and self.pool is not None:
            line = self.pool.take(theme)
        if not line:
            return None, None
        with self._lock:
            self._history.append(("assistant", line))
            del self._history[:-24]
            self._last_line = self.clock()
            tone, harsh = self._tone_now
            extra = {"say": line, "stage": stage, "tone": tone, "harsh": harsh, "theme": theme, "source": source,
                     "kind": "task" if source == "task" else self._kind_of(line, {"source": source, "theme": theme})}
            self._log("[narration]", Outcome("talk", "narration (agent)", "", 0.0, "persona", extra))
            if source == "task":
                self._remember_said(line)
        return line, {"tone": extra["tone"], "kind": extra["kind"], "stage": stage, "theme": theme, "language": self.language}

    def handle_partial(self, item, text):
        """A transcript still being written (live mode). Only strict matches, no dice: the safe word runs at once, an
        ungated complete command runs at once (and is remembered, so the final transcript does not run it twice); a
        gated request is returned as a _NeedPersona for a speculative call. -> Outcome | _NeedPersona | None"""
        with self._lock:
            now = self.clock()
            if item in self._consumed:
                # "trąbka" stopped everything at once (safety first); when the words go on into the cooldown password
                # "trąbka uspakajająca", the wind-down starts from there
                if self._consumed[item][0] != "halt":
                    return None
                m = self._match_no_wake(text)
                if m is None or m.intent.id != "cooldown":
                    return None
            else:
                m = self._match_no_wake(text)
            if m is not None and (m.intent.id == "halt" or m.score >= 0.95):
                self._consumed[item] = (m.intent.id, text)
                words = self.side_of(text)
                self._turn_side = words if words in ("front", "rear") else None
                self._turn_side_from_words = words is not None
                o = self._run_match(m, now)
                self._log(f"[partial] {text}", o)
                return self._say(o)
            command = self.strip_wake_word(text)
            if command is None or not command.strip():
                return None
            m, _ = match(command, self.intents)
            if m is None:
                return None
            if not self._gated(m.intent.id):
                if m.score < 0.95 or len(normalize(command).split()) < len(normalize(m.phrase).split()):
                    return None                        # the phrase is not complete (or not certain) yet
                self._consumed[item] = (m.intent.id, text)
                o = self._run_match(m, now, via="live")
                self._log(f"[partial] {text}", o)
                return self._say(o)
            if self.persona_active() and item not in self._speculated:
                self._speculated[item] = text
                return self._need_persona(text, m)       # hers to decide: start thinking already
            return None

    def handle_final(self, item, text):
        """the completed transcript of a turn (live mode): what a partial already ran is not run again"""
        with self._lock:
            done = self._consumed.pop(item, None)
            self._speculated.pop(item, None)
            del self._consumed_log[:-50]
        if done is not None:
            m, _ = match(self.strip_wake_word(text) or text, self.intents)
            if m is None or m.intent.id != done[0]:
                m = self._match_no_wake(text) or m
            if m is not None and m.intent.id == done[0]:
                o = Outcome("done", f"already ran from the partial transcript ({done[0]})", done[0], m.score, "live")
                self._log(text, o)
                return o
        return self.handle(text)

    def speculation_fits(self, partial, final):
        """the speculative answer to a partial transcript is used when the final one says the same thing"""
        a, b = normalize(partial).split(), normalize(final).split()
        if not a or not b:
            return False
        if a == b:
            return True
        if b[:len(a)] == a and len(b) - len(a) <= 2:
            return True                                # the final only adds a word or two ("..., proszę")
        ma, _ = match(self.strip_wake_word(partial) or partial, self.intents)
        mb, _ = match(self.strip_wake_word(final) or final, self.intents)
        return ma is not None and mb is not None and ma.intent.id == mb.intent.id and b[:len(a)] == a

    def handle_streamed(self, transcript, events, speak_sentence, need=None):
        """A persona answer arriving as a stream (persona.respond_stream events). The dice are rolled and the steps
        run as soon as the head is complete; the winning line is handed out sentence by sentence through
        speak_sentence(text, meta, final) while the model still writes it. -> Outcome"""
        G, D = "\x00G", "\x00D"
        head = None
        outcome = None
        chosen = None
        buf = {"line_if_granted": "", "line_if_denied": ""}
        sent = {"line_if_granted": "", "line_if_denied": ""}
        meta = None
        try:
            for ev in events:
                if ev[0] == "head":
                    head = ev[1]
                    head.line_if_granted, head.line_if_denied = G, D
                    with self._lock:
                        outcome = self._finish_persona(transcript, head)
                    say = outcome.extra.get("say", "")
                    chosen = "line_if_granted" if say == G else "line_if_denied" if say == D else None
                    if chosen is None:
                        outcome.extra.pop("say", None)
                    meta = {"tone": outcome.extra.get("tone", self._tone_now[0]), "kind": "answer",
                            "stage": outcome.extra.get("stage", self.stage()), "language": self.language}
                    outcome.extra["streamed"] = True
                elif ev[0] == "delta":
                    buf[ev[1]] += ev[2]
                    if chosen == ev[1]:
                        pending = buf[ev[1]][len(sent[ev[1]]):]
                        from tts_ws import split_sentences
                        whole, _ = split_sentences(pending)
                        from lines import scrub as _scrub
                        for s in whole:
                            speak_sentence(_scrub(s.strip())[0], meta, False)
                            sent[ev[1]] += pending[:pending.index(s) + len(s)]
                            pending = buf[ev[1]][len(sent[ev[1]]):]
                elif ev[0] == "done":
                    turn = ev[1]
                    if chosen:
                        line = getattr(turn, chosen).strip()
                        rest = line[len(sent[chosen].strip()):].strip() if line.startswith(sent[chosen].strip()) else ""
                        from lines import scrub as _scrub
                        speak_sentence(_scrub(rest)[0], meta, True)
                        line = _scrub(line)[0]
                        with self._lock:
                            outcome.extra["say"] = line
                            outcome.extra["kind"] = self._kind_of(line, outcome.extra)
                            self._history = [(r, line if t in (G, D) else t) for r, t in self._history]
                            if self.is_repeat(line):
                                outcome.extra["repeat"] = True
                            if self.is_banned(line):
                                outcome.extra["names_gear"] = True
                            self._remember_said(line)
                    else:
                        speak_sentence("", meta or {}, True)
                    outcome.extra["persona_raw"] = turn.raw
                    outcome.extra["latency_ms"] = turn.latency_ms
        except Exception as e:
            if outcome is None:
                # nothing decided yet: the ordinary path (a full answer) takes over
                answer = self._ask_persona(need) if need is not None else (e, None)
                with self._lock:
                    outcome = self._finish_persona(transcript, answer)
                    self._log(transcript, outcome)
                return self._say(outcome)
            speak_sentence("", meta or {}, True)
            outcome.extra["stream_error"] = str(e)
            with self._lock:
                self._history = [(r, t) for r, t in self._history if t not in (G, D)]
        with self._lock:
            self._log(transcript, outcome)
        return outcome

    # ---- two machines and two launchers: front (her mouth) and rear (from behind) ----------------------
    def side_of(self, transcript):
        """the side the words point at: "do ust", "ssij", "na twarz" -> front; "od tyłu", "w dupę", "do środka" -> rear"""
        low = " " + normalize(transcript) + " "
        front = sum(1 for w in SIDE_NORM["front"] if w in low)
        rear = sum(1 for w in SIDE_NORM["rear"] if w in low)
        if front and not rear:
            return "front"
        if rear and not front:
            return "rear"
        if front and rear:
            return "both"
        return None

    def _sided(self, steps, side):
        """the steps for one side: a machine / launcher action gets the side in its name (Hismith::Front::Speed::Faster)"""
        if side not in ("front", "rear") or not self.sides_enabled:
            return steps
        tag = "Front" if side == "front" else "Rear"
        out = []
        for st in steps:
            action = st.get("action", "")
            head, sep, tail = action.partition("::Hismith::")
            if sep and tail.split("::")[0] in SIDED_ACTIONS and not tail.startswith(("Front::", "Rear::")):
                st = dict(st, action=f"{head}::Hismith::{tag}::{tail}")
            out.append(st)
        return out

    def _run_sided(self, steps):
        side = self._turn_side
        if side in ("front", "rear"):
            self._last_side = side
        return self._run_raw(self._sided(steps, side))

    def is_banned(self, line):
        """she never names the hardware: the men in VR take her (narration.pool.banned_words)"""
        from lines import is_banned_line
        return is_banned_line(line, self.persona_cfg["narration"].get("pool", {}).get("banned_words", []))

    def is_repeat(self, line):
        """True when she already said this (or a close paraphrase) - the pool's whole history, else the recent lines"""
        if self.pool is not None:
            return self.pool.is_repeat(line)
        return any(similar(line, s) >= 0.8 for s in self._said_recent)

    def _draw_task(self, stage):
        """a physical order for the stage, never the same one twice in a session (then care lines, then any)"""
        t = self.persona_cfg["narration"].get("tasks", {})
        by_stage = t.get("by_stage", {})
        pool = []
        for s in range(stage, -1, -1):
            pool += [x for x in by_stage.get(str(s), []) if x not in self._tasks_used]
            if len(pool) >= 3:
                break
        if not pool:
            pool = [x for x in t.get("care", []) if x not in self._tasks_used] or list(t.get("care", []))
        if not pool:
            return None
        task = self.rng.choice(pool)
        self._tasks_used.append(task)
        return task

    def _remember_said(self, line):
        self._said_recent.append(line)
        del self._said_recent[:-40]
        if self.pool is not None:
            self.pool.remember(line)

    def _say(self, outcome):
        line = outcome.extra.get("say")
        if line and outcome.extra.get("source") not in ("pool", "task") and not in_language(line, self.language):
            # the model slipped into the other language (it copies the Polish examples of its prompt): a line of hers in
            # the language of the session is said instead
            fixed = self._line_in_language(outcome)
            if fixed:
                outcome.extra["say"], outcome.extra["wrong_language"] = fixed, line
                self._history = [(r, fixed if t == line else t) for r, t in self._history]
                line = fixed
        if line:
            target = repeat_target(line)
            if target:
                self._repeat_target = target
        if line:
            from lines import scrub
            clean, changed = scrub(line)
            if changed:
                # the model ignored the ban: the word is swapped before she says it (and in what she remembers)
                outcome.extra["say"], outcome.extra["scrubbed"] = clean, True
                self._history = [(r, clean if t == line else t) for r, t in self._history]
                line = clean
        if line and outcome.extra.get("source") != "pool":
            if outcome.extra.get("source") != "task" and self.is_repeat(line):
                outcome.extra["repeat"] = True     # the model ignored "nie_powtarzaj"; logged, she still speaks
            if self.is_banned(line):
                outcome.extra["names_gear"] = True # the model named the hardware; logged, so the prompt can be tightened
            self._remember_said(line)      # pool lines are recorded by the pool itself
        if line and (self.speak or self.speak_ex) and not self.external_voice:
            extra = outcome.extra
            if "kind" not in extra:
                extra["kind"] = self._kind_of(line, extra)
            meta = {"tone": extra.get("tone", self._tone_now[0]), "kind": extra["kind"], "stage": extra.get("stage", self.stage()),
                    "theme": extra.get("theme", ""), "language": self.language}
            try:
                if self.speak_ex:
                    try:
                        self.speak_ex(line, extra.get("voice", extra.get("stage", 0)), meta)
                    except TypeError:
                        self.speak_ex(line, extra.get("voice", extra.get("stage", 0)))
                else:
                    self.speak(line)
            except Exception:   # a silent persona must never break the loop
                pass
        return outcome

    def _kind_of(self, line, extra):
        """what sort of line this is, for the delivery preset: an order, an affirmation, aftercare, narration, an answer"""
        low = line.lower()
        if extra.get("source") == "task":
            return "task"
        if low.startswith(("powtórz za mną", "repeat after me", "powtórz:", "repeat:")):
            return "affirmation"
        theme = str(extra.get("theme", "")).lower()
        if theme.startswith(("po sesji", "aftercare")) or extra.get("aftercare"):
            return "aftercare"
        if extra.get("source") in ("pool", "live"):
            return "narration"
        if extra.get("nudge"):
            return "nudge"
        return "answer"

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
        if self.cooldown_active():
            sharp = False                           # winding down: she is only tender
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
        if self._turn_side is None:
            self._turn_side = self._last_side or "front"            # an unasked shot: in her face, unless the scene is behind
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

    def _idle_side(self):
        self._turn_side = self._last_side

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
            if now < self._narration_due or now - self._last_line < 15.0:
                return None
            if now - self._last_heard < float(cfg.get("quiet_after_user", 20)):
                return None                       # he has just spoken: she answers first, the comment waits
            if self._owed is not None and now - self._last_line < 25.0:
                return None                       # she is waiting for what she demanded
            sharp = self.persona_cfg["narration"]["sharp_p"]
            soft = self.cooldown_active()          # winding down: only tender, aftercare themes, no orders
            tone = "ostry" if not soft and self.rng.random() < float(sharp[max(0, min(stage, len(sharp) - 1))]) else "czuły"
            harsh = (3 if stage >= 4 and self.rng.random() < 0.4 else 2) if tone == "ostry" else 0
            self._tone_now = (tone, harsh)
            self._tones.append(tone)
            del self._tones[:-12]
            themes = self._narration_themes(cfg, soft)
            theme = self.rng.choice(themes)
            self._themes_recent.append(theme)
            del self._themes_recent[:-len(cfg["themes"])]
            state = dict(self._persona_state(), etap=stage, ton=self._tone(stage), ton_tury=tone, ostrosc=harsh)
            history = list(self._history)
            self._last_line = now                 # a failed call must not be retried in a loop
            self._narration_due = now + self._draw_narration_gap(stage)
        source, left = "live", None
        tasks = cfg.get("tasks", {})
        share = tasks.get("share", [0.0])
        line = None
        if tasks.get("enabled") and not soft and self.rng.random() < float(share[max(0, min(stage, len(share) - 1))]):
            with self._lock:
                line = self._draw_task(stage)
            if line:
                source, theme = "task", "polecenie"
        if not line:
            line = self.pool.take(theme) if self.pool is not None else None
            if line:
                source, left = "pool", self.pool.left(theme)
        if not line:
            for attempt in range(2):
                try:
                    line = self.persona.narrate(state, history, theme, list(cfg.get("examples", [])), int(cfg.get("max_words", 16)),
                                                str(cfg.get("style", "")), self.persona_cfg.get("language_rule", ""))
                except Exception:
                    return None
                if line and not self.is_repeat(line) and not self.is_banned(line):
                    break
                line = None                        # a repeat / names the gear: one more try, then silence
        if not line:
            return None
        with self._lock:
            self._history.append(("assistant", line))
            del self._history[:-24]
            self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
            self._last_line = self.clock()
            extra = {"say": line, "stage": stage, "tone": tone, "harsh": harsh, "theme": theme, "source": source,
                     "voice": stage + (5 if tone == "ostry" else 0)}
            if left is not None:
                extra["pool_left"] = left
            outcome = Outcome("talk", "narration", "", 0.0, "persona", extra)
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
            if now - max(self._last_user, self._last_line, self._last_heard) < float(ini["idle_prompt_seconds"]):
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
                "blokada_5_min": self.lock_remaining() > 0, "wyciszanie": self.cooldown_active(),
                "ostatnie_komendy": list(self._recent), "program_aktywny": self.program_summary(),
                "nie_powtarzaj": list(self._said_recent[-30:]), "jezyk": self.language,
                "nastroj_teraz": self.session_mood,
                "regula_jezyka": self.persona_cfg.get("language_rule", "")}

    def _need_persona(self, transcript, plain=None):
        self._last_plain = plain
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
        if self._beg_met == request_id:
            p = min(p + float(m.get("beg_met_bonus", 0.35)), 0.95)      # he asked nicer, as she told him
            self._beg_met = None
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
        listed, self._last_plain = getattr(self, "_last_plain", None), None
        if isinstance(turn, tuple):
            error, plain = turn
            if plain is not None and plain.intent.id in self.persona_cfg["fallback_plain"]:
                # she cannot be reached: a mild command (calm, slower, pause...) must not get stuck with her
                o = self._run_match(plain, self.clock(), None, "persona", {})
                o.detail = f"persona unavailable ({error}), ran plain" + (f"; {o.detail}" if o.detail else "")
                return o
            return Outcome("ignored", f"persona unavailable ({error})", via="persona")
        # a clear command from the list ("zalej mnie" = several shots) is what he asked for, even if the model read it
        # as a neighbour (one shot); the model still decides yes / no and what she says
        if listed is not None and listed.score >= 0.95 and str(turn.request) not in ("none", "") and turn.request != listed.intent.id:
            turn.request = listed.intent.id
        extra = {"persona_raw": turn.raw, "latency_ms": turn.latency_ms}
        if not turn.addressed:
            return Outcome("ignored", "persona: not said to her", via="persona", extra=extra)

        now = self.clock()
        # recognised locally as well, so that a confession / obedience counts even if the model misses it
        if self._says_any(transcript, self.persona_cfg["confession_phrases"]):
            turn.confession = True
        if self._says_any(transcript, self.persona_cfg["obey_phrases"], 0.88):
            turn.thanked = True
        if not getattr(self, "_turn_side_from_words", False) and getattr(turn, "side", "auto") in ("front", "rear", "both"):
            self._turn_side = None if turn.side == "both" else turn.side          # she chose the side from the context
        m = self.persona_cfg["manners"]
        met = self._demand_met(transcript)
        if met:
            # he did what she asked - roughly is enough (the recognition is not exact): the ask goes on, with credit
            rid, dem = self._last_demand
            extra_met = {"demand_met": dem}
            if dem in ("thank", "confess", "repeat"):
                self._owed, turn.thanked = rid, True          # the demand done: she gives it
            elif dem == "beg":
                self._beg_met = rid
            if rid and dem == "beg" and str(turn.request) in ("none", "", "improvise"):
                turn.request = rid                            # "proszę bardzo" continues the same ask
            turn.defiance = False
            self._last_demand = None
        else:
            extra_met = {}
        if self._repeat_target:
            from lines import similar
            if similar(transcript, self._repeat_target) >= 0.6:
                # he repeated what she asked, maybe with a word misheard ("korana" for "kolana"): that is obedience
                turn.thanked, turn.defiance = True, False
                self._repeat_target = None
        # the model's "defiance" counts only when he actually said no: a misheard word is not rebellion
        defiant = (turn.defiance and bool(REFUSAL_MARKERS.search(transcript))) or self._says_any(transcript, m["refusal_phrases"], 0.92)
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
            line = self._fresh_line(line, outcome)
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
            pending = self._owed or self._last_demand or self.penalty_remaining() > 0
            line = (turn.line_if_denied or turn.line_if_granted) if pending else (turn.line_if_granted or turn.line_if_denied)
            if pending and self._last_demand:
                line = self._varied_denial(line, self._last_demand[1])
            extra.update(extra_met)
            return spoken(line, Outcome("talk", "persona", "", 0.0, "persona", extra))

        request_id = request
        if intent is not None and not self._gated(request_id):
            # not hers to decide (e.g. a mode or "slower"): runs at once, she may still comment
            o = self._run_match(Match(intent, 1.0, "[persona]"), now, None, "persona", dict(extra))
            return spoken(turn.line_if_granted, o)

        if locked and not request_id.startswith("lube_"):
            o = Outcome("locked", "held: she only talks (and the Lube Launcher works)", request_id, 0.0, "persona", extra)
            return spoken(turn.line_if_denied, o)

        extra.update(extra_met)
        granted, p, roll = self._decide(request_id, turn, self._is_polite(transcript))
        extra.update(p=round(p, 2), roll=round(roll, 2), asks=self._asks.get(request_id, 0), demand=turn.demand,
                     polite=self._is_polite(transcript))
        if self.penalty_remaining() > 0:
            extra["punished"] = round(self.penalty_remaining())
        self._last_ask = now
        asks_before = self._asks.get(request_id, 0)
        if granted:
            self._last_demand = None
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
        self._last_demand = (request_id, turn.demand if turn.demand in ("beg", "thank", "repeat", "confess") else "beg")
        turn.line_if_denied = self._varied_denial(turn.line_if_denied, self._last_demand[1])
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
            self._program = {"next_at": now, "every": 0, "until": 0, "steps": steps, "used": used, "random_modes": False, "side": self._turn_side,
                             "then": {"at": now + duration, "steps": undo_steps, "used": undo_used}, "source": intent.id if intent else "program"}
        else:
            self._program = {"next_at": now + start_after, "every": every, "until": (now + start_after + duration) if duration else 0, "side": self._turn_side,
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

    def new_mood(self, reason="start"):
        """another mood for her: a new woman took over, or the current one changed her mind after a while"""
        moods = list((self.persona_cfg or {}).get("session_moods") or [""])
        fresh = [m for m in moods if m not in self._moods_recent[-3:] and m != self.session_mood] or moods
        self.session_mood = self.rng.choice(fresh)
        self._moods_recent.append(self.session_mood)
        del self._moods_recent[:-6]
        lo, hi = MOOD_MINUTES
        self._mood_until = self.clock() + self.rng.uniform(lo, hi) * 60
        if reason != "start" and self.session_mood:
            print(f"  mood ({reason}): {self.session_mood}")

    def tick(self):
        """Called about once a second by the main loop: runs her program when its time has come. A hold ("utrzymaj to")
        makes it wait, the safe word ends it. -> True when a step ran."""
        if self.clock() >= self._mood_until:
            self.new_mood("after a while")
        with self._lock:
            pr = self._program
            if pr is None:
                return False
            now = self.clock()
            if now < pr["next_at"] or self.lock_remaining() > 0 or self.cooldown_active():
                return False
            extra = {"program": self.program_summary()}
            self._turn_side = pr.get("side")
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
                self._run_raw(intent.steps)          # a key is for every side
                self._track_lock(intent)
                self._remember(intent.id)
                outcome = Outcome("executed", "key", intent.id, 1.0, "key")
                if intent.id == "cooldown":
                    self._comment_cooldown(outcome)
            self._log(f"[key] {intent_id}", outcome)
        return self._say(outcome) if outcome.extra.get("say") else outcome

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
            return Outcome("locked", f"held for another {left:.0f}s (only the safe word 'trąbka' ends it)", intent.id)
        return None

    def _track_lock(self, intent):
        self._end_cooldown_by(intent)
        if intent.id == "lock":
            minutes = next((a for step in intent.steps for a in step.get("args", []) if isinstance(a, int)), 5)
            self._locked_until = self.clock() + max(1, minutes) * 60
        elif intent.id == "cooldown":
            self._cooldown_until = self.clock() + float(self.persona_cfg["cooldown"].get("seconds", 300))
            self._program = None              # the wind-down is the end: nothing of hers speeds it up again
        elif intent.id == "halt":
            self._locked_until = None
            self._program = None
            self._cooldown_until = 0.0
            if self.on_halt:
                try:
                    self.on_halt()
                except Exception:
                    pass

    def cooldown_active(self) -> bool:
        """True while the machines wind down after "uspokój mnie" (and nothing else has moved them since)"""
        return self.clock() < self._cooldown_until

    def _end_cooldown_by(self, intent):
        """another machine command (faster, a mode, the script...) ends the wind-down; the Lube Launcher, the hold
        and talk do not"""
        if not self._cooldown_until or intent.id in ("cooldown", "lock") or intent.id.startswith("lube_"):
            return
        if any("::Hismith::" in st.get("action", "") for st in intent.steps):
            self._cooldown_until = 0.0

    def _cooldown_line(self):
        """one of her tender lines for the start of the wind-down, not one said lately"""
        lines = list(self.persona_cfg["cooldown"].get("lines", []))
        fresh = [x for x in lines if not self.is_repeat(x)] or lines
        return self.rng.choice(fresh) if fresh else None

    def _comment_cooldown(self, outcome):
        """the wind-down is not hers to decide, but she comments it - softly (preset afterglow)"""
        if not self.persona_active() or outcome.extra.get("say"):
            return outcome
        line = self._cooldown_line()
        if not line:
            return outcome
        stage = self.stage()
        self._tone_now = ("czuły", 0)
        outcome.extra.update({"say": line, "stage": stage, "tone": "czuły", "harsh": 0, "voice": stage, "kind": "aftercare",
                              "aftercare": True})
        self._history.append(("assistant", line))
        del self._history[:-24]
        self._window_until = self.clock() + float(self.persona_cfg["reply_window"])
        self._last_line = self.clock()
        self._narration_due = self.clock() + self._draw_narration_gap(stage)
        return outcome

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

    def _match_no_wake(self, text):
        """the safe word or the cooldown password, said without the wake word ("trąbka uspakajająca od tyłu" too)"""
        m, _ = match(text, self.intents)
        if m is None and self.sides_enabled:
            bare = strip_side_words(text)
            if bare and bare != normalize(text):
                m, _ = match(bare, self.intents)
        return m if m is not None and m.intent.id in NO_WAKE_WORD else None

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

        safe = self._safe_word_first(transcript)
        if safe is not None:
            return self._run_match(safe, now)                # "trąbka ..." stops, whatever follows
        command = self.strip_wake_word(transcript)
        if command is None:
            # the safe word and the cooldown password need no wake word, ever ("trąbka", "trąbka uspakajająca")
            m = self._match_no_wake(transcript)
            if m is not None:
                return self._run_match(m, now)
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
        if m is None and self.sides_enabled:
            bare = strip_side_words(command)
            if bare and bare != normalize(command):
                m, reason = match(bare, self.intents)       # "szybciej od tyłu" is "szybciej" for the rear one
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
        if m.intent.id == "cooldown" and not getattr(self, "_turn_side_from_words", False):
            self._turn_side = None             # the password alone winds down every machine, not the side used last
        steps = self._steps_for(m.intent, override)
        self._track_lock(m.intent)
        if any("LubeLauncher" in st["action"] for st in steps):
            self._last_lube = now
        if self.dry_run:
            return Outcome("dry-run", f"would run {len(steps)} step(s)", m.intent.id, m.score, via, extra or {})
        self.run_steps(steps)
        self._remember(m.intent.id if m.intent.id != "improvise" else "improvised " + "+".join((extra or {}).get("plan", [])))
        outcome = Outcome("executed", "", m.intent.id, m.score, via, extra or {})
        return self._comment_cooldown(outcome) if m.intent.id == "cooldown" else outcome
