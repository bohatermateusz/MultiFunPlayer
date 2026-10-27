"""Contract between commands.json / executor.py and the C# side (ButtplugOutputTarget.cs,
ScriptViewModel.cs): every action the voice tool can send must exist in MultiFunPlayer with the
same number and kind of arguments, and must pass the remote-command allow-list. Runs without
Windows: it reads the C# sources as text."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HERE = os.path.dirname(os.path.abspath(__file__))
MFP = os.path.normpath(os.path.join(HERE, "..", "..", "..", "Source", "MultiFunPlayer"))
VIEWMODELS = [
    os.path.join(MFP, "OutputTarget", "ViewModels", "ButtplugOutputTarget.cs"),
    os.path.join(MFP, "OutputTarget", "ViewModels", "ButtplugOutputTarget.HismithModes.cs"),
    os.path.join(MFP, "UI", "Controls", "ViewModels", "ScriptViewModel.cs"),
]
TARGET = "Buttplug.io/0"

# s.RegisterAction<bool>($"{Identifier}::Hismith::ManualMode::Set", ...)  /  s.RegisterAction("Script::Loop::Toggle", ...)
REGISTER_RE = re.compile(r'RegisterAction(?:<([^>]*)>)?\(\s*\$?"([^"]+)"')
CS_TO_JSON = {"bool": bool, "int": int, "double": (int, float), "float": (int, float), "string": str}


def registered_actions():
    """-> {action name: [argument type names]}"""
    actions = {}
    for path in VIEWMODELS:
        text = open(path, encoding="utf-8-sig").read()
        for generics, name in REGISTER_RE.findall(text):
            name = name.replace("{Identifier}", TARGET)
            types = [t.strip() for t in generics.split(",")] if generics else []
            actions[name] = types
    return actions


def allow_listed(name):
    # copy of ButtplugOutputTarget.IsRemoteActionAllowed
    return name.startswith(f"{TARGET}::Hismith::") or name.startswith("Script::Loop::") or name.startswith("Media::PlayPause::")


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actions = registered_actions()
        with open(os.path.join(HERE, "..", "commands.json"), encoding="utf-8") as f:
            cls.config = json.load(f)
        cls.assertTrue(cls, cls.actions, "no RegisterAction calls found")

    def test_sources_exist(self):
        for path in VIEWMODELS:
            self.assertTrue(os.path.isfile(path), path)

    def test_allow_list_source_unchanged(self):
        text = open(VIEWMODELS[0], encoding="utf-8-sig").read()
        for needle in ['$"{Identifier}::Hismith::"', '"Script::Loop::"', '"Media::PlayPause::"']:
            self.assertIn(needle, text, "IsRemoteActionAllowed changed; update allow_listed() in this test")

    def test_every_step_is_a_known_allowed_action_with_matching_arguments(self):
        for intent in self.config["intents"]:
            for step in intent["steps"]:
                name = step["action"].replace("{target}", TARGET)
                with self.subTest(intent=intent["id"], action=name):
                    self.assertIn(name, self.actions, "not registered in MultiFunPlayer")
                    self.assertTrue(allow_listed(name), "rejected by IsRemoteActionAllowed")
                    types = self.actions[name]
                    args = step.get("args", [])
                    self.assertEqual(len(args), len(types), f"argument count: C# wants {types}")
                    for value, cs_type in zip(args, types):
                        expected = CS_TO_JSON.get(cs_type)
                        self.assertIsNotNone(expected, f"unknown C# type {cs_type}")
                        self.assertIsInstance(value, expected, f"{value!r} is not a {cs_type}")
                        if cs_type == "int":
                            self.assertNotIsInstance(value, bool)

    def test_target_matches_default_identifier(self):
        self.assertEqual(self.config["target"], TARGET)

    def test_key_actions_are_present(self):
        for name in [f"{TARGET}::Hismith::LubeLauncher::Fire", f"{TARGET}::Hismith::Key::SinglePress",
                     f"{TARGET}::Hismith::Key::DoublePress", "Script::Loop::Toggle", "Media::PlayPause::Set",
                     f"{TARGET}::Hismith::Mode::Set", f"{TARGET}::Hismith::Speed::Faster", f"{TARGET}::Hismith::Depth::Deeper",
                     f"{TARGET}::Hismith::LubeLauncher::FireMany"]:
            self.assertIn(name, self.actions)


if __name__ == "__main__":
    unittest.main()
