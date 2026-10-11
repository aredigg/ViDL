from collections.abc import Callable
from enum import Enum, auto

from .command import AddChannelCommand, Command, DrainCommand, QuitCommand
from .keyboard import Key, LineEditor


class Controls:
    """Turns keys into commands: single key shortcuts, or a text prompt.

    Pasted text never runs shortcuts, it is only used by a prompt."""

    HINTS: str = "q quit · c finish downloads and quit · a add channel"
    ADD_LABEL: str = "Add channel"
    PROMPT_HINT: str = "enter add · esc cancel"

    class Mode(Enum):
        COMMAND = auto()
        TEXT = auto()

    def __init__(self, send: Callable[[Command], None]) -> None:
        self.__send = send
        self.__mode = Controls.Mode.COMMAND
        self.__editor: LineEditor | None = None
        self.__label = ""

    def mode(self) -> "Controls.Mode":
        return self.__mode

    # The label and editor of an open prompt
    def prompt(self) -> tuple[str, LineEditor] | None:
        if self.__mode is Controls.Mode.TEXT and self.__editor is not None:
            return self.__label, self.__editor
        return None

    def handle(self, key: Key) -> None:
        if self.__mode is Controls.Mode.TEXT:
            self.__text(key)
        else:
            self.__command(key)

    def __command(self, key: Key) -> None:
        if key.name == "char":
            match key.text:
                case "q" | "Q":
                    self.__send(QuitCommand())
                case "c" | "C":
                    self.__send(DrainCommand())
                case "a" | "A":
                    self.__open(Controls.ADD_LABEL)
                case _:
                    pass
        elif key.name == "ctrl" and not key.alt and key.text in ("c", "d"):
            # Ctrl-C only arrives as a key when the terminal does not signal it
            self.__send(QuitCommand())

    def __text(self, key: Key) -> None:
        if self.__editor is None:
            self.__close()
            return
        match self.__editor.handle(key):
            case LineEditor.Result.SUBMIT:
                text = self.__editor.text.strip()
                self.__close()
                if text:
                    self.__send(AddChannelCommand(text))
            case LineEditor.Result.CANCEL:
                self.__close()
            case LineEditor.Result.CONTINUE:
                pass

    def __open(self, label: str) -> None:
        self.__mode = Controls.Mode.TEXT
        self.__editor = LineEditor()
        self.__label = label

    def __close(self) -> None:
        self.__mode = Controls.Mode.COMMAND
        self.__editor = None
        self.__label = ""
