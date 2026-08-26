import sys
import termios
import tty
from queue import Queue
from select import select
from shutil import get_terminal_size as size
from threading import Event, Thread
from types import TracebackType

from .ansi import ANSI


class Terminal:
    def __init__(self, queue: Queue[str]) -> None:
        self.__width, self.__height = size()
        self.__queue: Queue[str] = queue
        self.__halt_event: Event = Event()
        self.__thread = Thread(target=self.__read_input, name="Terminal-input")
        self.__thread.start()
        self.__fd = None
        self.__old_termios = None
        self.__interactive = sys.stdin.isatty() and sys.stdout.isatty()

    def __enter__(self):
        self.__fd = sys.stdin.fileno()
        try:
            self.__old_termios = termios.tcgetattr(self.__fd)
            _ = tty.setcbreak(self.__fd)
        except termios.error:
            self.__interactive = False
        print(ANSI.Alternate.Enter, end="")
        _ = sys.stdout.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ):
        self.__halt_event.set()
        print(ANSI.Alternate.Leave, end="")
        _ = sys.stdout.flush()
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
        _ = sys.stdout.flush()

    def __read_input(self):
        while not self.__halt_event.is_set():
            if (select([sys.stdin], [], [], 0.2)[0]) and (char := sys.stdin.read(1)):
                self.__queue.put(char)

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

    def flush(self):
        _ = sys.stdout.flush()
