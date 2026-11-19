"""Escalation over time: the session heats up while he only listens (escalation.per_minute), and the narration themes
follow the stage (narration.theme_stages): only themes of the stage reached so far, mostly the harshest of them."""
import copy
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_manners import CFG, P, Server  # noqa: E402

E = P["escalation"]
N = P["narration"]


class OverTime(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)
        self.say("kochanie, jestem twoja", request="none")      # a session is on

    def minutes(self, m):
        for _ in range(int(m * 6)):
            self.clock.t += 10
            self.c.stage()

    def test_the_top_stage_in_about_ten_minutes_of_silence(self):
        self.assertLessEqual(self.c.stage(), 1)
        self.minutes(5)
        self.assertIn(self.c.stage(), (2, 3))
        self.minutes(5.5)
        self.assertEqual(self.c.stage(), 4)

    def test_no_heat_from_time_once_the_session_is_over(self):
        ini = P["initiative"]
        window = float(ini["session_seconds"]) + float(ini["idle_prompt_seconds"]) * (int(ini["max_nudges"]) + 1)
        self.minutes(window / 60 + 0.5)                          # he said nothing: the session runs out
        heat = self.c._heat
        self.minutes(3)
        self.assertEqual(self.c._heat, heat)                     # no more heat from time...
        self.assertEqual(self.c.stage(), 4)                      # ...but the stage reached holds for a while

    def test_every_theme_has_a_stage(self):
        for lang_cfg in (N, P["languages"]["en"]["narration"]):
            cfg = dict(N, **lang_cfg) if lang_cfg is not N else N
            named = {n.lower() for v in cfg["theme_stages"].values() for n in v}
            names = {t.split(" - ")[0].strip().lower() for t in cfg["themes"]}
            self.assertTrue(named <= names, named - names)
            self.assertGreater(len(named), 0.9 * len(names))


class ThemesFollowTheStage(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)

    def draw(self, stage, n=600):
        cfg = self.c.persona_cfg["narration"]
        got = Counter()
        for _ in range(n):
            t = self.c.rng.choice(self.c._narration_themes(cfg, False, stage))
            self.c._themes_recent.append(t)
            got[self.c.theme_stage(t, cfg)] += 1
        return got

    def test_stage_zero_only_the_gentle_themes(self):
        self.assertEqual(set(self.draw(0)), {0})

    def test_the_top_stage_is_mostly_the_harshest_themes(self):
        got = self.draw(4)
        self.assertGreater(got[4] / sum(got.values()), 0.5)
        self.assertGreater(got[3] + got[4], 0.75 * sum(got.values()))
        self.assertGreater(got[0], 0)                              # tender still comes up now and then, as a contrast

    def test_the_stage_is_known_for_a_theme(self):
        cfg = self.c.persona_cfg["narration"]
        bydlo = [t for t in cfg["themes"] if t.startswith("bydło")][0]
        self.assertEqual(self.c.theme_stage(bydlo, cfg), 4)
        pochwala = [t for t in cfg["themes"] if t.startswith("pochwała")][0]
        self.assertEqual(self.c.theme_stage(pochwala, cfg), 0)


if __name__ == "__main__":
    unittest.main()


class HarsherVersions(Server):
    """a theme from an early stage gets its harsher version (narration.pool.seed_up) once the session has gone further"""

    def setUp(self):
        super().setUp()
        import tempfile
        from lines import LinePool, escalated_seed, level_key
        self.level_key = level_key
        self.c.persona_cfg = copy.deepcopy(P)
        n = self.c.persona_cfg["narration"]
        self.theme = [t for t in n["themes"] if t.startswith("pochwała")][0]        # stage 0
        labels = n["levels"]
        seed = {self.theme: ["Grzeczna jesteś dziś.", "Ładnie klęczysz, kochanie."]}
        seed.update(escalated_seed(n["themes"], {"pochwała": {"1": ["Dobra dziwka, szeroko rozłożona."], "2": ["Najlepsza dziura w całym domu."]}}, labels))
        d = tempfile.mkdtemp()
        self.c.pool = LinePool(lambda *a: [], os.path.join(d, "p.json"), os.path.join(d, "s.jsonl"), background=False, seed=seed)
        self.labels = labels

    def test_the_level_follows_the_distance_between_the_stages(self):
        self.assertEqual(self.c._level_of(self.theme, 0), 0)
        self.assertEqual(self.c._level_of(self.theme, 1), 1)
        self.assertEqual(self.c._level_of(self.theme, 4), 2)
        self.assertEqual(self.c._level_of(self.theme, 4, soft=True), 0)        # winding down: never harsher
        bydlo = [t for t in self.c.persona_cfg["narration"]["themes"] if t.startswith("bydło")][0]
        self.assertEqual(self.c._level_of(bydlo, 4), 0)                        # a top theme is already the top

    def test_the_harshest_line_first_then_milder_ones(self):
        self.assertEqual(self.c._take_line(self.theme, 4)[0], "Najlepsza dziura w całym domu.")
        self.assertEqual(self.c._take_line(self.theme, 4)[0], "Dobra dziwka, szeroko rozłożona.")   # the top ran out: one down
        self.assertEqual(self.c._take_line(self.theme, 4)[0], "Grzeczna jesteś dziś.")
        self.assertEqual(self.c._take_line(self.theme, 0)[0], "Ładnie klęczysz, kochanie.")

    def test_every_harsher_seed_names_a_real_theme(self):
        for cfg in (P["narration"], dict(P["narration"], **P["languages"]["en"]["narration"])):
            names = {t.split(" - ")[0].strip().lower() for t in cfg["themes"]}
            for name, levels in cfg["pool"].get("seed_up", {}).items():
                self.assertIn(name.lower(), names, name)
                self.assertTrue(set(levels) <= set(cfg["levels"]), name)
                for lines in levels.values():
                    for x in lines:
                        self.assertNotIn("?", x)
                        self.assertLessEqual(len(x.split()), 30, x)
