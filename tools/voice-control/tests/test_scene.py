"""Narration follows the video: when something happens on screen (timeline.json next to the film + the player's
position sent by MultiFunPlayer + the stroke scripts' tempo) she says it at once - a new act, the seconds before a cum,
the cum, the machine speeding up or slowing down; while nothing new happens she says bank lines, in the scene's mood."""
import copy
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lines import LinePool, is_banned_line, scene_seed, _norm  # noqa: E402
from scene import SceneTracker, act_at, cum_moments, find_timeline, media_stem, scene_theme, simulate, stroke_rate, tempo_of  # noqa: E402
from test_manners import P, Server  # noqa: E402
from tts import choose_delivery  # noqa: E402

N = P["narration"]
SC = N["scene"]
EN = P["languages"]["en"]["narration"]

TIMELINE = {
    "film": "Film.mp4", "duration": 400.0,
    "intervals": [
        {"start": 0.0, "end": 60.0, "kind": "none", "summaries": ["Rozbieranie i pozowanie."],
         "overall": {"intensity": "calm", "people": 1},
         "minutes": [{"start": 0.0, "end": 60.0, "tags": ["intro", "tease"], "penetration": "none"}]},
        {"start": 60.0, "end": 150.0, "kind": "oral", "summaries": ["Seks oralny na kolanach."],
         "overall": {"intensity": "building", "people": 2},
         "cum": [{"t": 140, "where": "mouth", "confidence": 0.9}],
         "minutes": [{"start": 60.0, "end": 120.0, "tags": ["front", "blowjob"], "penetration": "oral", "intensity": "building"},
                     {"start": 120.0, "end": 150.0, "tags": ["front", "blowjob", "cum_soon"], "penetration": "oral", "intensity": "climax"}]},
        {"start": 150.0, "end": 260.0, "kind": "rear", "summaries": ["Seks od tyłu."],
         "overall": {"intensity": "climax", "people": 2},
         "minutes": [{"start": 150.0, "end": 260.0, "tags": ["rear", "doggy", "sex"], "penetration": "vaginal"}]},
        {"start": 260.0, "end": 330.0, "kind": "both", "summaries": ["Na rożnie."],
         "overall": {"intensity": "climax", "people": 3},
         "minutes": [{"start": 260.0, "end": 330.0, "tags": ["spitroast", "group"], "penetration": "vaginal", "people": 3}]},
        {"start": 330.0, "end": 400.0, "kind": "none", "summaries": ["Palcówka."],
         "overall": {"intensity": "building"},
         "minutes": [{"start": 330.0, "end": 400.0, "tags": ["fingering"], "penetration": "none"}]},
    ],
}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def turns(spans):
    """stroke-script turns for [(start, end, strokes per minute)]"""
    out = []
    for start, end, spm in spans:
        if spm:
            step = 60.0 / spm / 2
            t = start
            while t < end:
                out.append(round(t, 3))
                t += step
    return out


# front (her mouth): slow until 90 s, then fast; rear: medium, slowing to still at 230 s
STROKES = {"front": turns([(60, 90, 30), (90, 150, 130)]), "rear": turns([(150, 230, 70), (260, 330, 100)])}


def tracker(clock, strokes=None):
    return SceneTracker(clock=clock, loader=lambda path: copy.deepcopy(TIMELINE) if path else None,
                        strokes_loader=lambda path: strokes or {})


class Timeline(unittest.TestCase):
    def test_media_stem_from_any_kind_of_path(self):
        self.assertEqual(media_stem(r"\\nas\share\Some.Film.Name.mp4"), "Some.Film.Name")
        self.assertEqual(media_stem("/mnt/x/Some.Film.Name.mkv"), "Some.Film.Name")
        self.assertEqual(media_stem("http://192.168.0.1:8080/files/Some%20Film.mp4?x=1"), "Some Film")
        self.assertEqual(media_stem(""), "")

    def test_the_timeline_is_found_by_name_in_the_folders(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Film.timeline.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(TIMELINE, f)
            self.assertEqual(find_timeline("http://player/Film.mp4", [d]), path)
            self.assertEqual(find_timeline(os.path.join(d, "Film.mp4"), []), path)
            self.assertIsNone(find_timeline("http://player/Other.mp4", [d]))

    def test_the_act_follows_the_machines(self):
        self.assertEqual(act_at(TIMELINE, 10)["act"], "tease")
        self.assertEqual(act_at(TIMELINE, 70)["act"], "oral")
        self.assertEqual(act_at(TIMELINE, 200)["act"], "fucked")
        both = act_at(TIMELINE, 300)
        self.assertEqual(both["act"], "both_ends")
        self.assertTrue(both["group"])
        self.assertEqual(act_at(TIMELINE, 350)["act"], "played")
        self.assertEqual(act_at(TIMELINE, 130)["intensity"], "climax")       # the minute's intensity wins
        self.assertIsNone(act_at(TIMELINE, 500))

    def test_cum_moments(self):
        self.assertEqual(cum_moments(TIMELINE), [(140.0, "mouth")])

    def test_tempo_from_the_stroke_scripts(self):
        self.assertAlmostEqual(stroke_rate(STROKES["front"], 75), 30, delta=6)
        self.assertEqual(tempo_of(STROKES, "oral", 75)[0], "slow")
        self.assertEqual(tempo_of(STROKES, "oral", 120)[0], "max")
        self.assertEqual(tempo_of(STROKES, "fucked", 200)[0], "medium")
        self.assertEqual(tempo_of(STROKES, "fucked", 245)[0], "still")
        self.assertEqual(tempo_of(STROKES, "both_ends", 300)[0], "fast")      # both machines: the faster one
        self.assertEqual(tempo_of({}, "oral", 75), (None, None))


class Tracker(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s = tracker(self.clock)

    def at(self, pos, playing=True):
        self.s.feed("Film.mp4", pos, playing)

    def test_no_scene_without_a_video_when_paused_or_when_the_player_goes_quiet(self):
        self.assertIsNone(self.s.now())
        self.at(70, playing=False)
        self.assertIsNone(self.s.now())
        self.at(70)
        self.assertEqual(self.s.now()["act"], "oral")
        self.clock.t += 30                                       # no packet for 30 s
        self.assertIsNone(self.s.now())

    def test_the_position_runs_on_between_packets(self):
        self.at(57)
        self.clock.t += 4                                         # no packet for 4 s, the film plays on
        self.assertEqual(self.s.now()["act"], "oral")

    def test_the_seconds_after_a_cum_are_that_cum(self):
        self.at(145)
        scene = self.s.now()
        self.assertEqual((scene["act"], scene["moment"]), ("cum_mouth", "cum"))

    def test_cum_soon_then_cum_each_once(self):
        self.at(130)                                             # 10 s before
        ev = self.s.pending()
        self.assertEqual(ev["event"], "cum_soon")
        self.s.consume(ev)
        self.assertIsNone(self.s.pending())
        self.at(140.5)
        ev = self.s.pending()
        self.assertEqual((ev["event"], ev["act"]), ("cum", "cum_mouth"))
        self.s.consume(ev)
        self.assertIsNone(self.s.pending())
        self.at(155)                                             # the window is over: nothing more
        self.assertIsNone(self.s.pending())

    def test_seeking_back_brings_the_moments_back(self):
        self.at(130)
        self.s.consume(self.s.pending())
        self.at(60)
        self.at(129)
        self.assertEqual(self.s.pending()["event"], "cum_soon")

    def test_a_new_act_is_announced_once_it_lasted_a_while(self):
        self.at(190)
        self.s.now()
        self.assertIsNone(self.s.pending())                      # just began
        self.clock.t += 7
        self.at(197)
        ev = self.s.pending()
        self.assertEqual((ev["event"], ev["act"]), ("act_change", "fucked"))
        self.s.consume(ev)
        self.clock.t += 2
        self.at(199)
        self.assertIsNone(self.s.pending())

    def test_speeding_up_and_slowing_down_are_key_moments(self):
        s = tracker(self.clock, STROKES)
        for pos in range(60, 100):                               # slow, then fast from 90 s
            self.clock.t += 1
            s.feed("Film.mp4", pos, True)
            ev = s.pending()
            if ev and ev["event"] == "act_change":
                s.consume(ev)
            elif ev:
                break
        self.assertEqual((ev["event"], ev["act"]), ("tempo", "faster"))
        self.assertGreaterEqual(pos, 93)                         # once the new pace has lasted a few seconds
        s.consume(ev)
        self.clock.t += 1
        s.feed("Film.mp4", pos + 1, True)
        self.assertIsNone(s.pending())

    def test_the_pace_of_a_new_act_is_not_a_change_of_pace(self):
        events = simulate(TIMELINE, {"front": turns([(60, 150, 30)]), "rear": turns([(150, 260, 130)])})
        self.assertNotIn("tempo", [e["event"] for _, e, _ in events])

    def test_a_jump_is_not_a_change_of_pace(self):
        s = tracker(self.clock, STROKES)
        for pos in range(60, 75):
            self.clock.t += 1
            s.feed("Film.mp4", pos, True)
            s.pending()
        for pos in range(110, 125):                              # seek into the fast part
            self.clock.t += 1
            s.feed("Film.mp4", pos, True)
            ev = s.pending()
            self.assertFalse(ev and ev["event"] == "tempo", ev)

    def test_packets(self):
        self.assertTrue(self.s.handle_packet(json.dumps({"event": "media", "path": "Film.mp4", "position": 70, "playing": True}).encode()))
        self.assertEqual(self.s.now()["act"], "oral")
        self.assertFalse(self.s.handle_packet(b"garbage"))
        self.assertFalse(self.s.handle_packet(b'{"event": "other"}'))


class Bank(unittest.TestCase):
    def test_every_act_has_three_levels_of_clean_short_lines_in_both_languages(self):
        banned = N["pool"]["banned_words"]
        for cfg in (SC, EN["scene"]):
            self.assertEqual(set(cfg["seed"]), set(cfg["labels"]))
            for act, levels in cfg["seed"].items():
                self.assertEqual(set(levels), {"0", "1", "2"}, act)
                for level, lines in levels.items():
                    self.assertGreaterEqual(len(lines), 5, (act, level))
                    for line in lines:
                        self.assertLessEqual(len(_norm(line)), N["max_words"], line)
                        self.assertNotIn("?", line)
                        self.assertFalse(is_banned_line(line, banned), line)

    def test_act_themes_name_real_themes(self):
        for cfg in (N, EN):
            names = {t.split(" - ")[0].strip().lower() for t in cfg["themes"]}
            for act, themes in cfg["scene"]["act_themes"].items():
                missing = {t.lower() for t in themes} - names
                self.assertFalse(missing, (act, missing))

    def test_seed_keys(self):
        seed = scene_seed(SC, N["levels"])
        theme = scene_theme("oral", SC["labels"])
        self.assertIn(theme, seed)
        self.assertIn(f"{theme} || {N['levels']['2']}", seed)
        self.assertEqual(len(seed), 3 * len(SC["seed"]))


class Preview(unittest.TestCase):
    def test_the_film_act_by_act_without_the_player(self):
        from scene import preview
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Film.timeline.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(TIMELINE, f)
            out = []
            cfg = os.path.join(os.path.dirname(__file__), "..", "commands.json")
            self.assertEqual(preview(path, cfg, stage=2, out=out.append), 0)
            text = "\n".join(out)
            for act in ("TEASE", "ORAL", "FUCKED", "BOTH_ENDS", "PLAYED"):
                self.assertIn(act, text)
            self.assertIn("02:20  CUM (mouth)", text)          # said or let pass: the dice decide
            self.assertIn("between them: bank lines", text)
            out.clear()
            preview(path, cfg, stage=4, lang="en", at=300, out=out.append)
            self.assertIn("BOTH_ENDS", "\n".join(out))
            self.assertNotIn("ORAL", "\n".join(out))
            self.assertEqual(preview("Nothing.mp4", cfg, out=out.append), 1)


class Delivery(unittest.TestCase):
    def test_the_moment_picks_the_voice(self):
        self.assertEqual(choose_delivery({}, {"tone": "ostry", "kind": "narration", "moment": "cum_soon"})[0], "urgent")
        self.assertEqual(choose_delivery({}, {"tone": "ostry", "kind": "narration", "moment": "cum"})[0], "triumph")
        self.assertEqual(choose_delivery({}, {"tone": "ostry", "kind": "narration", "moment": "faster"})[0], "urgent")
        self.assertEqual(choose_delivery({}, {"tone": "czuły", "kind": "narration", "moment": "slower"})[0], "whisper")
        self.assertEqual(choose_delivery({}, {"tone": "czuły", "kind": "narration", "moment": "calm"})[0], "whisper")
        self.assertEqual(choose_delivery({}, {"tone": "ostry", "kind": "narration", "moment": "calm", "stage": 0})[0], "tease")
        self.assertEqual(choose_delivery({}, {"tone": "ostry", "kind": "task", "moment": "cum"})[0], "sharp")   # an order stays an order


class FollowsTheScene(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)
        self.c.persona_cfg["narration"]["tasks"]["enabled"] = False
        self.c.persona_cfg["initiative"]["session_seconds"] = 100_000
        self.c.persona_cfg["narration"]["min_gap_seconds"] = [0, 0]   # the pause between her lines: own tests (MinGap)
        self.c.persona_cfg["narration"]["address"]["enabled"] = False  # what she calls her: own tests (test_address)
        sc = self.c.persona_cfg["narration"]["scene"]             # the mechanics: every moment said, from the scene lines
        sc.update(event_chance={}, scene_min_gap=0, theme_share=0.0, milder_share=0.0)
        self.dir = tempfile.TemporaryDirectory()
        seed = scene_seed(SC, N["levels"])
        rnd = random.Random(0)

        def gibberish():                                          # lines that are not alike (the pool drops near repeats)
            return " ".join("".join(rnd.choice("abcdefghijklmnoprstuwyz") for _ in range(5)) for _ in range(5))
        seed.update({t: [f"{t.split(' - ')[0]} | {gibberish()}." for _ in range(30)] for t in N["themes"]})
        self.c.pool = LinePool(lambda *a: [], os.path.join(self.dir.name, "p.json"), os.path.join(self.dir.name, "s.jsonl"),
                               background=False, seed=seed, min_pool=0)
        self.c.persona.narrate = lambda *a, **k: None
        self.c.scene = tracker(self.clock)
        self.c.rng = random.Random(5)
        self.say("kochanie, jestem twoja", request="none")
        self.said.clear()

    def tearDown(self):
        self.dir.cleanup()

    def heat(self, h):
        self.c._heat, self.c._heat_last = h, self.clock.t

    def play(self, pos):
        self.c.scene.feed("Film.mp4", pos, True)

    def test_between_key_moments_she_says_bank_lines(self):
        self.heat(15.0)                                          # stage 2
        scene_lines = {x for v in SC["seed"].values() for lv in v.values() for x in lv}
        for _ in range(20):
            self.play(200)                                       # the same act all along: nothing new happens
            self.c._narration_due = self.clock.t
            self.c._last_line = self.clock.t - 100
            self.c._last_heard = 0.0
            self.c.idle_narration()
            self.clock.t += 1
        lines = [line for line, _ in self.said]
        self.assertEqual(len(lines), 20)
        self.assertFalse(scene_lines & set(lines))
        self.assertGreater(len({line.split(" | ")[0] for line in lines}), 5)    # all kinds of themes, not only the act's

    def test_the_tempo_sets_the_gap(self):
        self.c.scene = tracker(self.clock, STROKES)
        self.heat(15.0)
        self.c.rng = random.Random(1)
        self.play(120)                                           # max
        fast = self.c._draw_narration_gap(2)
        self.c.rng = random.Random(1)
        self.play(75)                                            # slow
        slow = self.c._draw_narration_gap(2)
        self.assertAlmostEqual(slow / fast, SC["tempo_gap_factor"]["slow"] / SC["tempo_gap_factor"]["max"], places=3)

    def test_a_speed_up_gets_its_line(self):
        self.c.scene = tracker(self.clock, STROKES)
        self.heat(15.0)
        self.c._narration_due = self.clock.t + 1000
        self.c._last_heard = 0.0
        said = []
        for pos in range(60, 100):
            self.clock.t += 1
            self.play(pos)
            line = self.c.scene_moment()
            if line:
                said.append((pos, line))
        faster = [x for v in SC["seed"]["faster"].values() for x in v]
        self.assertTrue(any(line in faster for _, line in said), said)
        self.assertGreaterEqual(self.said[-1][1], 5)            # read sharp

    def test_a_cum_gets_a_line_at_once_outside_the_gap(self):
        self.heat(15.0)
        self.c._narration_due = self.clock.t + 1000               # the regular gap is far away
        self.c._last_line = self.clock.t - 60
        self.c._last_heard = 0.0
        self.play(130)
        line = self.c.scene_moment()
        self.assertIn(line, [x for v in SC["seed"]["cum_soon"].values() for x in v])
        self.clock.t += 10
        self.play(140.5)
        line = self.c.scene_moment()                              # 10 s after her last line: over cum_gap (8 s)
        self.assertIn(line, [x for v in SC["seed"]["cum_mouth"].values() for x in v])

    def test_after_any_line_a_pause_of_60_to_90_s_before_the_next(self):
        self.c.persona_cfg["narration"]["min_gap_seconds"] = [60, 90]
        self.heat(40.0)                                          # the top stage: the shortest bank gaps
        self.c._last_heard = 0.0
        start, times = self.clock.t, []
        for t in range(900):
            self.clock.t = start + t
            self.play(200)                                       # nothing new on screen
            if self.c.scene_moment() or self.c.idle_narration():
                times.append(t)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreater(len(gaps), 5)
        self.assertGreaterEqual(min(gaps), 60)
        self.assertLessEqual(max(gaps), 91)
        self.assertGreater(len(set(gaps)), 2)                     # drawn anew each time

    def test_a_cum_waits_only_the_lower_end_of_the_pause(self):
        self.c.persona_cfg["narration"]["min_gap_seconds"] = [60, 90]
        self.heat(15.0)
        self.c._last_heard = 0.0
        self.c._narration_due = self.clock.t + 1000
        self.c._paused(self.clock.t - 50)                        # her last line 50 s ago
        self.c._pause_until = self.clock.t + 1000
        self.play(140.5)
        self.assertIsNone(self.c.scene_moment())                 # not yet: under a minute
        self.clock.t += 11
        self.play(141.5)
        self.assertIn(self.c.scene_moment(), [x for v in SC["seed"]["cum_mouth"].values() for x in v])
        self.assertGreaterEqual(self.c._pause_until - self.clock.t, 60)    # and the pause starts again after it

    def test_a_key_moment_waits_for_the_user_and_for_her_own_voice(self):
        self.heat(15.0)
        self.c._last_line = self.clock.t - 60
        self.c._last_heard = self.clock.t - 3                     # he has just spoken
        self.play(130)
        self.assertIsNone(self.c.scene_moment())
        self.c._last_heard = 0.0
        self.c.busy = lambda: True                                # she is talking
        self.assertIsNone(self.c.scene_moment())
        self.c.busy = lambda: False
        self.assertIsNotNone(self.c.scene_moment())

    def run_film(self, seconds=330, step=1):
        out = []
        for pos in range(60, 60 + seconds, step):
            self.clock.t += step
            self.play(pos % 330 + 0.5)
            line = self.c.scene_moment()
            if line:
                out.append((self.clock.t, line))
        return out

    def test_most_moments_pass_without_a_word(self):
        sc = self.c.persona_cfg["narration"]["scene"]
        sc.update(event_chance={"cum": 0.7, "cum_soon": 0.35, "act_change": 0.3, "tempo": 0.2}, scene_min_gap=75)
        self.c.scene = tracker(self.clock, STROKES)
        self.heat(15.0)
        self.c._last_heard = 0.0
        counts = []
        for seed in range(12):
            self.c.rng = random.Random(seed)
            self.c._last_line = self.c._last_scene_line = -1e9
            counts.append(len(self.run_film(990)))               # the film three times over (seeks back)
        self.assertLess(sum(counts) / len(counts), 6)            # 3 x (2 acts + tempo + cum_soon + cum) = 15 moments
        self.assertGreater(sum(counts), 0)

    def test_lines_about_the_video_are_spaced(self):
        self.c.persona_cfg["narration"]["scene"]["scene_min_gap"] = 75
        self.c.scene = tracker(self.clock, STROKES)
        self.heat(15.0)
        self.c._last_heard = 0.0
        times = [t for t, _ in self.run_film(990)]
        self.assertGreater(len(times), 2)
        self.assertTrue(all(b - a >= 75 for a, b in zip(times, times[1:])), times)

    def test_a_moment_does_not_push_the_bank_back(self):
        self.heat(15.0)
        self.c._last_line, self.c._last_heard = self.clock.t - 60, 0.0
        self.c._narration_due = self.clock.t + 20
        self.play(130)
        self.assertIsNotNone(self.c.scene_moment())
        self.assertEqual(self.c._narration_due, self.clock.t + 20)

    def test_moment_lines_also_come_from_the_themes_of_the_act(self):
        sc = self.c.persona_cfg["narration"]["scene"]
        sc["theme_share"] = 1.0
        self.heat(40.0)                                           # stage 4: the cum themes are reached
        self.c._last_heard = 0.0
        self.c._last_line = self.clock.t - 60
        self.play(141)
        line = self.c.scene_moment()
        fits = {t.split(" - ")[0] for t in self.c._act_themes({"act": "cum_mouth"}, self.c.persona_cfg["narration"])}
        self.assertIn(line.split(" | ")[0], fits)

    def test_scene_lines_come_in_random_order(self):
        firsts = set()
        for seed in range(8):
            self.tearDown()
            self.setUp()                                          # a fresh pool each time
            self.c.rng = random.Random(seed)
            self.heat(15.0)
            self.c._last_line, self.c._last_heard = self.clock.t - 60, 0.0
            self.play(130)
            firsts.add(self.c.scene_moment())
        self.assertGreater(len(firsts), 2)

    def test_with_everything_spent_and_no_model_an_old_line_comes_back_after_three_hours(self):
        self.c.scene = None
        when = [1_800_000_000.0]
        themes = N["themes"]
        self.c.pool = LinePool(lambda *a: [], os.path.join(self.dir.name, "p2.json"), os.path.join(self.dir.name, "s2.jsonl"),
                               background=False, min_pool=0, clock=lambda: when[0],
                               seed={t: [f"{t.split(' - ')[0]} | jedyna."] for t in themes})
        for t in themes:
            self.c.pool.take(t)                                   # everything said

        def no_model(*a, **k):
            raise RuntimeError("no key")
        self.c.persona.narrate = no_model
        self.heat(15.0)

        def attempt():
            self.c._narration_due = self.clock.t
            self.c._last_line, self.c._last_heard = self.clock.t - 100, 0.0
            self.clock.t += 1
            return self.c.idle_narration()
        when[0] += 3600
        self.assertIsNone(attempt())                              # an hour later: silence
        when[0] += 2.5 * 3600
        line = attempt()
        self.assertTrue(line and line.endswith("| jedyna."), line)

    def test_a_spent_theme_gives_way_to_the_rest_of_the_bank(self):
        self.c.scene = None
        self.heat(15.0)
        cfg = self.c.persona_cfg["narration"]
        for t in cfg["themes"]:
            for key in self.c._theme_keys(t, 2):
                self.c.pool.pool[key] = []                          # every theme spent...
        last = [t for t in cfg["themes"] if self.c.theme_stage(t, cfg) <= 2][-1]
        self.c.pool.pool[last] = [f"{last.split(' - ')[0]} | ostatnia świeża."]   # ...but one
        self.c.persona.narrate = lambda *a, **k: self.fail("the model must wait until the bank is spent")
        self.c._narration_due = self.clock.t
        self.c._last_line, self.c._last_heard = self.clock.t - 100, 0.0
        self.assertTrue(self.c.idle_narration().endswith("ostatnia świeża."))

    def test_seeking_back_brings_new_lines_for_the_same_moment(self):
        self.heat(15.0)
        said = []
        for _ in range(3):                                          # the same cum three times over
            self.c._last_line = self.c._last_scene_line = self.clock.t - 100
            self.c._last_heard = 0.0
            self.play(100)
            self.clock.t += 1
            self.play(141)
            said.append(self.c.scene_moment())
        self.assertEqual(len(set(said)), 3, said)

    def test_no_scene_no_change(self):
        self.c.scene = None
        self.assertIsNone(self.c.scene_moment())
        self.assertIsNone(self.c._persona_state()["scena"])

    def test_the_model_sees_the_scene(self):
        self.play(70)
        scena = self.c._persona_state()["scena"]
        self.assertEqual(scena["akt"], SC["labels"]["oral"])
        self.assertIn("oralny", scena["opis"])

    def test_the_peak_is_never_whispered(self):
        self.heat(0.0)                                            # stage 0: mostly tender
        voices = []
        for _ in range(20):
            self.play(200)                                        # climax (no stroke script: the timeline says so)
            self.c._narration_due = self.clock.t
            self.c._last_line = self.clock.t - 100
            self.c._last_heard = 0.0
            self.c.idle_narration()
            voices.append(self.said[-1][1])                       # 0-4 tender, 5-9 sharp
            self.clock.t += 1
        self.assertTrue(all(v >= 5 for v in voices), voices)


if __name__ == "__main__":
    unittest.main()


class FilmFromScripts(unittest.TestCase):
    """2026-11-20: a Stash stream ("http://.../scene/414/stream") never found its timeline - the scripts name the film"""

    def test_the_shared_stem_of_the_scripts(self):
        from scene import film_from_scripts
        names = ["TmwVRNet.Adult.Movies.Come.To.Life.Elena.Vedem.rear.funscript",
                 "TmwVRNet.Adult.Movies.Come.To.Life.Elena.Vedem.rear_width.funscript",
                 "TmwVRNet.Adult.Movies.Come.To.Life.Elena.Vedem.suck_speed.funscript",
                 "TmwVRNet.Adult.Movies.Come.To.Life.Elena.Vedem.valve.funscript"]
        self.assertEqual(film_from_scripts(names), "TmwVRNet.Adult.Movies.Come.To.Life.Elena.Vedem")
        self.assertIsNone(film_from_scripts([]))

    def test_a_stream_packet_is_looked_up_by_the_film(self):
        from scene import SceneTracker
        seen = []
        t = SceneTracker(loader=lambda p: seen.append(p) or None, strokes_loader=lambda p: {})
        t.handle_packet(json.dumps({"event": "media", "path": "http://192.168.0.108:9999/scene/414/stream", "position": 10,
                                    "duration": 1900, "playing": True, "scripts": ["Film.X.rear.funscript", "Film.X.valve.funscript"]}))
        self.assertEqual(seen, ["Film.X.mp4"])
        self.assertEqual(t.progress()[1], 1900)
