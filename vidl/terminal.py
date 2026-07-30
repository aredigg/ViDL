import sys
import termios
import tty
from shutil import get_terminal_size as size

from .ansi import ANSI


class Terminal:
    def __init__(self, input_queue) -> None:
        self.__width, self.__height = size()
        self.__input_queue = input_queue
        self.__fd = None
        self.__old_termios = None

    def __enter__(self):
        self.__fd = sys.stdin.fileno()
        self.__old_termios = termios.tcgetattr(self.__fd)
        tty.setcbreak(self.__fd)
        print(ANSI.Alternate.Enter, end="")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        print(ANSI.Alternate.Leave, end="")
        sys.stdout.flush()
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
        sys.stdout.flush()

    def get_size(self):
        self.__width, self.__height = size()
        return self.__height, self.__width

    def print(self, string, row, col):
        if 1 > row > self.__height or 1 > col > self.__width:
            return
        remaining_space = self.__width - col
        if ANSI.len(string) > remaining_space:
            string = ANSI.trim(string, remaining_space)
        print(ANSI.print(string, row, col))
