"""What she calls her in the narration: pet names and insults by stage and tone, a reminder of who she is."""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_manners import P, Server  # noqa: E402

A = P["narration"]["address"]


def words(tone, stages):
    lists = A["sharp" if tone == "ostry" else "tender"]
    return {w for s in stages for w in lists[str(s)]}


class Address(Server):
    def setUp(self):
        super().setUp()
        self.c.persona_cfg = copy.deepcopy(P)
        self.a = self.c.persona_cfg["narration"]["address"]
        self.a.update(share=[1.0] * 5, identity_share=[0.0] * 5, contrast_share=0.0)
        self.c.rng = random.Random(3)

    def test_harsh_words_at_the_top_tender_at_the_start(self):
        top = words("ostry", (3, 4))
        for _ in range(50):
            line, added = self.c._addressed("Bierze cię mocno i głęboko.", "ostry", 4)
            self.assertIn(added, top)
            self.assertIn(added.lower(), line.lower())
        start = words("czuły", (0,))
        for _ in range(50):
            _, added = self.c._addressed("Bierze cię powoli.", "czuły", 0)
            self.assertIn(added, start)

    def test_in_front_or_at_the_end(self):
        seen = set()
        for _ in range(60):
            line, added = self.c._addressed("Jesteś pełna.", "ostry", 4)
            if line.startswith(added[:1].upper() + added[1:] + ", "):
                self.assertTrue(line.endswith(", jesteś pełna."), line)
                seen.add("front")
            else:
                self.assertEqual(line, "Jesteś pełna, " + added + ".")
                seen.add("end")
        self.assertEqual(seen, {"front", "end"})

    def test_ellipsis_and_no_full_stop(self):
        self.a["front_share"] = 0.0
        line, added = self.c._addressed("Teraz… zaraz…", "ostry", 4)
        self.assertEqual(line, "Teraz… zaraz, " + added + "…")
        line, added = self.c._addressed("Ssij głębiej", "ostry", 4)
        self.assertEqual(line, "Ssij głębiej, " + added + ".")

    def test_left_alone(self):
        for text in ("Ssij, kochanie, powoli.", "Powtórz za mną: jestem ich.", "Jesteś moja, analeno, ty dziwko."):
            self.assertEqual(self.c._addressed(text, "ostry", 4), (text, None))
        self.assertEqual(self.c._addressed("Dostajesz wszystko.", "ostry", 4, event="cum"), ("Dostajesz wszystko.", None))
        self.a["enabled"] = False
        self.assertEqual(self.c._addressed("Dostajesz wszystko.", "ostry", 4), ("Dostajesz wszystko.", None))

    def test_who_she_is(self):
        self.a.update(share=[0.0] * 5, identity_share=[1.0] * 5)
        line, added = self.c._addressed("Bierze cię mocno", "ostry", 4)
        self.assertIn(added, A["identity"]["sharp"]["3"] + A["identity"]["sharp"]["4"])
        self.assertEqual(line, "Bierze cię mocno. " + added)

    def test_the_cooldown_is_tender(self):
        mild = words("czuły", (0, 1))
        for _ in range(40):
            _, added = self.c._addressed("Oddychaj spokojnie.", "ostry", 4, soft=True)
            self.assertIn(added, mild)

    def test_contrast_and_no_quick_repeats(self):
        self.a["contrast_share"] = 1.0
        _, added = self.c._addressed("Bierze cię mocno.", "ostry", 4)
        self.assertIn(added, words("czuły", range(5)))            # a pet name in a harsh line
        self.a["contrast_share"] = 0.0
        got = [self.c._addressed("Bierze cię mocno.", "ostry", 4)[1] for _ in range(6)]
        self.assertEqual(len(set(got)), 6)

    def test_a_word_or_a_reminder_never_both(self):
        self.a.update(identity_share=[1.0] * 5)
        line, added = self.c._addressed("Bierze cię mocno.", "ostry", 4)
        self.assertNotIn(" + ", added)

    def test_not_every_line(self):
        self.a.update(share=[0.5] * 5)
        got = [self.c._addressed("Bierze cię mocno.", "ostry", 4)[1] for _ in range(200)]
        self.assertTrue(40 < sum(1 for g in got if g) < 160)

    def test_the_words_are_clean(self):
        from lines import is_banned_line
        banned = P["narration"]["pool"]["banned_words"]
        for lang in (A, P["languages"]["en"]["narration"]["address"]):
            for group in ("tender", "sharp"):
                for ws in lang[group].values():
                    for w in ws:
                        self.assertFalse(is_banned_line(w, banned), w)
            for group in lang["identity"].values():
                for ws in group.values():
                    for w in ws:
                        self.assertFalse(is_banned_line(w, banned), w)
                        self.assertNotIn("?", w)
                        self.assertLessEqual(len(w.split()), 12)


if __name__ == "__main__":
    unittest.main()
