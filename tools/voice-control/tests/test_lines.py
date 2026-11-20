"""The never-repeating line pool: filled in the background, filtered against everything ever said."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lines import LinePool, similar  # noqa: E402


class Pool(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.pool_path = os.path.join(self.tmp.name, "lines_pool.json")
        self.said_path = os.path.join(self.tmp.name, "said.jsonl")

        def generate(theme, avoid, count):
            self.calls.append((theme, list(avoid), count))
            base = len(self.calls) * 100
            words = ["Klęcz i ssij, mała", "Należysz do nich bez reszty", "Twoje nie już nic nie znaczy", "Jesteś moją dziwką na zawsze",
                     "Dziękuj za to, kim jesteś", "Odpuść, ja to trzymam", "Nikt cię stąd nie wypuści", "Zalewam cię, bo mogę"]
            out = [f"{words[(base + i) % len(words)]} {base + i}." for i in range(count)]
            return out + ["Czy to pytanie?", "x " * 40, out[0]]
        self.generate = generate

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        kw.setdefault("background", False)
        return LinePool(self.generate, self.pool_path, self.said_path, min_pool=3, batch=5, max_words=16, **kw)

    def test_take_fills_then_serves_fresh_lines_and_remembers_them(self):
        p = self.make()
        self.assertIsNone(p.take("A"))                           # empty: a refill is triggered (synchronous here)
        self.assertEqual(len(self.calls), 1)
        first = p.take("A")
        self.assertTrue(first.endswith("100."))
        self.assertIn(first, p.said)
        self.assertNotIn(first, p.pool["A"])
        with open(self.said_path, encoding="utf-8") as f:
            self.assertIn(first, f.read())

    def test_filters_questions_length_and_duplicates(self):
        p = self.make()
        p.refill("A")
        lines = p.pool["A"]
        self.assertEqual(len(lines), 5)                          # 5 good, question / too long / duplicate dropped
        self.assertFalse(any("?" in x for x in lines))

    def test_never_returns_something_said_before(self):
        p = self.make()
        p.refill("A")
        said = p.take("A")
        p2 = self.make()                                         # a new session: said.jsonl is loaded
        self.assertIn(said, p2.said)
        p2.pool["A"].insert(0, said)                             # even if it sneaks into the pool
        self.assertNotEqual(p2.take("A"), said)

    def test_avoid_list_is_handed_to_the_model(self):
        p = self.make()
        p.remember("Jesteś moja.")
        p.refill("B")
        self.assertIn("Jesteś moja.", self.calls[-1][1])

    def test_refill_when_low_and_not_twice_at_once(self):
        p = self.make()
        p.refill("A")
        for _ in range(3):
            p.take("A")
        self.assertGreaterEqual(len(self.calls), 2)              # dropped under min_pool -> refilled

    def test_a_failing_model_does_not_break_take(self):
        def boom(theme, avoid, count):
            raise RuntimeError("down")
        p = LinePool(boom, self.pool_path, self.said_path, background=False)
        self.assertIsNone(p.take("A"))
        self.assertIn("down", p.last_error)

    def test_near_duplicates_are_dropped(self):
        self.assertGreater(similar("Jesteś moja, mała.", "Jesteś moja mała"), 0.8)
        p = self.make()
        p.remember("Klęcz i ssij, mała 100.")
        p.refill("A")
        self.assertFalse(any(x.startswith("Klęcz i ssij, mała 100") for x in p.pool["A"]))

    def test_seed_fills_an_empty_pool_once(self):
        p = LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Jedna.", "Dwa razy.", "Jedna."]})
        self.assertEqual(p.pool["A"], ["Jedna.", "Dwa razy."])
        p.take("A")
        p2 = LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Jedna.", "Dwa razy."]})
        self.assertEqual(p2.pool["A"][0], "Dwa razy.")                # the saved pool wins over the seed (then refilled)
        self.assertTrue(p2.is_repeat("Jedna."))
        self.assertFalse(p2.is_repeat("Coś zupełnie innego."))

    def test_a_theme_added_later_gets_its_seed(self):
        p = LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Jedna.", "Dwa razy."]})
        p.take("A")
        p2 = LinePool(self.generate, self.pool_path, self.said_path, background=False,
                      seed={"A": ["Jedna.", "Dwa razy."], "B": ["Na posyłki.", "Z katalogu."]})
        self.assertEqual(p2.pool["B"], ["Na posyłki.", "Z katalogu."])     # the new theme starts from its seed
        self.assertNotIn("Jedna.", p2.pool["A"])                           # the old one keeps its saved pool

    def test_a_line_added_to_a_seed_joins_the_pool_and_keeps_its_length(self):
        long = "Kolejka do twojego gardła jest dłuższa niż do jakiejkolwiek innej. I nikt się nie wścieka na czekanie. Wiedzą, że warto."
        p = LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Jedna.", "Dwa razy."]})
        p.take("A")
        p2 = LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Jedna.", "Dwa razy.", long]})
        self.assertIn(long, p2.pool["A"])                                   # longer than max_words: hand-written, kept
        self.assertEqual(p2.pool["A"].count("Dwa razy."), 1)                # nothing doubled
        self.assertNotIn("Jedna.", p2.pool["A"])                            # already said: not again

    def test_a_rewritten_line_replaces_the_old_one_in_the_pool(self):
        LinePool(self.generate, self.pool_path, self.said_path, background=False, seed={"A": ["Robi się kolejkę.", "Dwa razy."]})
        p = LinePool(self.generate, self.pool_path, self.said_path, background=False,
                     seed={"A": ["Ustawia się kolejka.", "Dwa razy."]}, retired=["Robi się kolejkę."])
        self.assertNotIn("Robi się kolejkę.", p.pool["A"])
        self.assertIn("Ustawia się kolejka.", p.pool["A"])

    def test_banned_words_never_enter_the_pool(self):
        p = LinePool(lambda t, a, c: ["Maszyna cię bierze.", "On cię bierze.", "Włącz silnik.", "Oni czekają."],
                     self.pool_path, self.said_path, background=False, banned_words=["maszyn", "silnik"])
        p.refill("A")
        self.assertEqual(p.pool["A"], ["On cię bierze.", "Oni czekają."])
        self.assertTrue(p.banned("Twoja MASZYNA"))

    def test_background_fill(self):
        p = self.make(background=True)
        p.refill("A")
        for _ in range(50):
            if p.left("A"):
                break
            time.sleep(0.02)
        self.assertEqual(p.left("A"), 5)



class Recycle(unittest.TestCase):
    """When the fresh lines of a theme are spent (and the model wrote none), a line said more than 3 h ago may come
    back - never sooner, never while fresh ones are left."""
    LINES = ["Klęcz i ssij powoli.", "Należysz do nich bez reszty.", "Twoje nie już nic nie znaczy."]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [1_800_000_000.0]
        self.paths = (os.path.join(self.tmp.name, "p.json"), os.path.join(self.tmp.name, "s.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, seed=None):
        return LinePool(lambda *a: [], *self.paths, min_pool=0, background=False, clock=lambda: self.now[0],
                        seed=seed if seed is not None else {"A": list(self.LINES)})

    def spend(self, p):
        said = [p.take("A") for _ in self.LINES]
        self.assertIsNone(p.take("A"))
        return said

    def test_nothing_comes_back_while_fresh_lines_are_left(self):
        p = self.make()
        p.take("A")
        self.now[0] += 4 * 3600
        self.assertIsNone(p.recycle("A"))

    def test_a_line_comes_back_only_after_three_hours(self):
        p = self.make()
        said = self.spend(p)
        self.now[0] += 2.9 * 3600
        self.assertIsNone(p.recycle("A"))
        self.now[0] += 0.2 * 3600
        line = p.recycle("A")
        self.assertIn(line, said)
        self.assertNotIn(line, {p.recycle("A") for _ in range(5)})   # said again just now: not before 3 h more

    def test_the_history_survives_a_restart(self):
        self.spend(self.make())
        with open(self.paths[1], encoding="utf-8") as f:
            self.assertIn('"theme": "A"', f.read())
        self.now[0] += 3.5 * 3600
        p = self.make()
        self.assertIsNone(p.take("A"))
        self.assertIn(p.recycle("A"), self.LINES)

    def test_lines_said_before_the_themes_were_logged_come_back_by_their_seed(self):
        with open(self.paths[1], "w", encoding="utf-8") as f:
            for line in self.LINES:
                f.write(f'{{"time": "2020-01-01 10:00:00", "line": "{line}"}}\n')
        p = self.make()
        self.assertIsNone(p.take("A"))                           # all said long ago: nothing fresh
        self.assertIn(p.recycle("A"), self.LINES)

    def test_the_bank_starts_over_from_the_line_said_longest_ago(self):
        p = self.make({"A": ["Pierwsza kwestia tutaj.", "Druga zupełnie inna."], "B": ["Trzecia z innego tematu."]})
        for key in ("A", "A", "B"):
            p.take(key)
            self.now[0] += 600
        self.now[0] += 3 * 3600
        order = [p.recycle_any(["A", "B"])[0] for _ in range(3)]
        self.assertEqual(order, ["Pierwsza kwestia tutaj.", "Druga zupełnie inna.", "Trzecia z innego tematu."])
        self.assertEqual(p.recycle_any(["A", "B"]), (None, None))    # all said again just now: 3 h more

    def test_off_when_zero(self):
        p = LinePool(lambda *a: [], *self.paths, min_pool=0, background=False, clock=lambda: self.now[0],
                     seed={"A": list(self.LINES)}, recycle_hours=0)
        self.spend(p)
        self.now[0] += 10 * 3600
        self.assertIsNone(p.recycle("A"))


if __name__ == "__main__":
    unittest.main()
