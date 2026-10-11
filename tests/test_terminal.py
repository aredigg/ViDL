# pyright: standard
import io
import os
import pty
import termios
import threading
import time
import unittest
from queue import Queue
from unittest import mock

from tests.helpers import IsolatedTestCase
from vidl.ansi import ANSI
from vidl.keyboard import PASTE_OFF, PASTE_ON, Key
from vidl.message import InputMessage, Message
from vidl.terminal import Terminal


def input_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "Terminal-input"]


def terminal_mode(fd: int) -> list:
    # PENDIN is maintained by the kernel, and may change on its own
    mode = termios.tcgetattr(fd)
    mode[3] &= ~getattr(termios, "PENDIN", 0)
    return mode


class TestTerminalOutput(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = self.quiet_console(cols=20, rows=5)
        self.terminal = Terminal(Queue())

    def output(self) -> str:
        return self.stdout.getvalue()

    def test_size_is_rows_then_columns(self):
        self.assertEqual(self.terminal.get_size(), (5, 20))

    def test_print_positions_the_cursor(self):
        self.terminal.print("hello", 2, 3)
        self.assertEqual(self.output(), ANSI.print("hello", 2, 3))

    def test_print_outside_the_screen_is_ignored(self):
        for row, col in ((0, 1), (6, 1), (1, 0), (1, 21)):
            self.terminal.print("x", row, col)
        self.assertEqual(self.output(), "")

    def test_print_is_trimmed_to_the_screen_width(self):
        self.terminal.print("0123456789", 1, 15)
        self.assertEqual(ANSI.remove(self.output()), "012345")

    def test_clear_and_flush(self):
        self.terminal.clear()
        self.terminal.flush()
        self.assertTrue(self.output().startswith(ANSI.ClearScreen))

    def test_cursor_is_shown_at_the_position_and_hidden(self):
        self.terminal.set_cursor((2, 3))
        self.assertEqual(self.output(), ANSI.print("", 2, 3) + ANSI.CursorShow)
        self.stdout.truncate(0)
        self.stdout.seek(0)
        self.terminal.set_cursor((4, 5))  # already shown, only moved
        self.assertEqual(self.output(), ANSI.print("", 4, 5))
        self.stdout.truncate(0)
        self.stdout.seek(0)
        self.terminal.set_cursor(None)
        self.assertEqual(self.output(), ANSI.CursorHide)
        self.stdout.truncate(0)
        self.stdout.seek(0)
        self.terminal.set_cursor(None)  # already hidden
        self.assertEqual(self.output(), "")

    def test_cursor_outside_the_screen_is_hidden(self):
        self.terminal.set_cursor((2, 3))
        self.terminal.set_cursor((6, 1))
        self.assertTrue(self.output().endswith(ANSI.CursorHide))


class TestTerminalNonInteractive(IsolatedTestCase):
    def test_alternate_screen_without_input_thread(self):
        stdout = self.quiet_console()
        with Terminal(Queue()):
            self.assertEqual(input_threads(), [])
            self.assertIn(ANSI.Alternate.Enter, stdout.getvalue())
        self.assertTrue(stdout.getvalue().endswith(ANSI.Alternate.Leave))
        self.assertNotIn(PASTE_ON, stdout.getvalue())


class TestTerminalInteractive(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = self.quiet_console()
        master, slave = pty.openpty()
        self.master = master
        self.stdin = os.fdopen(slave, "r")
        self.addCleanup(os.close, master)
        self.addCleanup(self.stdin.close)
        patcher = mock.patch("sys.stdin", self.stdin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def keys(self, queue: Queue[Message], count: int) -> list[Key]:
        keys: list[Key] = []
        while len(keys) < count:
            message = queue.get(timeout=5)
            assert isinstance(message, InputMessage)
            keys.extend(message.keys)
        return keys

    def test_keys_are_queued_and_terminal_restored(self):
        before = terminal_mode(self.stdin.fileno())
        queue: Queue[Message] = Queue()
        with Terminal(queue):
            self.assertEqual(len(input_threads()), 1)
            cbreak = termios.tcgetattr(self.stdin.fileno())
            self.assertFalse(cbreak[3] & termios.ICANON)
            os.write(self.master, b"q")
            self.assertEqual(self.keys(queue, 1), [Key("char", "q")])
        self.assertEqual(input_threads(), [])
        self.assertEqual(terminal_mode(self.stdin.fileno()), before)

    def test_bracketed_paste_is_enabled_while_running(self):
        with Terminal(Queue()):
            self.assertIn(PASTE_ON, self.stdout.getvalue())
            self.assertNotIn(PASTE_OFF, self.stdout.getvalue())
        output = self.stdout.getvalue()
        self.assertLess(output.index(PASTE_OFF), output.index(ANSI.Alternate.Leave))

    def test_sequences_and_utf8_are_decoded(self):
        queue: Queue[Message] = Queue()
        with Terminal(queue):
            # Written in parts, as a slow terminal may deliver them
            for part in (b"\x1b[", b"A", "é".encode()[:1], "é".encode()[1:]):
                os.write(self.master, part)
                time.sleep(0.01)
            self.assertEqual(self.keys(queue, 2), [Key("up"), Key("char", "é")])

    def test_a_lone_escape_is_the_escape_key(self):
        queue: Queue[Message] = Queue()
        with Terminal(queue):
            os.write(self.master, b"\x1b")
            self.assertEqual(self.keys(queue, 1), [Key("escape")])

    def test_a_paste_is_one_key(self):
        queue: Queue[Message] = Queue()
        with Terminal(queue):
            os.write(self.master, b"\x1b[200~https://x/q\x1b[201~")
            self.assertEqual(self.keys(queue, 1), [Key("paste", "https://x/q")])

    def test_many_keys_at_once_are_not_left_behind(self):
        queue: Queue[Message] = Queue()
        with Terminal(queue):
            os.write(self.master, b"abc")
            self.assertEqual(
                self.keys(queue, 3),
                [Key("char", "a"), Key("char", "b"), Key("char", "c")],
            )

    def test_non_tty_stdin_is_not_read(self):
        with mock.patch("sys.stdin", io.StringIO("q")):
            queue: Queue[Message] = Queue()
            with Terminal(queue):
                self.assertEqual(input_threads(), [])
            self.assertTrue(queue.empty())


if __name__ == "__main__":
    unittest.main()
