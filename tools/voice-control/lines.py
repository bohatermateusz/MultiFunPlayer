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


def level_key(theme, label):
    """the pool key of a theme's harsher version: the theme with the intensity spelled out (it is also the topic the model
    gets when it writes more lines for that version)"""
    return f"{theme} || {label}" if label else theme


def escalated_seed(themes, seed_up, labels):
    """narration.pool.seed_up (theme name before " - " -> {"1": [...], "2": [...]}) as pool seeds under level keys"""
    out = {}
    by_name = {t.split(" - ")[0].strip().lower(): t for t in themes}
    for name, levels in (seed_up or {}).items():
        theme = by_name.get(str(name).strip().lower())
        if theme is None:
            continue
        for level, lines in (levels or {}).items():
            label = (labels or {}).get(str(level))
            if label and lines:
                out[level_key(theme, label)] = list(lines)
    return out


def scene_seed(scene_cfg, labels):
    """narration.scene.seed (act -> {"0": [...], "1": [...], "2": [...]}) as pool seeds under the scene bank's keys
    (level 0 is the plain key, 1 and 2 the harsher versions named by narration.levels)"""
    from scene import scene_theme
    cfg = scene_cfg or {}
    out = {}
    for act, levels in (cfg.get("seed") or {}).items():
        theme = scene_theme(act, cfg.get("labels"))
        for level, lines in (levels or {}).items():
            label = (labels or {}).get(str(level)) if str(level) != "0" else ""
            if lines and (label or str(level) == "0"):
                out[level_key(theme, label)] = list(lines)
    return out


def _norm(text):
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def similar(a, b, floor=0.0):
    """how alike two lines are (0-1). With a floor, two cheap upper bounds are tried first and 0.0 is returned as soon
    as one of them says the real ratio cannot reach the floor - the exact ratio is costly and rarely needed."""
    m = difflib.SequenceMatcher(None, " ".join(_norm(a)), " ".join(_norm(b)))
    if floor and (m.real_quick_ratio() < floor or m.quick_ratio() < floor):
        return 0.0
    return m.ratio()


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
                 max_words=16, avoid_recent=40, clock=time.time, background=True, seed=None, banned_words=None,
                 retired=None, recycle_hours=3.0):
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
        # when the fresh lines of a theme are spent (and the model wrote none), a line of that theme said more than
        # recycle_hours ago may come back (recycle); 0 = never
        self.recycle_seconds = float(recycle_hours or 0) * 3600
        self._said_at = {}                    # line -> when it was last said (epoch seconds)
        self._said_theme = {}                 # line -> the pool key it was said from
        self._seed_lines = {k: [" ".join(str(x).split()) for x in v] for k, v in (seed or {}).items()}
        self._load()
        gone = {" ".join(str(x).split()) for x in (retired or [])}
        self._retired = gone
        if gone and any(x in gone for v in self.pool.values() for x in v):
            # lines rewritten or withdrawn in commands.json (narration.pool.retired) leave the saved pool
            self.pool = {t: [x for x in v if x not in gone] for t, v in self.pool.items()}
            self._save_pool()
        if seed:
            # the hand-written lines: the first run fills the pool with them; later a theme added since, or a line added
            # to a theme's seed, joins the pool - minus anything already said or already waiting there
            added = False
            with self._lock:
                known = {x for v in self.pool.values() for x in v} | set(self.said) | gone
                for theme, lines in seed.items():
                    # only lines not yet in the pool, said or withdrawn go through the (costly) similarity check
                    new = [" ".join(str(x).split()) for x in lines]
                    new = [x for x in new if x not in known]
                    fresh = self._fresh(theme, new, hand_written=True) if new else []
                    if fresh or theme not in self.pool:
                        self.pool[theme] = self.pool.get(theme, []) + fresh
                        added = True
                if added:
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
                            data = json.loads(row)
                            line = str(data["line"])
                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue
                        self.said.append(line)
                        try:
                            self._said_at[line] = time.mktime(time.strptime(str(data.get("time")), "%Y-%m-%d %H:%M:%S"))
                        except (ValueError, OverflowError):
                            pass
                        if data.get("theme"):
                            self._said_theme[line] = str(data["theme"])
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
    def _acceptable(self, line, seen, hand_written=False):
        line = " ".join(line.split())
        too_long = len(line.split()) > self.max_words and not hand_written      # her own written lines keep their length
        if not line or "?" in line or too_long or self.banned(line):
            return None
        if any(similar(line, s, self.similarity) >= self.similarity for s in seen):
            return None
        return line

    def _fresh(self, theme, candidates, hand_written=False):
        if hand_written:
            # her written lines are chosen on purpose: they are only kept from repeating what she already said, and
            # never two identical ones - comparing each of them with all the others made the first start take minutes
            seen = list(self.said[-self.max_said:])
            have = {x for v in self.pool.values() for x in v}
            out = []
            for c in candidates:
                line = self._acceptable(str(c), seen, hand_written)
                if line and line not in have:
                    out.append(line)
                    have.add(line)
            return out
        seen = list(self.said[-self.max_said:]) + [x for v in self.pool.values() for x in v]
        out = []
        for c in candidates:
            line = self._acceptable(str(c), seen, hand_written)
            if line:
                out.append(line)
                seen.append(line)
        return out

    # ---- the pool --------------------------------------------------------------------------------
    def left(self, theme):
        with self._lock:
            return len(self.pool.get(theme, []))

    def take(self, theme, rng=None):
        """-> a fresh line for the theme (removed from the pool and remembered as said), or None. With rng a random
        line of the theme rather than the oldest one (so the same lines do not come in the same order)."""
        with self._lock:
            lines = self.pool.get(theme, [])
            while lines:
                line = lines.pop(rng.randrange(len(lines)) if rng is not None else 0)
                if not any(similar(line, s) >= self.similarity for s in self.said[-200:]):
                    self._record(line, theme)
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

    def _record(self, line, theme=None):
        now = self.clock()
        self.said.append(line)
        del self.said[:-self.max_said]
        self._said_at[line] = now
        if theme:
            self._said_theme[line] = theme
        if self.said_path:
            row = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "line": line}
            if theme:
                row["theme"] = theme
            try:
                with open(self.said_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def recycle(self, theme, rng=None):
        """-> a line of the theme said again, or None (see recycle_any)"""
        line, _ = self.recycle_any([theme], rng)
        return line

    def recycle_any(self, keys, rng=None):
        """-> (line, key) said again, or (None, None). Only once the fresh lines of those keys are spent: the bank starts
        over - of all their lines (hand-written, or said from them before) the one said the longest ago, so every
        line comes back once before any comes back twice - and never one said within recycle_hours. Recorded as said."""
        if not self.recycle_seconds:
            return None, None
        now = self.clock()
        with self._lock:
            keys = [k for k in dict.fromkeys(keys) if not self.pool.get(k)]       # fresh lines first
            if not keys:
                return None, None
            wanted = set(keys)
            ready = {}
            for k in keys:
                for x in self._seed_lines.get(k, []):
                    ready.setdefault(x, k)
            for x, k in self._said_theme.items():
                if k in wanted:
                    ready[x] = k
            ready = {x: k for x, k in ready.items() if x in self._said_at and now - self._said_at[x] >= self.recycle_seconds
                     and x not in self._retired and not self.banned(x)}
            if not ready:
                return None, None
            oldest = min(self._said_at[x] for x in ready)
            line = (rng or __import__("random")).choice(sorted(x for x in ready if self._said_at[x] - oldest < 60))
            self._record(line, ready[line])
            return line, ready[line]

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
