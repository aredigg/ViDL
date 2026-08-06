import sys
import termios
import tty
from select import select
from shutil import get_terminal_size as size
from threading import Event, Thread

from .ansi import ANSI


class Terminal:
    def __init__(self, queue) -> None:
        self.__width, self.__height = size()
        self.__queue = queue
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__read_input, name="Terminal-input")
        self.__thread.start()
        self.__fd = None
        self.__old_termios = None
        self.__interactive = sys.stdin.isatty() and sys.stdout.isatty()

    def __enter__(self):
        self.__fd = sys.stdin.fileno()
        try:
            self.__old_termios = termios.tcgetattr(self.__fd)
            tty.setcbreak(self.__fd)
        except termios.error:
            self.__interactive = False
        print(ANSI.Alternate.Enter, end="")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.__halt_event.set()
        print(ANSI.Alternate.Leave, end="")
        sys.stdout.flush()
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
        sys.stdout.flush()

    def __read_input(self):
        while not self.__halt_event.is_set():
            if select([sys.stdin], [], [], 0.2)[0]:
                if char := sys.stdin.read(1):
                    self.__queue.put(char)

    def get_size(self):
        self.__width, self.__height = size()
        return self.__height, self.__width

    def clear(self):
        print(ANSI.ClearScreen)

    def print(self, string, row, col):
        if not (1 <= row <= self.__height and 1 <= col <= self.__width):
            return
        remaining_space = self.__width - col + 1
        if ANSI.len(string) > remaining_space:
            string = ANSI.trim(string, remaining_space)
        print(ANSI.print(string, row, col), end="")

    def flush(self):
        sys.stdout.flush()
