# pyright: standard
import unittest

from vidl.command import AddChannelCommand, Command, DrainCommand, QuitCommand
from vidl.controls import Controls
from vidl.keyboard import Key

Mode = Controls.Mode


class TestControls(unittest.TestCase):
    def setUp(self):
        self.sent: list[Command] = []
        self.controls = Controls(self.sent.append)

    def press(self, *keys: Key) -> None:
        for key in keys:
            self.controls.handle(key)

    def type(self, text: str) -> None:
        self.press(*(Key("char", c) for c in text))

    def test_shortcuts(self):
        cases = (
            (Key("char", "q"), QuitCommand()),
            (Key("char", "Q"), QuitCommand()),
            (Key("ctrl", "c"), QuitCommand()),
            (Key("ctrl", "d"), QuitCommand()),
            (Key("char", "c"), DrainCommand()),
            (Key("char", "C"), DrainCommand()),
        )
        for key, command in cases:
            with self.subTest(key=key):
                self.sent.clear()
                self.press(key)
                self.assertEqual(self.sent, [command])
                self.assertIs(self.controls.mode(), Mode.COMMAND)

    def test_other_keys_are_ignored(self):
        self.press(
            Key("char", "x"),
            Key("enter"),
            Key("escape"),
            Key("up"),
            Key("alt", "q"),
            Key("ctrl", "c", alt=True),
            Key("unknown", "\x1b[99~"),
        )
        self.assertEqual(self.sent, [])
        self.assertIs(self.controls.mode(), Mode.COMMAND)

    def test_pasted_text_never_runs_shortcuts(self):
        self.press(Key("paste", "qca"))
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.controls.prompt())

    def test_add_channel_prompt(self):
        self.press(Key("char", "a"))
        self.assertIs(self.controls.mode(), Mode.TEXT)
        prompt = self.controls.prompt()
        assert prompt is not None
        self.assertEqual(prompt[0], Controls.ADD_LABEL)
        # Shortcut keys are text while the prompt is open
        self.type("https://q.c/a")
        self.assertEqual(self.sent, [])
        self.press(Key("enter"))
        self.assertEqual(self.sent, [AddChannelCommand("https://q.c/a")])
        self.assertIs(self.controls.mode(), Mode.COMMAND)
        self.assertIsNone(self.controls.prompt())

    def test_pasted_url_in_the_prompt(self):
        self.press(Key("char", "A"), Key("paste", "  https://x/y  "), Key("enter"))
        self.assertEqual(self.sent, [AddChannelCommand("https://x/y")])

    def test_cancel_sends_nothing(self):
        for cancel in (Key("escape"), Key("ctrl", "c"), Key("ctrl", "g")):
            with self.subTest(cancel=cancel):
                self.press(Key("char", "a"))
                self.type("abc")
                self.press(cancel)
                self.assertEqual(self.sent, [])
                self.assertIs(self.controls.mode(), Mode.COMMAND)

    def test_empty_input_sends_nothing(self):
        self.press(Key("char", "a"), Key("char", " "), Key("enter"))
        self.assertEqual(self.sent, [])
        self.assertIs(self.controls.mode(), Mode.COMMAND)

    def test_ctrl_d_edits_in_the_prompt(self):
        self.press(Key("char", "a"))
        self.type("ab")
        self.press(Key("home"), Key("ctrl", "d"), Key("enter"))
        self.assertEqual(self.sent, [AddChannelCommand("b")])

    def test_a_new_prompt_starts_empty(self):
        self.press(Key("char", "a"))
        self.type("old")
        self.press(Key("escape"), Key("char", "a"))
        prompt = self.controls.prompt()
        assert prompt is not None
        self.assertEqual(prompt[1].text, "")


if __name__ == "__main__":
    unittest.main()
