"""A pool of fresh lines for her narration, so that nothing she says repeats.

LinePool keeps, per theme, lines generated ahead of time (lines_pool.json) and everything that was ever spoken
(said.jsonl). When a theme runs low, one background request asks the model for a batch of new lines, handing it the
last spoken ones as "do not repeat"; the answer is filtered against the whole history (exact and near duplicates,
length, questions). take(theme) is instant and never returns a line that was said before."""
import difflib
import json
import os
import re
import threading
import time


def _norm(text):
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def similar(a, b):
    return difflib.SequenceMatcher(None, " ".join(_norm(a)), " ".join(_norm(b))).ratio()


SCRUB = [
    # (pattern, replacement) - the forms of "suka" / "suczka" / "bitch" swapped for words she is allowed to use
    (r"\bsucz?k?(?:o|uniu)\b", "dziewczynko"), (r"\bsuczko\b", "dziewczynko"), (r"\bsuko\b", "dziewczynko"),
    (r"\bsucz?k(?:ą)\b", "dziwką"), (r"\bsuczką\b", "dziwką"), (r"\bsuką\b", "dziwką"),
    (r"\bsuczkę\b", "dziwkę"), (r"\bsukę\b", "dziwkę"),
    (r"\bsuczki\b", "dziwki"), (r"\bsuki\b", "dziwki"),
    (r"\bsuczka\b", "dziwka"), (r"\bsuka\b", "dziwka"),
    (r"\bbitches\b", "sluts"), (r"\bbitchy\b", "slutty"), (r"\bbitch\b", "slut"),
]


def scrub(line):
    """the line with the forbidden words replaced (keeps the case of the first letter) -> (line, changed)"""
    out = line
    for pat, rep in SCRUB:
        out = re.sub(pat, lambda m: rep[0].upper() + rep[1:] if m.group(0)[:1].isupper() else rep, out, flags=re.I)
    return out, out != line


def is_banned_line(line, banned_words):
    low = " " + line.lower() + " "
    for w in banned_words or []:
        w = str(w)
        if w.startswith("re:"):
            if re.search(w[3:], low, re.I):
                return True
        elif w.lower() in low:
            return True
    return False


class LinePool:
    def __init__(self, generate, pool_path=None, said_path=None, min_pool=8, batch=20, max_said=2000, similarity=0.8,
                 max_words=16, avoid_recent=40, clock=time.time, background=True, seed=None, banned_words=None):
        self.generate = generate              # generate(theme, avoid: [str], count) -> [str]
        self.pool_path, self.said_path = pool_path, said_path
        self.min_pool, self.batch, self.max_said = int(min_pool), int(batch), int(max_said)
        self.similarity, self.max_words, self.avoid_recent = float(similarity), int(max_words), int(avoid_recent)
        self.clock, self.background = clock, background
        self.banned_words = [str(w) for w in (banned_words or [])]
        self._lock = threading.Lock()
        self._filling = set()
        self.pool = {}                        # theme -> [lines]
        self.said = []                        # every line ever spoken, oldest first
        self.last_error = None
        self._load()
        if not self.pool and seed:
            # the first run: start from the hand-written lines (nothing said before them is possible)
            with self._lock:
                for theme, lines in seed.items():
                    self.pool[theme] = self._fresh(theme, [str(x) for x in lines])
                self._save_pool()

    # ---- persistence -----------------------------------------------------------------------------
    def _load(self):
        if self.pool_path and os.path.exists(self.pool_path):
            try:
                with open(self.pool_path, encoding="utf-8") as f:
                    data = json.load(f)
                self.pool = {k: [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}
            except (OSError, json.JSONDecodeError, AttributeError):
                self.pool = {}
        if self.said_path and os.path.exists(self.said_path):
            try:
                with open(self.said_path, encoding="utf-8") as f:
                    for row in f:
                        try:
                            self.said.append(str(json.loads(row)["line"]))
                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue
            except OSError:
                pass
            del self.said[:-self.max_said]

    def _save_pool(self):
        if not self.pool_path:
            return
        tmp = self.pool_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.pool, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.pool_path)

    # ---- filtering -------------------------------------------------------------------------------
    def _acceptable(self, line, seen):
        line = " ".join(line.split())
        if not line or "?" in line or len(line.split()) > self.max_words or self.banned(line):
            return None
        if any(similar(line, s) >= self.similarity for s in seen):
            return None
        return line

    def _fresh(self, theme, candidates):
        seen = list(self.said[-self.max_said:]) + [x for v in self.pool.values() for x in v]
        out = []
        for c in candidates:
            line = self._acceptable(str(c), seen)
            if line:
                out.append(line)
                seen.append(line)
        return out

    # ---- the pool --------------------------------------------------------------------------------
    def left(self, theme):
        with self._lock:
            return len(self.pool.get(theme, []))

    def take(self, theme):
        """-> a fresh line for the theme (removed from the pool and remembered as said), or None"""
        with self._lock:
            lines = self.pool.get(theme, [])
            while lines:
                line = lines.pop(0)
                if not any(similar(line, s) >= self.similarity for s in self.said[-200:]):
                    self._record(line)
                    self._save_pool()
                    low = len(lines) < self.min_pool
                    break
            else:
                self._save_pool()
                line, low = None, True
        if low:
            self.refill(theme)
        return line

    def remember(self, line):
        """a line spoken outside the pool (an answer, a nudge, a live narration) must not come back either"""
        with self._lock:
            self._record(line)

    def _record(self, line):
        self.said.append(line)
        del self.said[:-self.max_said]
        if self.said_path:
            try:
                with open(self.said_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "line": line}, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def banned(self, line):
        """True when the line uses a banned word: a plain entry is a substring, an "re:" entry a regular expression"""
        return is_banned_line(line, self.banned_words)

    def is_repeat(self, line, window=400):
        """True when the line (or a close paraphrase) was said before"""
        with self._lock:
            return any(similar(line, s) >= self.similarity for s in self.said[-window:])

    def recent(self, n=20):
        with self._lock:
            return list(self.said[-n:])

    def refill(self, theme, wait=False):
        """asks the model for a batch of new lines for the theme (in the background unless wait); no-op while a fill
        for that theme is running"""
        with self._lock:
            if theme in self._filling:
                return False
            self._filling.add(theme)
        if self.background and not wait:
            threading.Thread(target=self._fill, args=(theme,), daemon=True).start()
        else:
            self._fill(theme)
        return True

    def _fill(self, theme):
        try:
            avoid = self.recent(self.avoid_recent)
            try:
                candidates = list(self.generate(theme, avoid, self.batch))
                self.last_error = None
            except Exception as e:                   # the pool must never break the session
                self.last_error = str(e)
                return
            with self._lock:
                fresh = self._fresh(theme, candidates)
                self.pool.setdefault(theme, []).extend(fresh)
                self._save_pool()
        finally:
            with self._lock:
                self._filling.discard(theme)

    def warm_up(self, themes):
        for t in themes:
            if self.left(t) < self.min_pool:
                self.refill(t)


class AudioCache:
    """Narration lines synthesized ahead of time (the expressive, slower model can be used: latency does not matter),
    kept as PCM files under folder; get(line) hands the audio over once and forgets it. warm(pool, themes) fills the
    cache for the next lines of each theme in a background thread, one request at a time."""

    def __init__(self, folder, synth, meta_for=None, prefetch=6, max_bytes=200 * 1024 * 1024, background=True):
        self.folder, self.synth, self.meta_for = folder, synth, meta_for or (lambda line, theme: {})
        self.prefetch, self.max_bytes, self.background = int(prefetch), int(max_bytes), background
        self._lock = threading.Lock()
        self._busy = False
        self.failures = 0
        self.last_error = None
        os.makedirs(folder, exist_ok=True)

    def _path(self, line):
        import hashlib
        return os.path.join(self.folder, hashlib.sha1(line.encode("utf-8")).hexdigest() + ".pcm")

    def has(self, line):
        return os.path.exists(self._path(line))

    def get(self, line):
        """-> the PCM bytes, or None; the file is removed (a line is said once)"""
        path = self._path(line)
        try:
            with open(path, "rb") as f:
                data = f.read()
            os.remove(path)
            return data if len(data) > 1 else None
        except OSError:
            return None

    def warm(self, pool, themes):
        """synthesize the next prefetch lines of every theme that are not cached yet (in the background)"""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        if self.background:
            threading.Thread(target=self._warm, args=(pool, list(themes)), daemon=True).start()
        else:
            self._warm(pool, list(themes))
        return True

    def _warm(self, pool, themes):
        try:
            for theme in themes:
                with pool._lock:
                    lines = list(pool.pool.get(theme, [])[: self.prefetch])
                for line in lines:
                    if self.has(line) or self._size() >= self.max_bytes:
                        continue
                    try:
                        pcm = self.synth(line, self.meta_for(line, theme))
                        self.last_error = None
                    except Exception as e:                      # the cache must never break the session
                        self.failures += 1
                        self.last_error = str(e)
                        continue
                    if pcm:
                        tmp = self._path(line) + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(pcm)
                        os.replace(tmp, self._path(line))
        finally:
            with self._lock:
                self._busy = False

    def _size(self):
        try:
            return sum(os.path.getsize(os.path.join(self.folder, f)) for f in os.listdir(self.folder) if f.endswith(".pcm"))
        except OSError:
            return 0

    def prune(self):
        """drop the oldest files above max_bytes"""
        try:
            files = sorted((os.path.getmtime(os.path.join(self.folder, f)), os.path.join(self.folder, f))
                           for f in os.listdir(self.folder) if f.endswith(".pcm"))
        except OSError:
            return
        total = sum(os.path.getsize(p) for _, p in files)
        for _, p in files:
            if total <= self.max_bytes:
                break
            try:
                total -= os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
