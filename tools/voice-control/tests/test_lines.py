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
            words = ["Klęcz i ssij, mała", "Należysz do nich bez reszty", "Twoje nie już nic nie znaczy", "Jesteś moją suką na zawsze",
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

    def test_background_fill(self):
        p = self.make(background=True)
        p.refill("A")
        for _ in range(50):
            if p.left("A"):
                break
            time.sleep(0.02)
        self.assertEqual(p.left("A"), 5)


if __name__ == "__main__":
    unittest.main()
