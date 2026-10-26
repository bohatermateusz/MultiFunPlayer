"""Match a transcript against a closed list of command phrases.

The transcript is never executed as such: it has to be (nearly) one of the configured phrases.
Everything else - film dialogue, breathing artefacts, speech-to-text hallucinations - is ignored.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DIGITS = {"1": "jeden", "2": "dwa", "3": "trzy", "4": "cztery", "5": "piec", "6": "szesc", "7": "siedem", "8": "osiem"}
TOKEN_MIN = 0.70      # every phrase word must have a transcript word at least this similar
AMBIGUITY_MARGIN = 0.05
MAX_TRANSCRIPT_WORDS = 8


def normalize(text: str) -> str:
    """lower case, no accents (incl. the Polish l-stroke), digits 1-8 as words, only letters and single spaces"""
    text = text.lower().replace("ł", "l")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(DIGITS.get(t, t) for t in text.split())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _collapse_repeats(tokens: list) -> list:
    """'stop stop' -> 'stop' (speech-to-text often repeats a word that was said once with emphasis)"""
    return [t for i, t in enumerate(tokens) if i == 0 or t != tokens[i - 1]]


def phrase_score(transcript_tokens: list, phrase_tokens: list, max_extra: int) -> float:
    """0..1; 0 when a phrase word is missing or there are too many other words"""
    if not phrase_tokens or not transcript_tokens:
        return 0.0
    transcript_tokens = _collapse_repeats(transcript_tokens)
    if len(phrase_tokens) == 1:
        max_extra = 0   # a one-word command must be said alone, otherwise it also fires inside longer phrases
    used, sims = set(), []
    for pt in phrase_tokens:
        best, best_j = 0.0, None
        for j, tt in enumerate(transcript_tokens):
            if j in used:
                continue
            s = _sim(pt, tt)
            if s > best:
                best, best_j = s, j
        if best_j is None or best < TOKEN_MIN:
            return 0.0
        used.add(best_j)
        sims.append(best)
    extras = len(transcript_tokens) - len(used)
    if extras > max_extra:
        return 0.0
    return max(0.0, sum(sims) / len(phrase_tokens) - 0.04 * extras)


@dataclass
class Intent:
    id: str
    phrases: list
    steps: list                      # [{"action": str, "args": list}]
    threshold: float = 0.88
    cooldown: float = 2.0
    max_extra_words: int = 2
    confirm: bool = False
    _tokens: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._tokens = [normalize(p).split() for p in self.phrases]


@dataclass
class Match:
    intent: Intent
    score: float
    phrase: str


def match(transcript: str, intents: list):
    """-> (Match | None, reason). reason explains why nothing matched."""
    tokens = normalize(transcript).split()
    if not tokens:
        return None, "empty"
    if len(tokens) > MAX_TRANSCRIPT_WORDS:
        return None, "too long for a command"

    scored = []
    for intent in intents:
        best = max(((phrase_score(tokens, pt, intent.max_extra_words), p) for pt, p in zip(intent._tokens, intent.phrases)),
                   key=lambda x: x[0])
        if best[0] >= intent.threshold:
            scored.append(Match(intent, best[0], best[1]))

    if not scored:
        return None, "no phrase close enough"
    scored.sort(key=lambda m: -m.score)
    if len(scored) > 1 and scored[0].score - scored[1].score < AMBIGUITY_MARGIN:
        return None, f"ambiguous: {scored[0].intent.id} / {scored[1].intent.id}"
    return scored[0], "ok"
