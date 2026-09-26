# pyright: standard
import os
import sys
import threading
import time
import unittest
from unittest import mock

from tests.helpers import IsolatedTestCase, private
from vidl.ansi import ANSI
from vidl.config import Config
from vidl.debug import Debug


class TestDebug(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.temp_dir()
        self.log = os.path.join(self.directory, "debug.log")
        Config.settings["Debug"]["file_name"] = self.log
        hooks = (sys.excepthook, threading.excepthook)

        def restore_hooks() -> None:
            sys.excepthook, threading.excepthook = hooks

        self.addCleanup(restore_hooks)

    def run_debug(self, messages: list[tuple[int, str]], pause: float = 0) -> None:
        Debug.activate()
        try:
            for slot, message in messages:
                Debug.print(slot, message)
                if pause:
                    time.sleep(pause)
        finally:
            Debug.deactivate()

    def files(self) -> list[str]:
        names = sorted(
            f for f in os.listdir(self.directory) if f.startswith("debug.log")
        )
        contents = []
        for name in names:
            with open(os.path.join(self.directory, name), encoding="utf-8") as f:
                contents.append(f.read())
        return contents

    def test_print_is_ignored_when_inactive(self):
        Debug.print(0, "nothing")
        self.assertFalse(os.path.exists(self.log))

    def test_messages_are_written_with_prefixes(self):
        self.run_debug(
            [(-1, "general"), (0, f"{ANSI.Bold}slot one{ANSI.Reset}"), (1, "")]
        )
        (content,) = self.files()
        lines = content.splitlines()
        self.assertTrue(lines[0].endswith("UTC > === Begin ==="))
        self.assertTrue(lines[1].endswith("UTC > general"))
        self.assertTrue(lines[2].endswith("UTC > SLOT-01 > slot one"))
        self.assertTrue(lines[-1].endswith("=== End ==="))
        self.assertIn("=== Inactive ===", content)
        self.assertNotIn("\x1b", content)
        self.assertEqual(len(lines), 5)  # empty messages are skipped

    def test_log_is_appended(self):
        self.run_debug([(0, "first")])
        self.run_debug([(0, "second")])
        (content,) = self.files()
        self.assertEqual(content.count("=== Begin ==="), 2)

    def test_messages_queued_before_deactivation_are_written(self):
        self.run_debug([(0, f"message {i:03}") for i in range(200)])
        (content,) = self.files()
        for i in range(200):
            self.assertIn(f"message {i:03}", content)

    def test_unicode_is_written_as_utf8(self):
        self.run_debug([(0, "日本語 𓃥 ✅")])
        self.assertIn("日本語 𓃥 ✅", self.files()[0])

    def test_rotation_keeps_each_message_once(self):
        Config.settings["Debug"]["wrap_size"] = 0
        with mock.patch.object(Debug, "BUFFER_SIZE", 200):
            self.run_debug(
                [(0, f"message {i:03} " + "x" * 40) for i in range(40)], 0.005
            )
        contents = self.files()
        self.assertGreater(len(contents), 1)
        for content in contents:
            self.assertEqual(content.count("=== Begin ==="), 1)
            self.assertEqual(content.count("=== End ==="), 1)
        everything = "".join(contents)
        for i in range(40):
            self.assertEqual(everything.count(f"message {i:03} "), 1)

    def test_wrap_move_shifts_rotated_files(self):
        for suffix, text in (("", "current"), (".000", "zero"), (".001", "one")):
            with open(self.log + suffix, "w") as f:
                f.write(text)
        private(Debug, Debug, "wrap_move")(self.log)
        self.assertFalse(os.path.exists(self.log))
        for suffix, text in ((".000", "current"), (".001", "zero"), (".002", "one")):
            with open(self.log + suffix) as f:
                self.assertEqual(f.read(), text)

    def test_wrap_move_drops_the_oldest_at_the_limit(self):
        for suffix in ("", ".000", ".001", ".002"):
            with open(self.log + suffix, "w") as f:
                f.write(suffix or "current")
        with mock.patch.object(Debug, "MAX_ROTATIONS", 3):
            private(Debug, Debug, "wrap_move")(self.log)
        self.assertEqual(
            sorted(os.listdir(self.directory)),
            ["debug.log.000", "debug.log.001", "debug.log.002"],
        )
        with open(self.log + ".002") as f:
            self.assertEqual(f.read(), ".001")

    def test_uncaught_exceptions_are_logged(self):
        Debug.activate()
        try:
            try:
                raise ValueError("main thread failure")
            except ValueError as e:
                sys.excepthook(type(e), e, e.__traceback__)

            def fail() -> None:
                raise RuntimeError("thread failure")

            thread = threading.Thread(target=fail)
            thread.start()
            thread.join()
        finally:
            Debug.deactivate()
        content = self.files()[0]
        self.assertIn("ValueError: main thread failure", content)
        self.assertIn("RuntimeError: thread failure", content)
        self.assertIn("Traceback", content)


if __name__ == "__main__":
    unittest.main()
