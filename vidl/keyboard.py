"""Decodes raw terminal input into keys, and edits a single line of text.

Pure logic without threads or I/O: the terminal feeds the bytes it reads to a
KeyDecoder, and the resulting keys drive the commands or a LineEditor prompt.
"""

import codecs
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import ClassVar

from .unicode import Unicode

PASTE_ON: str = "\x1b[?2004h"  # enable bracketed paste
PASTE_OFF: str = "\x1b[?2004l"


def is_printable(char: str) -> bool:
    """Whether a single character may be inserted and shown as text.

    Rejects controls (C0, DEL, C1), surrogates, line and paragraph separators,
    noncharacters and format characters (e.g. bidirectional overrides), except
    the zero width (non-)joiner and tag characters used by emoji sequences.
    Unassigned code points are accepted, as they may be emoji newer than the
    Unicode database of this Python version."""
    if len(char) != 1:
        return False
    code = ord(char)
    if 0xFDD0 <= code <= 0xFDEF or code & 0xFFFE == 0xFFFE:
        return False
    category = unicodedata.category(char)
    if category == "Cf":
        return code in (0x200C, 0x200D) or 0xE0020 <= code <= 0xE007F
    return category not in ("Cc", "Cs", "Zl", "Zp")


@dataclass(frozen=True)
class Key:
    """A decoded key press.

    name is one of "char", "paste", "enter", "tab", "escape", "backspace",
    "delete", "insert", "up", "down", "left", "right", "home", "end",
    "page_up", "page_down", "ctrl" (text is the lowercase letter), "alt"
    (text is the character typed with Alt/Meta), or "unknown" (text is the
    raw unrecognised input, to be ignored)."""

    name: str
    text: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


class _State(Enum):
    TEXT = auto()
    ESCAPE = auto()  # after ESC
    CSI = auto()  # after ESC [
    SS3 = auto()  # after ESC O
    SKIP = auto()  # discarding the rest of an abandoned, overlong sequence


class KeyDecoder:
    """Turns terminal input bytes into keys.

    Escape sequences, UTF-8 characters and bracketed pastes may be split
    across feeds. A lone ESC can only be told apart from the start of a
    sequence by waiting: while pending() is True, flush() should be called
    once ESCAPE_TIMEOUT has passed without new input.

    Invalid UTF-8 is decoded as U+FFFD, which is emitted as a "char" key (it
    is printable, so the user sees that something was not understood)."""

    ESCAPE_TIMEOUT: float = 0.05
    MAX_PASTE: int = 65536  # characters kept from one bracketed paste
    MAX_SEQUENCE: int = 32  # longer escape sequences are abandoned

    __ESC: str = "\x1b"
    __PASTE_START: str = "\x1b[200~"
    __PASTE_END: str = "\x1b[201~"
    __LETTERS: ClassVar[dict[str, str]] = {
        "A": "up",
        "B": "down",
        "C": "right",
        "D": "left",
        "H": "home",
        "F": "end",
    }
    __RXVT_LETTERS: ClassVar[dict[str, str]] = {
        "a": "up",
        "b": "down",
        "c": "right",
        "d": "left",
    }
    __TILDE: ClassVar[dict[str, str]] = {
        "1": "home",
        "2": "insert",
        "3": "delete",
        "4": "end",
        "5": "page_up",
        "6": "page_down",
        "7": "home",
        "8": "end",
    }
    # rxvt ends editing key sequences with these instead of "~" when modified
    __RXVT_FINALS: ClassVar[dict[str, tuple[bool, bool]]] = {
        "$": (False, True),  # (ctrl, shift)
        "^": (True, False),
        "@": (True, True),
    }

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.__clock: Callable[[], float] = clock
        self.__decoder: codecs.IncrementalDecoder = codecs.getincrementaldecoder(
            "utf-8"
        )(errors="replace")
        self.__last_feed: float = float("-inf")
        self.__state: _State = _State.TEXT
        self.__sequence: str = ""  # the escape sequence being read
        self.__after_cr: bool = False  # a following "\n" completes "\r\n"
        self.__pasting: bool = False
        self.__paste: list[str] = []
        self.__paste_length: int = 0
        self.__paste_cr: bool = False

    def feed(self, data: bytes) -> list[Key]:
        if not data:
            return []
        now = self.__clock()
        if now - self.__last_feed > KeyDecoder.ESCAPE_TIMEOUT:
            # A "\n" long after a "\r" is a separate key press (Ctrl+J)
            self.__after_cr = False
        self.__last_feed = now
        keys: list[Key] = []
        for char in self.__decoder.decode(data):
            while not self.__consume(char, keys):
                pass  # the state changed, the character is processed again
        return keys

    def pending(self) -> bool:
        return not self.__pasting and self.__state is not _State.TEXT

    def flush(self, force: bool = False) -> list[Key]:
        """Ends a pending escape sequence once ESCAPE_TIMEOUT has passed since
        the last feed: a lone ESC becomes "escape", a lone "ESC [" or "ESC O"
        becomes Alt+[ or Alt+O, and other incomplete sequences "unknown".
        A bracketed paste in progress is never ended."""
        keys: list[Key] = []
        if self.pending() and (
            force or self.__clock() - self.__last_feed >= KeyDecoder.ESCAPE_TIMEOUT
        ):
            self.__interrupt(keys)
        return keys

    def __consume(self, char: str, keys: list[Key]) -> bool:
        """Processes one character, returns False when it has to be processed
        again in the new state."""
        match self.__state:
            case _State.TEXT:
                if char == KeyDecoder.__ESC:
                    self.__state, self.__sequence = _State.ESCAPE, char
                    self.__after_cr = self.__paste_cr = False
                elif self.__pasting:
                    self.__paste_char(char)
                else:
                    self.__text_char(char, keys)
                return True
            case _State.ESCAPE:
                return self.__escape_char(char, keys)
            case _State.CSI | _State.SS3:
                return self.__sequence_char(char, keys)
            case _State.SKIP:
                code = ord(char)
                if 0x20 <= code <= 0x3F:
                    return True
                self.__state = _State.TEXT
                return 0x40 <= code <= 0x7E  # the final byte ends the sequence

    def __escape_char(self, char: str, keys: list[Key]) -> bool:
        if char == "[":
            self.__state, self.__sequence = _State.CSI, self.__sequence + char
            return True
        if char == "O":
            self.__state, self.__sequence = _State.SS3, self.__sequence + char
            return True
        self.__state, self.__sequence = _State.TEXT, ""
        if self.__pasting:
            # ESC and a printable character are a two character sequence,
            # a control character is processed on its own
            return is_printable(char)
        if char == KeyDecoder.__ESC:
            keys.append(Key("escape"))
            return False  # the second ESC starts a new sequence
        key = KeyDecoder.__key(char)
        if key.name == "char":
            keys.append(Key("alt", char))
        elif key.name == "unknown":
            keys.append(Key("unknown", KeyDecoder.__ESC + char))
        else:
            keys.append(replace(key, alt=True))
            self.__after_cr = char == "\r"
        return True

    def __sequence_char(self, char: str, keys: list[Key]) -> bool:
        code = ord(char)
        sequence = self.__sequence + char
        if self.__state is _State.CSI:
            if self.__sequence == "\x1b[" and char == "[":
                # Linux console function keys, ESC [ [ and a letter
                self.__sequence = sequence
                return True
            if self.__sequence == "\x1b[[":
                if 0x40 <= code <= 0x7E:
                    self.__complete(sequence, keys)
                    return True
                self.__interrupt(keys)
                return False
            body = self.__sequence[2:]
            if char in KeyDecoder.__RXVT_FINALS and body.isdigit():
                self.__complete(sequence, keys)
                return True
            continues = 0x20 <= code <= 0x3F
        else:
            continues = 0x30 <= code <= 0x3B  # digits and ";" only
        if continues:
            self.__sequence = sequence
            if len(sequence) >= KeyDecoder.MAX_SEQUENCE:
                # It can only end longer than MAX_SEQUENCE, so it is abandoned
                if not self.__pasting:
                    keys.append(Key("unknown", sequence))
                self.__state, self.__sequence = _State.SKIP, ""
            return True
        if 0x40 <= code <= 0x7E:
            self.__complete(sequence, keys)
            return True
        self.__interrupt(keys)
        return False

    def __interrupt(self, keys: list[Key]) -> None:
        """Ends an incomplete escape sequence."""
        sequence = self.__sequence
        state = self.__state
        self.__state, self.__sequence = _State.TEXT, ""
        if self.__pasting or state is _State.SKIP:
            return
        if sequence == KeyDecoder.__ESC:
            keys.append(Key("escape"))
        elif sequence in ("\x1b[", "\x1bO"):
            keys.append(Key("alt", sequence[1]))
        else:
            keys.append(Key("unknown", sequence))

    def __complete(self, sequence: str, keys: list[Key]) -> None:
        self.__state, self.__sequence = _State.TEXT, ""
        if self.__pasting:
            if sequence == KeyDecoder.__PASTE_END:
                keys.append(Key("paste", "".join(self.__paste)))
                self.__pasting = False
                self.__paste, self.__paste_length = [], 0
            return  # other sequences are removed from the pasted text
        if sequence == KeyDecoder.__PASTE_START:
            self.__pasting, self.__paste_cr = True, False
            self.__paste, self.__paste_length = [], 0
            return
        keys.append(KeyDecoder.__parse(sequence))

    def __text_char(self, char: str, keys: list[Key]) -> None:
        after_cr = self.__after_cr
        self.__after_cr = char == "\r"
        if not (char == "\n" and after_cr):
            keys.append(KeyDecoder.__key(char))

    def __paste_char(self, char: str) -> None:
        after_cr = self.__paste_cr
        self.__paste_cr = char == "\r"
        if char == "\n" and after_cr:
            return
        if char in ("\r", "\n", "\t"):
            char = " "
        elif not is_printable(char):
            return
        if self.__paste_length < KeyDecoder.MAX_PASTE:
            self.__paste.append(char)
            self.__paste_length += 1

    @staticmethod
    def __key(char: str) -> Key:
        """The key for a single character outside an escape sequence."""
        code = ord(char)
        if char in ("\r", "\n"):
            return Key("enter")
        if char == "\t":
            return Key("tab")
        if char in ("\x7f", "\x08"):
            return Key("backspace")
        if 0x01 <= code <= 0x1A:
            return Key("ctrl", chr(code + 0x60))
        if is_printable(char):
            return Key("char", char)
        return Key("unknown", char)

    @staticmethod
    def __parse(sequence: str) -> Key:
        """The key for a complete CSI or SS3 sequence."""
        unknown = Key("unknown", sequence)
        body, final = sequence[2:-1], sequence[-1]
        if body.strip("0123456789;"):
            return unknown  # private parameters, intermediates, or ESC [ [
        params = body.split(";")
        if sequence[1] == "O":
            if len(params) == 1 and final in KeyDecoder.__LETTERS:
                return KeyDecoder.__modified(
                    KeyDecoder.__LETTERS[final], params[0], unknown
                )
            if not body and final in KeyDecoder.__RXVT_LETTERS:
                return Key(KeyDecoder.__RXVT_LETTERS[final], ctrl=True)
            if not body and final == "M":
                return Key("enter")  # keypad enter
            return unknown
        modifier = params[1] if len(params) == 2 else ""
        if final in KeyDecoder.__LETTERS or final == "Z":
            if len(params) > 2 or params[0] not in ("", "1"):
                return unknown
            if final == "Z":
                key = KeyDecoder.__modified("tab", modifier, unknown)
                return replace(key, shift=True) if key.name == "tab" else key
            return KeyDecoder.__modified(KeyDecoder.__LETTERS[final], modifier, unknown)
        if not body and final in KeyDecoder.__RXVT_LETTERS:
            return Key(KeyDecoder.__RXVT_LETTERS[final], shift=True)
        if len(params) > 2 or params[0] not in KeyDecoder.__TILDE:
            return unknown
        if final == "~":
            return KeyDecoder.__modified(
                KeyDecoder.__TILDE[params[0]], modifier, unknown
            )
        if final in KeyDecoder.__RXVT_FINALS and len(params) == 1:
            ctrl, shift = KeyDecoder.__RXVT_FINALS[final]
            return Key(KeyDecoder.__TILDE[params[0]], ctrl=ctrl, shift=shift)
        return unknown

    @staticmethod
    def __modified(name: str, modifier: str, unknown: Key) -> Key:
        """The key with the flags of an xterm modifier parameter, which is 1
        plus a bitmask of shift (1), alt (2), ctrl (4) and meta (8)."""
        if not modifier:
            return Key(name)
        value = int(modifier)
        if not 1 <= value <= 16:
            return unknown
        bits = value - 1
        return Key(name, ctrl=bool(bits & 4), alt=bool(bits & 10), shift=bool(bits & 1))


class LineEditor:
    """Edits a single line of text, readline style.

    The cursor never separates a character from the zero width characters
    (e.g. combining marks) following it, which are edited as one unit.
    Words for Alt+B/F/D, Alt+Backspace and Ctrl/Alt+Left/Right/Delete are
    runs of alphanumeric characters, while Ctrl+W deletes back to whitespace,
    as in readline."""

    MAX_LENGTH: int = 2048

    class Result(Enum):
        CONTINUE = auto()
        SUBMIT = auto()
        CANCEL = auto()

    def __init__(self, text: str = "", max_length: int = MAX_LENGTH) -> None:
        self.__max_length: int = max(0, max_length)
        self.__text: str = ""
        self.__cursor: int = 0
        self.__scroll: int = -1  # first character shown, -1 before rendering
        self.__insert(text)

    @property
    def text(self) -> str:
        return self.__text

    @property
    def cursor(self) -> int:
        return self.__cursor

    def handle(self, key: Key) -> "LineEditor.Result":
        cursor = self.__cursor
        word = key.ctrl or key.alt
        match key.name:
            case "enter":
                return LineEditor.Result.SUBMIT
            case "escape":
                return LineEditor.Result.CANCEL
            case "char" | "paste":
                self.__insert(key.text)
            case "backspace":
                self.__delete(self.__word_left(cursor) if word else self.__prev(cursor))
            case "delete":
                self.__delete(
                    self.__word_right(cursor) if word else self.__next(cursor)
                )
            case "left":
                self.__cursor = (
                    self.__word_left(cursor) if word else self.__prev(cursor)
                )
            case "right":
                self.__cursor = (
                    self.__word_right(cursor) if word else self.__next(cursor)
                )
            case "home":
                self.__cursor = 0
            case "end":
                self.__cursor = len(self.__text)
            case "ctrl" if not key.alt:
                return self.__control(key.text)
            case "alt":
                self.__alt(key.text)
            case _:
                pass
        return LineEditor.Result.CONTINUE

    def render(self, width: int) -> tuple[str, int]:
        """The part of the text to show in width columns, and the column of
        the cursor in it.

        The view only scrolls when the cursor leaves it: past the right edge
        the cursor is put a quarter of the width from the left, so typing at
        the end does not scroll on every key; when the cursor reaches the
        left edge with text hidden before it, it is put a quarter of the
        width from the right, and free space after the end of the text is
        filled. Characters are never split, the cursor character is shown
        when it fits, and the view starts at 0 whenever the text fits."""
        if width < 1:
            return "", 0
        text, cursor, end = self.__text, self.__cursor, len(self.__text)
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + Unicode.len(char))

        def columns(start: int, stop: int) -> int:
            return offsets[stop] - offsets[start]

        # The cursor cell shows the character under it, or is empty at the end
        cell = max(1, columns(cursor, self.__next(cursor)))
        tail = 1 if cursor == end else 0
        context = max(1, width // 4)

        def anchor(target: int) -> int:
            """The scroll showing at least target columns before the cursor,
            as far as the cursor cell stays visible."""
            position = cursor
            while position > 0 and columns(position, cursor) < target:
                previous = self.__prev(position)
                if columns(previous, cursor) + cell > width:
                    break
                position = previous
            return position

        scroll = min(self.__scroll, end)
        while scroll > 0 and not self.__boundary(scroll):
            scroll -= 1
        if (
            scroll < 0
            or scroll > cursor
            or (scroll > 0 and columns(scroll, cursor) <= 0)
        ):
            scroll = anchor(width - cell - context)
            while scroll > 0 and columns(self.__prev(scroll), end) + tail <= width:
                scroll = self.__prev(scroll)
        elif columns(scroll, cursor) + cell > width:
            scroll = anchor(context)
        if columns(0, end) + tail <= width:
            scroll = 0
        self.__scroll = scroll

        stop = scroll
        while stop < end and columns(scroll, stop + 1) <= width:
            stop += 1
        return text[scroll:stop], columns(scroll, cursor)

    def __control(self, letter: str) -> "LineEditor.Result":
        cursor = self.__cursor
        match letter:
            case "c" | "g":
                return LineEditor.Result.CANCEL
            case "a":
                self.__cursor = 0
            case "e":
                self.__cursor = len(self.__text)
            case "b":
                self.__cursor = self.__prev(cursor)
            case "f":
                self.__cursor = self.__next(cursor)
            case "d":
                self.__delete(self.__next(cursor))
            case "u":
                self.__delete(0)
            case "k":
                self.__delete(len(self.__text))
            case "w":
                position = cursor
                while position > 0 and self.__space(self.__prev(position)):
                    position = self.__prev(position)
                while position > 0 and not self.__space(self.__prev(position)):
                    position = self.__prev(position)
                self.__delete(position)
            case _:
                pass
        return LineEditor.Result.CONTINUE

    def __alt(self, char: str) -> None:
        match char:
            case "b":
                self.__cursor = self.__word_left(self.__cursor)
            case "f":
                self.__cursor = self.__word_right(self.__cursor)
            case "d":
                self.__delete(self.__word_right(self.__cursor))
            case _:
                pass

    def __insert(self, string: str) -> None:
        string = "".join(char for char in string if is_printable(char))
        room = self.__max_length - len(self.__text)
        if len(string) > room:
            # Drop whole characters, with their combining marks
            cut = max(0, room)
            while cut > 0 and Unicode.len(string[cut]) == 0:
                cut -= 1
            string = string[:cut]
        if string:
            text, cursor = self.__text, self.__cursor
            self.__text = text[:cursor] + string + text[cursor:]
            cursor += len(string)
            # Marks without a base at the start now belong to the inserted text
            while not self.__boundary(cursor):
                cursor += 1
            self.__cursor = cursor

    def __delete(self, position: int) -> None:
        """Deletes between the cursor and position, on either side of it."""
        start, stop = sorted((self.__cursor, position))
        self.__text = self.__text[:start] + self.__text[stop:]
        self.__cursor = start

    def __boundary(self, position: int) -> bool:
        """Whether the cursor may be at position, i.e. it is not followed by
        a zero width character."""
        text = self.__text
        return position <= 0 or position >= len(text) or Unicode.len(text[position]) > 0

    def __prev(self, position: int) -> int:
        position -= 1
        while position > 0 and not self.__boundary(position):
            position -= 1
        return max(0, position)

    def __next(self, position: int) -> int:
        end = len(self.__text)
        position += 1
        while position < end and not self.__boundary(position):
            position += 1
        return min(end, position)

    def __word(self, position: int) -> bool:
        return self.__text[position].isalnum()

    def __space(self, position: int) -> bool:
        return self.__text[position].isspace()

    def __word_left(self, position: int) -> int:
        while position > 0 and not self.__word(self.__prev(position)):
            position = self.__prev(position)
        while position > 0 and self.__word(self.__prev(position)):
            position = self.__prev(position)
        return position

    def __word_right(self, position: int) -> int:
        end = len(self.__text)
        while position < end and not self.__word(position):
            position = self.__next(position)
        while position < end and self.__word(position):
            position = self.__next(position)
        return position
