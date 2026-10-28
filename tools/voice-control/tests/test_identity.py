"""Her identity (persona.identity): who she is, who he is, her playbook and the controls, in front of every prompt."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from persona import IDENTITY_SECTIONS, build_nudge_prompt, build_system_prompt, render_character  # noqa: E402
from voice_control import load_config  # noqa: E402

CFG, INTENTS = load_config(os.path.join(os.path.dirname(__file__), "..", "commands.json"))
P = CFG["persona"]


class Identity(unittest.TestCase):
    def test_every_section_is_filled_in_the_config(self):
        for key, _ in IDENTITY_SECTIONS:
            self.assertTrue(P["identity"].get(key, "").strip(), key)

    def test_rendered_in_order_then_the_core(self):
        text = render_character(P)
        positions = [text.index(title) for _, title in IDENTITY_SECTIONS]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], text.index(P["character"][:30]))

    def test_what_he_likes_is_in_there(self):
        text = render_character(P)
        for word in ("księżniczk", "misi", "sissy", "braną jako kobieta", "uległ", "Lube Launcher", "koniec"):
            self.assertIn(word, text, word)

    def test_without_identity_the_core_alone(self):
        self.assertEqual(render_character({"character": " x "}), "x")
        self.assertEqual(render_character({}), "")

    def test_in_both_prompts(self):
        text = render_character(P)
        self.assertIn("KIM JESTEŚ", build_system_prompt(text, INTENTS, ["faster"], {}))
        self.assertIn("TWÓJ REPERTUAR", build_nudge_prompt(text, {}))

    def test_the_new_names_are_hers(self):
        self.assertIn("księżniczko", P["address"]["names"])
        self.assertIn("misiu", P["address"]["names"])


if __name__ == "__main__":
    unittest.main()
