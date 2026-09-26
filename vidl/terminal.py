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
    INPUT_POLL_INTERVAL: float = 0.2

    def __init__(self, queue: Queue[str]) -> None:
        self.__width, self.__height = size()
        self.__queue: Queue[str] = queue
        self.__halt_event: Event = Event()
        self.__thread: Thread | None = None
        self.__fd: int | None = None
        self.__old_termios = None
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
        print(ANSI.Alternate.Leave, end="")
        _ = sys.stdout.flush()
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
        _ = sys.stdout.flush()

    def __read_input(self):
        while not self.__halt_event.is_set():
            if select([sys.stdin], [], [], Terminal.INPUT_POLL_INTERVAL)[0]:
                if not (char := sys.stdin.read(1)):
                    return  # EOF, stdin will stay readable and never block again
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
