import sys
import termios
import tty

from .ansi import ANSI


class Terminal:
    def __init__(self, input_queue) -> None:
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
