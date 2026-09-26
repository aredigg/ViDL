# pyright: standard
import io
import os
import pty
import termios
import threading
import unittest
from queue import Queue
from unittest import mock

from tests.helpers import IsolatedTestCase
from vidl.ansi import ANSI
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


class TestTerminalNonInteractive(IsolatedTestCase):
    def test_alternate_screen_without_input_thread(self):
        stdout = self.quiet_console()
        with Terminal(Queue()):
            self.assertEqual(input_threads(), [])
            self.assertIn(ANSI.Alternate.Enter, stdout.getvalue())
        self.assertTrue(stdout.getvalue().endswith(ANSI.Alternate.Leave))


class TestTerminalInteractive(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.quiet_console()
        master, slave = pty.openpty()
        self.master = master
        self.stdin = os.fdopen(slave, "r")
        self.addCleanup(os.close, master)
        self.addCleanup(self.stdin.close)
        patcher = mock.patch("sys.stdin", self.stdin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keys_are_queued_and_terminal_restored(self):
        before = terminal_mode(self.stdin.fileno())
        queue: Queue[str] = Queue()
        with Terminal(queue):
            self.assertEqual(len(input_threads()), 1)
            cbreak = termios.tcgetattr(self.stdin.fileno())
            self.assertFalse(cbreak[3] & termios.ICANON)
            os.write(self.master, b"q")
            self.assertEqual(queue.get(timeout=5), "q")
        self.assertEqual(input_threads(), [])
        self.assertEqual(terminal_mode(self.stdin.fileno()), before)

    def test_non_tty_stdin_is_not_read(self):
        with mock.patch("sys.stdin", io.StringIO("q")):
            queue: Queue[str] = Queue()
            with Terminal(queue):
                self.assertEqual(input_threads(), [])
            self.assertTrue(queue.empty())


if __name__ == "__main__":
    unittest.main()
