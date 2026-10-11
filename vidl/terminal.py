import os
import sys
import termios
import tty
from queue import Queue
from select import select
from shutil import get_terminal_size as size
from threading import Event, Thread
from types import TracebackType

from .ansi import ANSI
from .keyboard import PASTE_OFF, PASTE_ON, Key, KeyDecoder
from .message import InputMessage, Message


class Terminal:
    INPUT_POLL_INTERVAL: float = 0.2
    READ_SIZE: int = 1024

    def __init__(self, queue: Queue[Message]) -> None:
        self.__width, self.__height = size()
        self.__queue: Queue[Message] = queue
        self.__halt_event: Event = Event()
        self.__thread: Thread | None = None
        self.__fd: int | None = None
        self.__old_termios = None
        self.__cursor_visible = False
        # Keyboard input is only read when stdin is a terminal
        self.__interactive = sys.stdin is not None and sys.stdin.isatty()

    def __enter__(self):
        if self.__interactive:
            fd = self.__fd = sys.stdin.fileno()
            try:
                self.__old_termios = termios.tcgetattr(fd)
                _ = tty.setcbreak(fd)
            except termios.error:
                self.__interactive = False
        if self.__interactive:
            self.__thread = Thread(target=self.__read_input, name="Terminal-input")
            self.__thread.start()
        print(ANSI.Alternate.Enter, end="")
        if self.__interactive:
            # Pasted text arrives as one key, so it is never taken as commands
            print(PASTE_ON, end="")
        _ = sys.stdout.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ):
        self.__halt_event.set()
        if self.__thread is not None:
            self.__thread.join()
        if self.__interactive:
            print(PASTE_OFF, end="")
        print(ANSI.Alternate.Leave, end="")
        _ = sys.stdout.flush()
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
        _ = sys.stdout.flush()

    # Reads raw bytes, so a key sequence or a paste is never left behind in a
    # Python buffer while select() reports nothing to read
    def __read_input(self):
        fd = self.__fd
        if fd is None:
            return
        decoder = KeyDecoder()
        while not self.__halt_event.is_set():
            # A lone ESC is only known to be the Escape key after a pause
            timeout = (
                KeyDecoder.ESCAPE_TIMEOUT
                if decoder.pending()
                else Terminal.INPUT_POLL_INTERVAL
            )
            try:
                readable = select([fd], [], [], timeout)[0]
            except (OSError, ValueError):
                return
            if readable:
                try:
                    data = os.read(fd, Terminal.READ_SIZE)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                if not data:
                    # EOF, stdin will stay readable and never block again
                    self.__put(decoder.flush(force=True))
                    return
                self.__put(decoder.feed(data))
            else:
                self.__put(decoder.flush())

    def __put(self, keys: list[Key]) -> None:
        if keys:
            self.__queue.put(InputMessage(keys=tuple(keys)))

    def get_size(self):
        self.__width, self.__height = size()
        return self.__height, self.__width

    def clear(self):
        print(ANSI.ClearScreen)

    def print(self, string: str, row: int, col: int):
        if not (1 <= row <= self.__height and 1 <= col <= self.__width):
            return
        remaining_space = self.__width - col + 1
        if ANSI.len(string) > remaining_space:
            string = ANSI.trim(string, remaining_space)
        print(ANSI.print(string, row, col), end="")

    # Shows the cursor at the position, or hides it with None
    def set_cursor(self, position: tuple[int, int] | None):
        if position is None:
            if self.__cursor_visible:
                print(ANSI.CursorHide, end="")
                self.__cursor_visible = False
            return
        row, col = position
        if not (1 <= row <= self.__height and 1 <= col <= self.__width):
            self.set_cursor(None)
            return
        print(ANSI.print("", row, col), end="")
        if not self.__cursor_visible:
            print(ANSI.CursorShow, end="")
            self.__cursor_visible = True

    def flush(self):
        _ = sys.stdout.flush()
