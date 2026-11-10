"""Match a transcript against a closed list of command phrases.

The transcript is never executed as such: it has to be (nearly) one of the configured phrases.
Everything else - film dialogue, breathing artefacts, speech-to-text hallucinations - is ignored.
"""
import re
import unicodedata
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

DIGITS = {"1": "jeden", "2": "dwa", "3": "trzy", "4": "cztery", "5": "piec", "6": "szesc", "7": "siedem", "8": "osiem"}
TOKEN_MIN = 0.70      # every phrase word must have a transcript word at least this similar
AMBIGUITY_MARGIN = 0.05
MAX_TRANSCRIPT_WORDS = 8
# polite words around a command of 2+ words ("proszę bardzo, wróćmy do filmu") are not extra words of it; a one-word
# command must still be said alone
POLITE = {"prosze", "bardzo", "blagam", "ladnie", "grzecznie", "please", "pretty"}


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
    description: str = ""            # one sentence for the LLM that guesses unclear commands
    repeatable: bool = False         # "much faster" may repeat the steps (LLM "times")
    llm_min_confidence: float = None # None: the global llm.min_confidence
    improvise: bool = False          # the LLM may use this command as a step of an improvised plan
    phrases_by_lang: dict = field(default_factory=dict)   # {"en": [...]} - the phrases in other languages
    _tokens: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._tokens = [normalize(p).split() for p in self.phrases]

    def for_language(self, lang):
        """the same command with the phrases of that language (the default phrases when it has none)"""
        phrases = self.phrases_by_lang.get(lang)
        if phrases is None:
            return self
        return replace(self, phrases=list(phrases))


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
        if not intent.phrases:           # reachable only through the LLM (e.g. "improvise")
            continue
        plain = [t for t in tokens if t not in POLITE] or tokens
        best = max(((max(phrase_score(tokens, pt, intent.max_extra_words), phrase_score(plain, pt, intent.max_extra_words) if len(pt) > 1 else 0.0), p)
                    for pt, p in zip(intent._tokens, intent.phrases)),
                   key=lambda x: x[0])
        if best[0] >= intent.threshold:
            scored.append(Match(intent, best[0], best[1]))

    if not scored:
        return None, "no phrase close enough"
    scored.sort(key=lambda m: -m.score)
    # "max tryb a" holds the shorter command "tryb a": of two close matches the one whose phrase contains the other's
    # words (and more) wins
    if len(scored) > 1 and scored[0].score - scored[1].score < AMBIGUITY_MARGIN:
        a, b = (set(normalize(m.phrase).split()) for m in scored[:2])
        if a > b:
            scored = [scored[0]]
        elif b > a:
            scored = [scored[1]]
    if len(scored) > 1 and scored[0].score - scored[1].score < AMBIGUITY_MARGIN:
        return None, f"ambiguous: {scored[0].intent.id} / {scored[1].intent.id}"
    return scored[0], "ok"
