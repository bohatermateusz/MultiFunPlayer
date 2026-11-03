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


class LinePool:
    def __init__(self, generate, pool_path=None, said_path=None, min_pool=8, batch=20, max_said=2000, similarity=0.8,
                 max_words=16, avoid_recent=40, clock=time.time, background=True):
        self.generate = generate              # generate(theme, avoid: [str], count) -> [str]
        self.pool_path, self.said_path = pool_path, said_path
        self.min_pool, self.batch, self.max_said = int(min_pool), int(batch), int(max_said)
        self.similarity, self.max_words, self.avoid_recent = float(similarity), int(max_words), int(avoid_recent)
        self.clock, self.background = clock, background
        self._lock = threading.Lock()
        self._filling = set()
        self.pool = {}                        # theme -> [lines]
        self.said = []                        # every line ever spoken, oldest first
        self.last_error = None
        self._load()

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
        if not line or "?" in line or len(line.split()) > self.max_words:
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
