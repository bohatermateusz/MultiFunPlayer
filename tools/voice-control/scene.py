"""What is happening in the video right now, so her narration can follow it.

MultiFunPlayer (Buttplug output target, "media status to port") sends the current media path, position and
play/pause state over UDP once a second. Next to each video there may be <video>.timeline.json: the film cut into
intervals (oral / rear / both / none, which machine works), each with an intensity, cum moments and per-minute tags.
SceneTracker reads both and answers two questions:

  now()      -> the act on screen at this moment ({"act", "intensity", "tempo", "group", "summary", ...}) or None
  pending()  -> a key moment that should be commented at once (a new act, the seconds before a cum, the cum itself,
                the machine clearly speeding up or slowing down)

Acts are what she can talk about, seen from her side (the front machine is her mouth, the rear one her hole):
oral, fucked, both_ends, played, tease, and around a cum: cum_soon, cum_mouth, cum_on, cum_inside.
The tempo comes from the film's stroke scripts (<video>.suck.funscript for the front, .rear.funscript for the rear):
strokes per minute of the machine that works now -> still / slow / medium / fast / max.
No file, a paused video or no news from the player for a while -> no scene: narration goes on as before."""
import bisect
import json
import os
import re
import socket
import threading
import time
import urllib.parse

ACTS = ("oral", "fucked", "both_ends", "played", "tease")
CUM_ACTS = {"mouth": "cum_mouth", "face": "cum_on", "body": "cum_on", "ass": "cum_on",
            "inside_vagina": "cum_inside", "inside_anus": "cum_inside"}
KIND_ACTS = {"oral": "oral", "rear": "fucked", "both": "both_ends"}
PLAY_TAGS = ("toy", "fingering", "handjob", "masturbation", "cunnilingus", "titjob", "footjob")
TEMPOS = ("still", "slow", "medium", "fast", "max")
STROKE_SCRIPTS = {"front": (".suck.funscript", ".funscript"), "rear": (".rear.funscript",)}
DEFAULTS = {
    "stale_seconds": 5.0,        # no packet for this long -> the player is gone, no scene
    "act_min_seconds": 6.0,      # a new act is announced once it has lasted this long
    "act_event_window": 20.0,    # ... and no later than this after it began
    "cum_soon_window": [12.0, 4.0],  # seconds before a cum when "he is about to" may be said
    "cum_window": 6.0,           # seconds after a cum when "now" may be said
    "cum_act_seconds": 25.0,     # how long after a cum the scene counts as that cum
    "tempo_levels": [10, 50, 85, 115],   # strokes per minute where slow / medium / fast / max begin
    "tempo_window": 6.0,         # seconds of the script the tempo is counted over
    "tempo_hold": 4.0,           # a new tempo is a key moment once it has lasted this long
    "tempo_event_gap": 40.0,     # ... and not sooner than this after the last one
}


def scene_theme(act, labels=None):
    """the pool "theme" of an act's scene bank: "scena: <act> - <what is happening to her>" (the part after " - "
    is what the model reads when it writes more lines for it)"""
    label = (labels or {}).get(act)
    return f"scena: {act} - {label}" if label else f"scena: {act}"


def media_stem(path):
    """the video's name without folder and extension, from a Windows path, a POSIX path or a URL"""
    if not path:
        return ""
    text = str(path)
    if "://" in text:
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
    name = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    stem, ext = os.path.splitext(name)
    return stem if ext and len(ext) <= 5 else name


# the scripts scene-map writes next to a film: <film>.<part>.funscript (and <film>.funscript)
SCRIPT_PARTS = ("rear", "rear_width", "rear_depth", "rear_speed", "suck", "suck_speed", "valve", "pump", "lube")


def film_from_scripts(names):
    """the film's name from the scripts MultiFunPlayer loaded ("Film.rear.funscript", "Film.suck_speed.funscript", ...):
    the stem most of them share, or None - for a player that gives no file path (a Stash stream ".../scene/414/stream")"""
    stems = []
    for name in names or []:
        base = re.split(r"[\\/]", str(name))[-1]
        if not base.lower().endswith(".funscript"):
            continue
        base = base[: -len(".funscript")]
        head, _, part = base.rpartition(".")
        stems.append(head if head and part.lower() in SCRIPT_PARTS else base)
    if not stems:
        return None
    return max(set(stems), key=lambda x: (stems.count(x), len(x)))


def find_sidecar(path, dirs, suffix):
    """-> the path of <stem><suffix> next to the video or in one of dirs, or None"""
    stem = media_stem(path)
    if not stem:
        return None
    folders = list(dirs or [])
    if path and "://" not in str(path):
        folders.insert(0, os.path.dirname(str(path)))
    for folder in folders:
        if not folder:
            continue
        candidate = os.path.join(folder, stem + suffix)
        try:
            if os.path.isfile(candidate):
                return candidate
        except OSError:
            continue
    return None


def find_timeline(path, dirs):
    """-> the path of <stem>.timeline.json next to the video or in one of dirs, or None"""
    return find_sidecar(path, dirs, ".timeline.json")


def load_reversals(path):
    """-> the sorted times (s) where a stroke script turns (one stroke = two turns), or None"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            actions = sorted(json.load(f).get("actions") or [], key=lambda a: a["at"])
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return None
    out = []
    for a, b, c in zip(actions, actions[1:], actions[2:]):
        d1, d2 = b["pos"] - a["pos"], c["pos"] - b["pos"]
        if d1 * d2 < 0 and abs(d1) >= 10:
            out.append(b["at"] / 1000.0)
    return out


def load_strokes(path, dirs):
    """-> {"front": [turn times], "rear": [...]} from the stroke scripts next to the video (sides without one left out)"""
    out = {}
    for side, suffixes in STROKE_SCRIPTS.items():
        for suffix in suffixes:
            found = find_sidecar(path, dirs, suffix)
            turns = load_reversals(found) if found else None
            if turns is not None:
                out[side] = turns
                break
    return out


def stroke_rate(turns, position, window=6.0):
    """strokes per minute around a position"""
    if not turns:
        return 0.0
    n = bisect.bisect(turns, position + window / 2) - bisect.bisect(turns, position - window / 2)
    return n / 2 / window * 60


def tempo_of(strokes, act, position, levels=(10, 50, 85, 115), window=6.0):
    """-> (tempo name, strokes per minute) of the machine the act uses (both at once: the faster one), or (None, None)"""
    sides = {"oral": ["front"], "fucked": ["rear"]}.get(act, ["front", "rear"])
    rates = [stroke_rate(strokes[s], position, window) for s in sides if s in (strokes or {})]
    if not rates:
        return None, None
    spm = max(rates)
    return TEMPOS[bisect.bisect(list(levels), spm)], round(spm)


def load_timeline(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("intervals"), list) else None


def cum_moments(timeline):
    """-> sorted [(t, where)] of every cum in the film"""
    out = set()
    for iv in (timeline or {}).get("intervals") or []:
        for c in iv.get("cum") or []:
            try:
                out.add((float(c["t"]), str(c.get("where") or "body")))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(out)


def act_at(timeline, position):
    """-> the ongoing act (without cum) at a position: {"act", "intensity", "group", "summary", "start", "end"} or None.
    The interval's kind decides (it is what the machines do: front = her mouth, rear = her hole); an interval with
    neither is "played" (hands, toys, a tongue) or "tease" (a pause, talk, undressing) by its minute's tags."""
    for iv in (timeline or {}).get("intervals") or []:
        try:
            start, end = float(iv["start"]), float(iv["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not start <= position < end:
            continue
        minute = next((m for m in iv.get("minutes") or [] if float(m.get("start", 0)) <= position < float(m.get("end", 0))), None) or {}
        tags = set(minute.get("tags") or [])
        overall = iv.get("overall") or {}
        act = KIND_ACTS.get(str(iv.get("kind")))
        if "spitroast" in tags and act in ("oral", "fucked"):
            act = "both_ends"
        if act is None:
            act = "played" if tags & set(PLAY_TAGS) or minute.get("penetration") == "handjob" else "tease"
        summaries = iv.get("summaries") or []
        return {"act": act, "intensity": str(minute.get("intensity") or overall.get("intensity") or "building"),
                "group": "group" in tags or int(minute.get("people") or overall.get("people") or 0) >= 3,
                "summary": str(summaries[0]) if summaries else "", "start": start, "end": end}
    return None


class SceneTracker:
    """the player's state (fed by the UDP listener, or by feed() in tests) and the timeline of the video it plays"""

    def __init__(self, dirs=None, clock=time.monotonic, options=None, loader=None, strokes_loader=None):
        self.dirs = [str(d) for d in (dirs or [])]
        self.clock = clock
        self.opt = dict(DEFAULTS, **(options or {}))
        self.loader = loader or (lambda path: (lambda f: load_timeline(f) if f else None)(find_timeline(path, self.dirs)))
        self.strokes_loader = strokes_loader or (lambda path: load_strokes(path, self.dirs))
        self.strokes = {}
        self._lock = threading.Lock()
        self.path, self.position, self.playing, self.duration = None, 0.0, False, 0.0
        self._fed_at = None
        self.timeline, self._cums = None, []
        self._done = set()               # key moments already said (or let pass) in this film
        self._act, self._act_since, self._act_announced = None, None, None
        self._tempo, self._tempo_since, self._tempo_from, self._tempo_said = None, None, None, -1e9
        self._sock = None
        self.last_error = None
        self.machine_side = None         # "front" / "rear" from MFP, None until it says

    # ---- input -----------------------------------------------------------------------------------
    def feed(self, path, position, playing, duration=None):
        now = self.clock()
        with self._lock:
            if path != self.path:
                self.path = path
                self.timeline = self.loader(path) if path else None
                self.strokes = (self.strokes_loader(path) or {}) if self.timeline is not None else {}
                self._cums = cum_moments(self.timeline)
                self._done.clear()
                self._act = self._act_since = self._act_announced = None
                self._tempo = self._tempo_since = self._tempo_from = None
            elif self._fed_at is not None and abs(float(position) - self._pos(now)) > 5.0:
                if float(position) < self._pos(now):
                    # seeking back: what lies ahead can be said again
                    self._done = {k for k in self._done if k[1] < float(position)}
                    self._act_announced = None
                self._tempo = self._tempo_since = self._tempo_from = None   # a jump is not the machine changing pace
            self.position, self.playing = float(position), bool(playing)
            if duration:
                self.duration = float(duration)
            self._fed_at = now

    def handle_packet(self, data):
        try:
            msg = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(msg, dict) or msg.get("event") != "media":
            return False
        if msg.get("machine_side") in ("front", "rear"):
            self.machine_side = msg["machine_side"]     # where MFP says the S2 is (its MACHINE switch)
        path = msg.get("path") or None
        film = film_from_scripts(msg.get("scripts"))
        if film and (not path or "://" in str(path)):
            path = film + ".mp4"               # a stream URL says nothing: the scripts name the film (found in dirs)
        self.feed(path, float(msg.get("position") or 0.0), bool(msg.get("playing")), msg.get("duration"))
        return True

    def paused(self):
        """True when the player is there (fresh news) and says the film is paused - she keeps quiet then"""
        now = self.clock()
        with self._lock:
            return (self.path is not None and self._fed_at is not None and not self.playing
                    and now - self._fed_at <= float(self.opt["stale_seconds"]))

    def progress(self):
        """-> (position s, duration s) of the film playing now, or None (nothing playing, the player went quiet)"""
        now = self.clock()
        with self._lock:
            if not (self.playing and self._fed_at is not None and now - self._fed_at <= float(self.opt["stale_seconds"])):
                return None
            return self._pos(now), self.duration

    def start(self, port=23556, host="127.0.0.1"):
        """listens for the player's status in a background thread; -> True when the port could be bound"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((host, int(port)))
        except OSError as e:
            self.last_error = str(e)
            return False
        self._sock = sock

        def run():
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except OSError:
                    return
                try:
                    self.handle_packet(data)
                except Exception as e:          # a bad packet or a broken timeline must never stop the listener
                    self.last_error = str(e)
        threading.Thread(target=run, daemon=True).start()
        return True

    def stop(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # ---- state -----------------------------------------------------------------------------------
    def _pos(self, now):
        return self.position + (now - self._fed_at if self.playing and self._fed_at is not None else 0.0)

    def _live(self, now):
        return (self.timeline is not None and self.playing and self._fed_at is not None
                and now - self._fed_at <= float(self.opt["stale_seconds"]))

    def now(self):
        """-> the scene at this moment, or None (no timeline, paused, the player went quiet)"""
        now = self.clock()
        with self._lock:
            if not self._live(now):
                return None
            pos = self._pos(now)
            scene = act_at(self.timeline, pos)
            for t, where in self._cums:
                if t <= pos <= t + float(self.opt["cum_act_seconds"]):
                    scene = dict(scene or {"intensity": "climax", "group": False, "summary": ""},
                                 act=CUM_ACTS.get(where, "cum_on"), where=where, moment="cum")
                    break
            if scene is None:
                return None
            scene["position"] = round(pos, 1)
            scene["tempo"], scene["spm"] = tempo_of(self.strokes, scene["act"], pos, self.opt["tempo_levels"],
                                                    float(self.opt["tempo_window"]))
            self._track_act(scene["act"], now)
            self._track_tempo(scene["tempo"], now)
            return scene

    def _track_act(self, act, now):
        if act != self._act:
            self._act, self._act_since = act, now
            self._tempo = self._tempo_since = self._tempo_from = None   # a new act's pace is part of it, not a change of pace

    def _track_tempo(self, tempo, now):
        if tempo != self._tempo:
            if self._tempo is not None:
                self._tempo_from = self._tempo if self._tempo_from is None or self._tempo_since is None \
                    or now - self._tempo_since >= float(self.opt["tempo_hold"]) else self._tempo_from
            self._tempo, self._tempo_since = tempo, now

    def pending(self):
        """-> the key moment to comment now, or None: {"event": "cum" | "cum_soon" | "act_change", "act", "key"}.
        The most urgent first; a moment stays pending only inside its window and is said once (consume())."""
        now = self.clock()
        scene = self.now()
        with self._lock:
            if scene is None:
                return None
            pos = scene["position"]
            lo, hi = (float(x) for x in self.opt["cum_soon_window"])
            for t, where in self._cums:
                if t <= pos <= t + float(self.opt["cum_window"]) and ("cum", t) not in self._done:
                    return {"event": "cum", "act": CUM_ACTS.get(where, "cum_on"), "where": where, "key": ("cum", t)}
            for t, where in self._cums:
                if t - lo <= pos <= t - hi and ("cum_soon", t) not in self._done:
                    return {"event": "cum_soon", "act": "cum_soon", "where": where, "key": ("cum_soon", t)}
            act = scene["act"]
            if (act in ACTS and act != self._act_announced and self._act_since is not None
                    and float(self.opt["act_min_seconds"]) <= now - self._act_since <= float(self.opt["act_event_window"])):
                return {"event": "act_change", "act": act, "key": ("act", pos)}
            change = self._tempo_change(now)
            if change and act in ACTS:
                return {"event": "tempo", "act": change, "tempo": self._tempo, "key": ("tempo", pos)}
            return None

    def _tempo_change(self, now):
        """"faster" when the machine went from at most medium to fast / max, "slower" from fast / max down to slow /
        still - once the new pace has lasted tempo_hold, and not within tempo_event_gap of the last one"""
        if self._tempo is None or self._tempo_from is None or self._tempo_since is None:
            return None
        if now - self._tempo_since < float(self.opt["tempo_hold"]) or now - self._tempo_said < float(self.opt["tempo_event_gap"]):
            return None
        new, old = TEMPOS.index(self._tempo), TEMPOS.index(self._tempo_from)
        if new >= 3 and old <= 2:
            return "faster"
        if new <= 1 and old >= 3:
            return "slower"
        return None

    def consume(self, event):
        """the moment was said (or skipped for good): it does not come back in this film"""
        with self._lock:
            if event["event"] == "act_change":
                self._act_announced = event["act"]
            elif event["event"] == "tempo":
                self._tempo_said, self._tempo_from = self.clock(), self._tempo
            else:
                self._done.add(event["key"])


# ---- preview: what her narration would do in a film, without MultiFunPlayer, a voice or an API key ---------------
def _clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _seconds(text):
    parts = [float(p) for p in str(text).split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def segments(timeline, step=1.0):
    """-> [(start, end, act-dict)]: the film cut where the act, the intensity or the group changes"""
    out, duration, pos = [], float(timeline.get("duration") or 0), 0.0
    if not duration:
        duration = max((float(iv.get("end", 0)) for iv in timeline.get("intervals") or []), default=0.0)
    while pos < duration:
        a = act_at(timeline, pos)
        sig = (a["act"], a["intensity"], a["group"]) if a else None
        if out and out[-1][3] == sig:
            out[-1][1] = pos + step
        else:
            out.append([pos, pos + step, a, sig])
        pos += step
    return [(s, min(e, duration), a) for s, e, a, _ in out]


def simulate(timeline, strokes, options=None, event_gap=15.0, cum_gap=8.0, line_seconds=4.0, chance=None,
             scene_min_gap=0.0, rng=None):
    """-> [(position, event, said)]: the key moments of the film as in a session where you stay quiet, played second
    by second. A moment waits while she is still speaking (line_seconds) or within event_gap (a cum: cum_gap) of her
    last line and is lost when its window passes; then it is said with its chance, unless the last line about the
    video was less than scene_min_gap ago. Bank lines are not simulated."""
    import random
    rng = rng or random.Random()
    clock = [0.0]
    t = SceneTracker(clock=lambda: clock[0], options=options, loader=lambda p: timeline, strokes_loader=lambda p: strokes)
    out, last, last_scene = [], -1e9, -1e9
    for pos in range(int(float(timeline.get("duration") or 0))):
        clock[0] = float(pos)
        t.feed("film", pos, True)
        ev = t.pending()
        if not ev:
            continue
        gap = cum_gap if ev["event"] in ("cum", "cum_soon") else event_gap
        if pos - last < max(gap, line_seconds):
            continue
        t.consume(ev)
        said = pos - last_scene >= scene_min_gap and rng.random() < float((chance or {}).get(ev["event"], 1.0))
        out.append((pos, ev, said))
        if said:
            last = last_scene = pos
    return out


def preview(target, config="commands.json", stage=2, lang="pl", at=None, samples=2, out=print, seed=None):
    """prints the film act by act: the tempo, what she says at once (the key moments), and what she does between them"""
    from controller import deep_merge
    with open(config, encoding="utf-8") as f:
        pc = json.load(f).get("persona", {})
    if lang != "pl":
        pc = deep_merge(pc, (pc.get("languages") or {}).get(lang, {}))
    n = pc.get("narration", {})
    sc = n.get("scene") or {}
    path = target if str(target).endswith(".timeline.json") else find_timeline(target, sc.get("dirs") or [])
    timeline = load_timeline(path) if path else None
    if timeline is None:
        out(f"No timeline for {media_stem(target) or target} (looked next to the video and in {sc.get('dirs')})")
        return 1
    video = path[: -len(".timeline.json")] + ".mp4"
    strokes = load_strokes(video, [])
    opt = dict(DEFAULTS, **(sc.get("tracker") or {}))
    levels = sc.get("levels_by_stage") or [0, 0, 1, 1, 2]
    level = int(levels[max(0, min(stage, len(levels) - 1))])
    lines_seed, labels = sc.get("seed") or {}, sc.get("labels") or {}
    import random
    pick = random.Random(seed)

    def line(act):
        for lv in range(level, -1, -1):
            got = (lines_seed.get(act) or {}).get(str(lv)) or []
            if got:
                return pick.choice(got)
        return "-"

    gaps = n.get("gap_seconds") or [[60, 120]]
    lo, hi = gaps[max(0, min(stage, len(gaps) - 1))]
    factors = sc.get("tempo_gap_factor") or {}
    voice = {"cum_soon": "urgent", "cum": "triumph", "faster": "urgent", "slower": "whisper"}
    import random
    events = simulate(timeline, strokes, sc.get("tracker"), float(sc.get("event_gap", 15)), float(sc.get("cum_gap", 8)),
                      chance=sc.get("event_chance"), scene_min_gap=float(sc.get("scene_min_gap", 0)), rng=random.Random(seed))
    out(f"{media_stem(path[: -len('.timeline.json')])}  ({_clock(timeline.get('duration') or 0)})")
    out(f"stage {stage}, language {lang}: bank lines every {lo}-{hi} s (x tempo); a key moment is said now and then "
        f"(one random run - every run differs); stroke scripts: {', '.join(sorted(strokes)) or 'none (no tempo)'}")
    for start, end, a in segments(timeline):
        if at is not None and not start <= at < end:
            continue
        out("")
        if a is None:
            out(f"{_clock(start)}-{_clock(end)}  (nothing in the timeline: bank lines as without a video)")
            continue
        tempos = [tempo_of(strokes, a["act"], p, opt["tempo_levels"], float(opt["tempo_window"])) for p in range(int(start), int(end))]
        names = [x for x, _ in tempos if x]
        spm = [x for _, x in tempos if x is not None]
        tempo = (f"tempo {min(spm)}-{max(spm)}/min, mostly {max(set(names), key=names.count)}" if names else "no tempo")
        out(f"{_clock(start)}-{_clock(end)}  {a['act'].upper()}  [{tempo}{', group' if a['group'] else ''}]  {labels.get(a['act'], '')}")
        if a["summary"]:
            out(f"   on screen: {a['summary']}")
        for pos, ev, said in events:
            if not start <= pos < end:
                continue
            what = {"act_change": "new act", "cum_soon": "about to cum", "cum": f"CUM ({ev.get('where')})",
                    "tempo": "speeds up" if ev["act"] == "faster" else "slows down"}[ev["event"]]
            if not said:
                out(f"   {_clock(pos)}  {what}: passes without a word this time")
                continue
            v = voice.get(ev["act"] if ev["event"] == "tempo" else ev["event"], "by tone")
            out(f"   {_clock(pos)}  {what} -> \"{line(ev['act'])}\"  [voice: {v}]")
        if names:
            main = max(set(names), key=names.count)
            f = float(factors.get(main, 1.0))
            mood = "sharp" if main == "max" else ("tender / whispered" if main in ("still", "slow") else "by stage")
            out(f"   between them: bank lines every {round(lo * f)}-{round(hi * f)} s, tone {mood}")
        else:
            out(f"   between them: bank lines every {lo}-{hi} s")
    return 0


if __name__ == "__main__":
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="What her narration would do in a film: act by act, the tempo, what she "
                                             "says at the key moments and what she does between them. Needs only the "
                                             "timeline (and the stroke scripts for the tempo).")
    ap.add_argument("film", help="the video (path, URL or just its name) or its .timeline.json")
    ap.add_argument("--stage", type=int, default=2, help="escalation stage 0-4 (default 2)")
    ap.add_argument("--lang", default="pl", help="pl / en")
    ap.add_argument("--at", help="only the act at this time, e.g. 2:20")
    ap.add_argument("--seed", type=int, help="the same random run again")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.json"))
    a = ap.parse_args()
    sys.exit(preview(a.film, a.config, a.stage, a.lang, _seconds(a.at) if a.at else None, seed=a.seed))
