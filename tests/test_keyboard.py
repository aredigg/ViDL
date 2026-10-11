# pyright: standard
import random
import unittest
from itertools import pairwise

from tests.helpers import private
from vidl.keyboard import (
    PASTE_OFF,
    PASTE_ON,
    Key,
    KeyDecoder,
    LineEditor,
    is_printable,
)
from vidl.unicode import Unicode

ESC = "\x1b"
START = "\x1b[200~"
END = "\x1b[201~"
NAMES = {
    "char",
    "paste",
    "enter",
    "tab",
    "escape",
    "backspace",
    "delete",
    "insert",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "page_up",
    "page_down",
    "ctrl",
    "alt",
    "unknown",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def decode(*chunks: str | bytes) -> list[Key]:
    """Feeds the chunks without time passing, then flushes."""
    decoder = KeyDecoder(FakeClock())
    keys: list[Key] = []
    for chunk in chunks:
        keys += decoder.feed(chunk.encode() if isinstance(chunk, str) else chunk)
    return keys + decoder.flush(force=True)


def chars(text: str) -> list[Key]:
    return [Key("char", c) for c in text]


class TestDecoderText(unittest.TestCase):
    def test_paste_mode_sequences(self):
        self.assertEqual(PASTE_ON, "\x1b[?2004h")
        self.assertEqual(PASTE_OFF, "\x1b[?2004l")

    def test_printable_characters(self):
        self.assertEqual(decode("aZ5 ~/"), chars("aZ5 ~/"))

    def test_multi_byte_characters(self):
        self.assertEqual(decode("é日😀Ж"), chars("é日😀Ж"))

    def test_combining_marks_are_separate_keys(self):
        self.assertEqual(decode("e\u0301"), chars("e\u0301"))

    def test_zero_width_joiner_is_a_char(self):
        self.assertEqual(decode("👨\u200d👩"), chars("👨\u200d👩"))

    def test_enter(self):
        self.assertEqual(decode("\r"), [Key("enter")])
        self.assertEqual(decode("\n"), [Key("enter")])
        self.assertEqual(decode("\r\n"), [Key("enter")])
        self.assertEqual(decode("\r\r"), [Key("enter")] * 2)
        self.assertEqual(decode("\n\r"), [Key("enter")] * 2)
        self.assertEqual(decode("\n\n"), [Key("enter")] * 2)
        self.assertEqual(decode("\r\n\n"), [Key("enter")] * 2)
        self.assertEqual(decode("\r\n\r\n"), [Key("enter")] * 2)
        self.assertEqual(
            decode("\ra\n"), [Key("enter"), Key("char", "a"), Key("enter")]
        )

    def test_cr_lf_split_across_feeds(self):
        self.assertEqual(decode("\r", "\n"), [Key("enter")])
        self.assertEqual(decode("a\r", "\nb"), [*chars("a"), Key("enter"), *chars("b")])

    def test_lf_long_after_cr_is_another_enter(self):
        clock = FakeClock()
        decoder = KeyDecoder(clock)
        self.assertEqual(decoder.feed(b"\r"), [Key("enter")])
        clock.now += 1
        self.assertEqual(decoder.feed(b"\n"), [Key("enter")])

    def test_sequence_between_cr_and_lf(self):
        self.assertEqual(decode("\r\x1b[A\n"), [Key("enter"), Key("up"), Key("enter")])

    def test_tab_and_backspace(self):
        self.assertEqual(decode("\t"), [Key("tab")])
        self.assertEqual(decode("\x7f"), [Key("backspace")])
        self.assertEqual(decode("\x08"), [Key("backspace")])

    def test_control_letters(self):
        special = {0x08, 0x09, 0x0A, 0x0D}
        for code in range(0x01, 0x1B):
            if code not in special:
                with self.subTest(code=code):
                    self.assertEqual(
                        decode(chr(code)), [Key("ctrl", chr(ord("a") + code - 1))]
                    )
        self.assertEqual(decode("\x03"), [Key("ctrl", "c")])
        self.assertEqual(decode("\x04"), [Key("ctrl", "d")])
        self.assertEqual(decode("\x15"), [Key("ctrl", "u")])
        self.assertEqual(decode("\x17"), [Key("ctrl", "w")])

    def test_other_c0_controls_are_unknown(self):
        for char in "\x00\x1c\x1d\x1e\x1f":
            with self.subTest(char=char):
                self.assertEqual(decode(char), [Key("unknown", char)])

    def test_c1_controls_are_unknown(self):
        for code in range(0x80, 0xA0):
            with self.subTest(code=code):
                self.assertEqual(decode(chr(code)), [Key("unknown", chr(code))])

    def test_other_non_printables_are_unknown(self):
        for char in ("\u2028", "\u2029", "\u202e", "\u2066", "\ufeff", "\u00ad"):
            with self.subTest(char=char):
                self.assertEqual(decode(char), [Key("unknown", char)])
        self.assertEqual(
            decode("\ufdd0\uffff"), [Key("unknown", "\ufdd0"), Key("unknown", "\uffff")]
        )

    def test_invalid_utf8_is_the_replacement_character(self):
        self.assertEqual(decode(b"\xff"), chars("\ufffd"))
        self.assertEqual(decode(b"a\xc3(b"), chars("a\ufffd(b"))
        self.assertEqual(decode(b"\xed\xa0\x80"), chars("\ufffd" * 3))  # surrogate

    def test_truncated_character_is_completed_by_the_next_feed(self):
        decoder = KeyDecoder(FakeClock())
        self.assertEqual(decoder.feed(b"\xc3"), [])
        self.assertFalse(decoder.pending())
        self.assertEqual(decoder.feed(b"\xa9"), chars("é"))
        self.assertEqual(decoder.feed(b"\xc3"), [])
        self.assertEqual(decoder.feed(b"a"), chars("\ufffda"))

    def test_empty_feed(self):
        decoder = KeyDecoder(FakeClock())
        self.assertEqual(decoder.feed(b""), [])
        self.assertFalse(decoder.pending())
        self.assertEqual(decoder.flush(force=True), [])

    def test_utf8_split_at_every_byte(self):
        for text in ("é", "日", "😀", "aé日😀b"):
            data = text.encode()
            for split in range(len(data) + 1):
                with self.subTest(text=text, split=split):
                    self.assertEqual(decode(data[:split], data[split:]), chars(text))
            with self.subTest(text=text, split="bytes"):
                self.assertEqual(decode(*(bytes([b]) for b in data)), chars(text))


class TestDecoderSequences(unittest.TestCase):
    def test_cursor_keys(self):
        expected = {
            "\x1b[A": "up",
            "\x1b[B": "down",
            "\x1b[C": "right",
            "\x1b[D": "left",
            "\x1b[H": "home",
            "\x1b[F": "end",
            "\x1b[1A": "up",
            "\x1bOA": "up",
            "\x1bOB": "down",
            "\x1bOC": "right",
            "\x1bOD": "left",
            "\x1bOH": "home",
            "\x1bOF": "end",
        }
        for sequence, name in expected.items():
            with self.subTest(sequence=sequence):
                self.assertEqual(decode(sequence), [Key(name)])

    def test_tilde_keys(self):
        expected = {
            "1": "home",
            "2": "insert",
            "3": "delete",
            "4": "end",
            "5": "page_up",
            "6": "page_down",
            "7": "home",
            "8": "end",
        }
        for number, name in expected.items():
            with self.subTest(number=number):
                self.assertEqual(decode(f"\x1b[{number}~"), [Key(name)])

    def test_modifiers(self):
        expected = {
            1: Key("right"),
            2: Key("right", shift=True),
            3: Key("right", alt=True),
            4: Key("right", alt=True, shift=True),
            5: Key("right", ctrl=True),
            6: Key("right", ctrl=True, shift=True),
            7: Key("right", ctrl=True, alt=True),
            8: Key("right", ctrl=True, alt=True, shift=True),
            9: Key("right", alt=True),  # meta
            13: Key("right", ctrl=True, alt=True),
        }
        for modifier, key in expected.items():
            with self.subTest(modifier=modifier):
                self.assertEqual(decode(f"\x1b[1;{modifier}C"), [key])
                self.assertEqual(decode(f"\x1b[;{modifier}C"), [key])
                self.assertEqual(decode(f"\x1bO{modifier}C"), [key])

    def test_modified_tilde_keys(self):
        self.assertEqual(decode("\x1b[3;5~"), [Key("delete", ctrl=True)])
        self.assertEqual(decode("\x1b[5;2~"), [Key("page_up", shift=True)])
        self.assertEqual(decode("\x1b[1;3~"), [Key("home", alt=True)])

    def test_modified_letters(self):
        self.assertEqual(decode("\x1b[1;5A"), [Key("up", ctrl=True)])
        self.assertEqual(decode("\x1b[1;2H"), [Key("home", shift=True)])
        self.assertEqual(decode("\x1b[1;3F"), [Key("end", alt=True)])
        self.assertEqual(decode("\x1b[1;6D"), [Key("left", ctrl=True, shift=True)])

    def test_invalid_modifiers_are_unknown(self):
        for sequence in ("\x1b[1;0C", "\x1b[1;17C", "\x1b[3;99~"):
            with self.subTest(sequence=sequence):
                self.assertEqual(decode(sequence), [Key("unknown", sequence)])

    def test_shift_tab(self):
        self.assertEqual(decode("\x1b[Z"), [Key("tab", shift=True)])
        self.assertEqual(decode("\x1b[1;5Z"), [Key("tab", ctrl=True, shift=True)])

    def test_rxvt_keys(self):
        self.assertEqual(decode("\x1b[a"), [Key("up", shift=True)])
        self.assertEqual(decode("\x1b[d"), [Key("left", shift=True)])
        self.assertEqual(decode("\x1bOc"), [Key("right", ctrl=True)])
        self.assertEqual(decode("\x1bOb"), [Key("down", ctrl=True)])
        self.assertEqual(decode("\x1b[3$"), [Key("delete", shift=True)])
        self.assertEqual(decode("\x1b[5^"), [Key("page_up", ctrl=True)])
        self.assertEqual(decode("\x1b[7@"), [Key("home", ctrl=True, shift=True)])

    def test_keypad_enter(self):
        self.assertEqual(decode("\x1bOM"), [Key("enter")])

    def test_unrecognised_sequences_are_unknown(self):
        for sequence in (
            "\x1b[15~",  # F5
            "\x1bOP",  # F1
            "\x1b[[A",  # F1 on the Linux console
            "\x1b[?1;2c",
            "\x1b[1;2;3A",
            "\x1b[2A",
            "\x1b[9~",
            "\x1b[~",
            "\x1b[3;5$~",  # "$" is an intermediate unless it ends rxvt keys
            "\x1b[ q",
            "\x1b[97;5u",
            "\x1b[1;5:1C",
            "\x1bO1;5C",
            "\x1b[201~",  # paste end without a start
        ):
            with self.subTest(sequence=sequence):
                self.assertEqual(
                    decode(sequence + "x"), [Key("unknown", sequence), *chars("x")]
                )

    def test_alt_keys(self):
        for char in "aZ5 é😀[O~\ufffd":
            with self.subTest(char=char):
                self.assertEqual(decode(ESC + char), [Key("alt", char)])
        self.assertEqual(decode("\x1bab"), [Key("alt", "a"), *chars("b")])

    def test_alt_with_control_characters(self):
        self.assertEqual(decode("\x1b\x7f"), [Key("backspace", alt=True)])
        self.assertEqual(decode("\x1b\x08"), [Key("backspace", alt=True)])
        self.assertEqual(decode("\x1b\r"), [Key("enter", alt=True)])
        self.assertEqual(decode("\x1b\t"), [Key("tab", alt=True)])
        self.assertEqual(decode("\x1b\x01"), [Key("ctrl", "a", alt=True)])
        self.assertEqual(decode("\x1b\x00"), [Key("unknown", "\x1b\x00")])
        self.assertEqual(decode("\x1b\u0085"), [Key("unknown", "\x1b\u0085")])
        self.assertEqual(decode("\x1b\r\n"), [Key("enter", alt=True)])

    def test_escape_escape(self):
        self.assertEqual(decode("\x1b\x1b"), [Key("escape"), Key("escape")])
        self.assertEqual(decode("\x1b\x1b[A"), [Key("escape"), Key("up")])
        self.assertEqual(decode("\x1b\x1ba"), [Key("escape"), Key("alt", "a")])

    def test_interrupted_sequences(self):
        self.assertEqual(
            decode("\x1b[1\x03"), [Key("unknown", "\x1b[1"), Key("ctrl", "c")]
        )
        self.assertEqual(decode("\x1b[1\x1b[A"), [Key("unknown", "\x1b[1"), Key("up")])
        self.assertEqual(decode("\x1b[é"), [Key("alt", "["), *chars("é")])
        self.assertEqual(decode("\x1bO\r"), [Key("alt", "O"), Key("enter")])
        self.assertEqual(decode("\x1bO1x"), [Key("unknown", "\x1bO1x")])
        self.assertEqual(decode("\x1bO1é"), [Key("unknown", "\x1bO1"), *chars("é")])
        self.assertEqual(
            decode("\x1b[[\x1b"), [Key("unknown", "\x1b[["), Key("escape")]
        )

    def test_sequences_split_at_every_position(self):
        for text in (
            "\x1b[A",
            "\x1b[1;5C",
            "\x1bOH",
            "\x1b[3~",
            "\x1b[3$",
            "\x1b[[B",
            "\x1ba",
            "\x1b\x1b[B",
            "\x1bé",
            "x\x1b[6;2~日\r\n😀",
        ):
            expected = decode(text)
            data = text.encode()
            for split in range(len(data) + 1):
                with self.subTest(text=text, split=split):
                    self.assertEqual(decode(data[:split], data[split:]), expected)
            with self.subTest(text=text, split="bytes"):
                self.assertEqual(decode(*(bytes([b]) for b in data)), expected)


class TestDecoderTimeout(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.decoder = KeyDecoder(self.clock)

    def test_lone_escape_waits_for_the_timeout(self):
        self.assertFalse(self.decoder.pending())
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.assertTrue(self.decoder.pending())
        self.assertEqual(self.decoder.flush(), [])
        self.clock.now += KeyDecoder.ESCAPE_TIMEOUT / 2
        self.assertEqual(self.decoder.flush(), [])
        self.assertTrue(self.decoder.pending())
        self.clock.now += KeyDecoder.ESCAPE_TIMEOUT
        self.assertEqual(self.decoder.flush(), [Key("escape")])
        self.assertFalse(self.decoder.pending())
        self.assertEqual(self.decoder.flush(), [])
        self.assertEqual(self.decoder.feed(b"a"), chars("a"))

    def test_timeout_is_measured_from_the_last_feed(self):
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.clock.now += KeyDecoder.ESCAPE_TIMEOUT * 0.9
        self.assertEqual(self.decoder.feed(b"["), [])
        self.clock.now += KeyDecoder.ESCAPE_TIMEOUT * 0.9
        self.assertEqual(self.decoder.flush(), [])
        self.assertEqual(self.decoder.feed(b"B"), [Key("down")])

    def test_forced_flush(self):
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.assertEqual(self.decoder.flush(force=True), [Key("escape")])

    def test_escape_continued_by_a_later_feed(self):
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.assertEqual(self.decoder.feed(b"[A"), [Key("up")])
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.clock.now += 1  # the caller did not flush in time
        self.assertEqual(self.decoder.feed(b"[A"), [Key("up")])

    def test_escape_then_text_after_the_timeout(self):
        self.assertEqual(self.decoder.feed(b"\x1b"), [])
        self.clock.now += 1
        self.assertEqual(self.decoder.flush(), [Key("escape")])
        self.assertEqual(self.decoder.feed(b"[A"), chars("[A"))

    def test_incomplete_sequences_after_the_timeout(self):
        for text, keys in (
            ("\x1b[", [Key("alt", "[")]),
            ("\x1bO", [Key("alt", "O")]),
            ("\x1b[1;5", [Key("unknown", "\x1b[1;5")]),
            ("\x1bO5", [Key("unknown", "\x1bO5")]),
            ("\x1b[[", [Key("unknown", "\x1b[[")]),
            ("a\x1b", [Key("char", "a")]),
        ):
            with self.subTest(text=text):
                decoder = KeyDecoder(self.clock)
                self.assertEqual(
                    decoder.feed(text.encode()), keys[:1] if text[0] == "a" else []
                )
                self.assertTrue(decoder.pending())
                self.clock.now += 1
                flushed = decoder.flush()
                self.assertEqual(flushed, [Key("escape")] if text[0] == "a" else keys)
                self.assertFalse(decoder.pending())


class TestDecoderPaste(unittest.TestCase):
    def test_paste_is_one_key(self):
        self.assertEqual(
            decode(f"a{START}https://x.y/?a=1&b=[2]{END}b"),
            [*chars("a"), Key("paste", "https://x.y/?a=1&b=[2]"), *chars("b")],
        )

    def test_empty_paste(self):
        self.assertEqual(decode(START + END), [Key("paste", "")])

    def test_paste_with_unicode(self):
        self.assertEqual(
            decode(f"{START}é日😀e\u0301{END}"), [Key("paste", "é日😀e\u0301")]
        )

    def test_newlines_and_tabs_become_spaces(self):
        self.assertEqual(
            decode(f"{START}a\r\nb\rc\nd\te\n\n{END}"), [Key("paste", "a b c d e  ")]
        )
        self.assertEqual(decode(START, "a\r", "\nb", END), [Key("paste", "a b")])

    def test_control_characters_are_removed(self):
        self.assertEqual(
            decode(f"{START}a\x00\x03\x7f\x08\u0085\u009b\u202e\u2028b{END}"),
            [Key("paste", "ab")],
        )

    def test_escape_sequences_are_removed(self):
        self.assertEqual(
            decode(
                f"{START}a\x1b[31mb\x1b[200~c\x1bOAd\x1bxe\x1b\x1b[1;5Cf"
                f"\x1b\rg\x1b[1\x1b[201~h"
            ),
            [Key("paste", "abcdef g"), *chars("h")],
        )

    def test_overlong_sequence_in_paste(self):
        self.assertEqual(
            decode(f"{START}a\x1b[{'1' * 100}mb{END}"), [Key("paste", "ab")]
        )
        self.assertEqual(decode(f"{START}a\x1b[{'1' * 100}{END}"), [Key("paste", "a")])

    def test_paste_split_at_every_position(self):
        text = f"x{START}a\r\nb\x1b[1mé日{END}\x1b[A"
        expected = [*chars("x"), Key("paste", "a bé日"), Key("up")]
        data = text.encode()
        for split in range(len(data) + 1):
            with self.subTest(split=split):
                self.assertEqual(decode(data[:split], data[split:]), expected)
        self.assertEqual(decode(*(bytes([b]) for b in data)), expected)

    def test_paste_is_not_cut_by_the_timeout(self):
        clock = FakeClock()
        decoder = KeyDecoder(clock)
        for chunk in (START + "ab", "\x1b", "[20", "1"):
            self.assertEqual(decoder.feed(chunk.encode()), [])
            self.assertFalse(decoder.pending())
            clock.now += 10
            self.assertEqual(decoder.flush(), [])
            self.assertEqual(decoder.flush(force=True), [])
        self.assertEqual(decoder.feed(b"~c"), [Key("paste", "ab"), *chars("c")])

    def test_paste_is_capped(self):
        size = KeyDecoder.MAX_PASTE + 1000
        keys = decode(START + "a" * size + "b" + END + "c")
        self.assertEqual(keys, [Key("paste", "a" * KeyDecoder.MAX_PASTE), *chars("c")])

    def test_endless_paste_memory_is_bounded(self):
        decoder = KeyDecoder(FakeClock())
        self.assertEqual(decoder.feed(START.encode()), [])
        chunk = ("abc\r\n\x1b[1m" + "\x1b[" + "9" * 50 + "日" * 1000).encode()
        for _ in range(100):
            self.assertEqual(decoder.feed(chunk), [])
            paste: list[str] = private(decoder, KeyDecoder, "paste")
            self.assertLessEqual(len(paste), KeyDecoder.MAX_PASTE)
            sequence: str = private(decoder, KeyDecoder, "sequence")
            self.assertLessEqual(len(sequence), KeyDecoder.MAX_SEQUENCE)
        self.assertEqual(decoder.feed(b"\x1b[" + b"1" * 50), [])
        self.assertLessEqual(
            len(private(decoder, KeyDecoder, "sequence")), KeyDecoder.MAX_SEQUENCE
        )
        keys = decoder.feed(END.encode())
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(keys[0].text), KeyDecoder.MAX_PASTE)
        self.assertEqual(private(decoder, KeyDecoder, "paste"), [])


class TestDecoderLimits(unittest.TestCase):
    def test_overlong_sequence_is_abandoned(self):
        sequence = "\x1b[" + "1" * 100 + "A"
        keys = decode(sequence + "x")
        self.assertEqual(
            keys, [Key("unknown", sequence[: KeyDecoder.MAX_SEQUENCE]), *chars("x")]
        )

    def test_abandoned_sequence_ends_at_a_non_parameter(self):
        self.assertEqual(
            decode("\x1b[" + "1" * 100 + "\x1b[B"),
            [
                Key("unknown", "\x1b[" + "1" * (KeyDecoder.MAX_SEQUENCE - 2)),
                Key("down"),
            ],
        )
        self.assertEqual(
            decode("\x1bO" + "1" * 100 + "é"),
            [
                Key("unknown", "\x1bO" + "1" * (KeyDecoder.MAX_SEQUENCE - 2)),
                *chars("é"),
            ],
        )

    def test_abandoned_sequence_and_timeout(self):
        clock = FakeClock()
        decoder = KeyDecoder(clock)
        keys = decoder.feed(("\x1b[" + "1" * 40).encode())
        self.assertEqual([k.name for k in keys], ["unknown"])
        self.assertTrue(decoder.pending())
        clock.now += 1
        self.assertEqual(decoder.flush(), [])
        self.assertFalse(decoder.pending())
        self.assertEqual(decoder.feed(b"A"), chars("A"))

    def test_longest_sequence_is_decoded(self):
        padding = "0" * (KeyDecoder.MAX_SEQUENCE - 6)
        sequence = f"\x1b[1;{padding}5C"
        self.assertEqual(len(sequence), KeyDecoder.MAX_SEQUENCE)
        self.assertEqual(decode(sequence), [Key("right", ctrl=True)])
        longer = f"\x1b[1;0{padding}5C"
        self.assertEqual(decode(longer)[0].name, "unknown")


class TestDecoderFuzz(unittest.TestCase):
    PIECES: tuple[bytes, ...] = (
        b"\x1b",
        b"[",
        b"O",
        b";",
        b"1",
        b"5",
        b"~",
        b"A",
        b"$",
        b"\r",
        b"\n",
        b"\t",
        b"\x7f",
        b"\x00",
        b"a",
        b" ",
        b"\xc3",
        b"\xa9",
        b"\xe6\x97\xa5",
        b"\xf0\x9f",
        b"\xc2\x85",
        b"\xff",
        START.encode(),
        END.encode(),
        b"\x1b[1;5C",
    )

    def check(self, keys: list[Key]) -> None:
        for key in keys:
            self.assertIn(key.name, NAMES)
            self.assertIsInstance(key.text, str)
            if key.name in ("char", "alt"):
                self.assertEqual(len(key.text), 1)
                self.assertTrue(is_printable(key.text), repr(key))
            elif key.name == "ctrl":
                self.assertIn(key.text, "abcdefghijklmnopqrstuvwxyz")
            elif key.name == "paste":
                self.assertLessEqual(len(key.text), KeyDecoder.MAX_PASTE)
                self.assertTrue(all(is_printable(c) for c in key.text), repr(key))
            elif key.name != "unknown":
                self.assertEqual(key.text, "")

    def test_random_bytes(self):
        rng = random.Random(1234)
        for _ in range(300):
            clock = FakeClock()
            decoder = KeyDecoder(clock)
            for _ in range(rng.randrange(1, 30)):
                if rng.random() < 0.5:
                    data = bytes(
                        rng.randrange(256) for _ in range(rng.randrange(1, 20))
                    )
                else:
                    data = b"".join(
                        rng.choice(self.PIECES) for _ in range(rng.randrange(1, 20))
                    )
                self.check(decoder.feed(data))
                if rng.random() < 0.3:
                    clock.now += rng.choice((0.0, 0.01, 0.1))
                    self.check(decoder.flush(force=rng.random() < 0.2))
                _ = decoder.pending()
            self.check(decoder.flush(force=True))

    def test_random_bytes_through_the_editor(self):
        rng = random.Random(99)
        for _ in range(100):
            decoder = KeyDecoder(FakeClock())
            editor = LineEditor(max_length=rng.randrange(0, 40))
            for _ in range(30):
                data = b"".join(
                    rng.choice(self.PIECES) for _ in range(rng.randrange(1, 8))
                )
                for key in decoder.feed(data):
                    _ = editor.handle(key)
                    self.assertLessEqual(
                        len(editor.text), private(editor, LineEditor, "max_length")
                    )
                    self.assertTrue(all(is_printable(c) for c in editor.text))
                    width = rng.randrange(0, 12)
                    visible, column = editor.render(width)
                    self.assertLessEqual(Unicode.len(visible), max(0, width))
                    self.assertLessEqual(column, max(0, width - 1))


def editor_with(
    text: str, cursor: int | None = None, max_length: int = LineEditor.MAX_LENGTH
) -> LineEditor:
    editor = LineEditor(text, max_length)
    if cursor is not None:
        _ = editor.handle(Key("home"))
        while editor.cursor < cursor:
            _ = editor.handle(Key("right"))
    return editor


class TestLineEditor(unittest.TestCase):
    def type(self, editor: LineEditor, *keys: Key) -> list[LineEditor.Result]:
        return [editor.handle(key) for key in keys]

    def test_initial_text(self):
        editor = LineEditor("abc")
        self.assertEqual((editor.text, editor.cursor), ("abc", 3))
        self.assertEqual(LineEditor().text, "")
        self.assertEqual(LineEditor("a\x00b\nc\u202e").text, "abc")
        self.assertEqual(LineEditor("abcdef", max_length=4).text, "abcd")
        self.assertEqual(LineEditor("abce\u0301", max_length=4).text, "abc")
        self.assertEqual(LineEditor("abc", max_length=-1).text, "")

    def test_typing(self):
        editor = LineEditor()
        results = self.type(editor, *chars("héllo 日😀"))
        self.assertEqual(set(results), {LineEditor.Result.CONTINUE})
        self.assertEqual((editor.text, editor.cursor), ("héllo 日😀", 8))

    def test_insert_at_cursor(self):
        editor = editor_with("ac", 1)
        _ = editor.handle(Key("char", "b"))
        self.assertEqual((editor.text, editor.cursor), ("abc", 2))
        _ = editor.handle(Key("paste", "XY"))
        self.assertEqual((editor.text, editor.cursor), ("abXYc", 4))

    def test_control_characters_are_not_inserted(self):
        editor = LineEditor("a")
        for char in ("\x00", "\x07", "\x1b", "\x7f", "\u0085", "\u202e", "\n", ""):
            _ = editor.handle(Key("char", char))
        _ = editor.handle(Key("paste", "b\x00c\rd"))
        self.assertEqual(editor.text, "abcd")

    def test_max_length(self):
        editor = LineEditor("abc", max_length=5)
        self.type(editor, *chars("defg"))
        self.assertEqual(editor.text, "abcde")
        _ = editor.handle(Key("backspace"))
        _ = editor.handle(Key("paste", "XYZ"))
        self.assertEqual((editor.text, editor.cursor), ("abcdX", 5))
        _ = editor.handle(Key("home"))
        _ = editor.handle(Key("paste", "Q"))
        self.assertEqual(editor.text, "abcdX")

    def test_max_length_keeps_combining_marks_with_their_base(self):
        editor = LineEditor("ab", max_length=4)
        _ = editor.handle(Key("paste", "ce\u0301f"))
        self.assertEqual(editor.text, "abc")
        editor = LineEditor("ab", max_length=4)
        _ = editor.handle(Key("paste", "e\u0301f"))
        self.assertEqual(editor.text, "abe\u0301")

    def test_results(self):
        editor = LineEditor("abc")
        self.assertEqual(editor.handle(Key("enter")), LineEditor.Result.SUBMIT)
        self.assertEqual(
            editor.handle(Key("enter", alt=True)), LineEditor.Result.SUBMIT
        )
        self.assertEqual(editor.handle(Key("escape")), LineEditor.Result.CANCEL)
        self.assertEqual(editor.handle(Key("ctrl", "c")), LineEditor.Result.CANCEL)
        self.assertEqual(editor.handle(Key("ctrl", "g")), LineEditor.Result.CANCEL)
        for key in (
            Key("up"),
            Key("down"),
            Key("tab"),
            Key("tab", shift=True),
            Key("insert"),
            Key("page_up"),
            Key("page_down"),
            Key("unknown", "\x1b[15~"),
            Key("ctrl", "z"),
            Key("ctrl", "c", alt=True),
            Key("ctrl", "a", alt=True),
            Key("alt", "x"),
        ):
            with self.subTest(key=key):
                self.assertEqual(editor.handle(key), LineEditor.Result.CONTINUE)
                self.assertEqual((editor.text, editor.cursor), ("abc", 3))

    def test_backspace_and_delete(self):
        editor = editor_with("abcd", 2)
        _ = editor.handle(Key("backspace"))
        self.assertEqual((editor.text, editor.cursor), ("acd", 1))
        _ = editor.handle(Key("delete"))
        self.assertEqual((editor.text, editor.cursor), ("ad", 1))
        _ = editor.handle(Key("ctrl", "d"))
        self.assertEqual((editor.text, editor.cursor), ("a", 1))
        self.type(editor, Key("delete"), Key("ctrl", "d"))
        self.assertEqual((editor.text, editor.cursor), ("a", 1))
        self.type(editor, Key("backspace"), Key("backspace"))
        self.assertEqual((editor.text, editor.cursor), ("", 0))

    def test_movement(self):
        editor = LineEditor("abc")
        expected = (
            (Key("left"), 2),
            (Key("ctrl", "b"), 1),
            (Key("home"), 0),
            (Key("left"), 0),
            (Key("right"), 1),
            (Key("ctrl", "f"), 2),
            (Key("end"), 3),
            (Key("right"), 3),
            (Key("ctrl", "a"), 0),
            (Key("ctrl", "e"), 3),
            (Key("home", ctrl=True), 0),
            (Key("end", shift=True), 3),
        )
        for key, cursor in expected:
            _ = editor.handle(key)
            self.assertEqual(editor.cursor, cursor, key)
        self.assertEqual(editor.text, "abc")

    def test_kill(self):
        editor = editor_with("hello world", 5)
        _ = editor.handle(Key("ctrl", "k"))
        self.assertEqual((editor.text, editor.cursor), ("hello", 5))
        _ = editor.handle(Key("left"))
        _ = editor.handle(Key("ctrl", "u"))
        self.assertEqual((editor.text, editor.cursor), ("o", 0))

    def test_word_movement(self):
        text = "https://www.example.com/a_b  path"
        editor = LineEditor(text)
        stops: list[int] = []
        while editor.cursor > 0:
            _ = editor.handle(Key("alt", "b"))
            stops.append(editor.cursor)
        self.assertEqual(stops, [29, 26, 24, 20, 12, 8, 0])
        stops = []
        while editor.cursor < len(text):
            _ = editor.handle(Key("alt", "f"))
            stops.append(editor.cursor)
        self.assertEqual(stops, [5, 11, 19, 23, 25, 27, 33])
        for key in (Key("left", ctrl=True), Key("left", alt=True)):
            _ = editor.handle(Key("end"))
            _ = editor.handle(key)
            self.assertEqual(editor.cursor, 29)
        for key in (Key("right", ctrl=True), Key("right", alt=True)):
            _ = editor.handle(Key("home"))
            _ = editor.handle(key)
            self.assertEqual(editor.cursor, 5)

    def test_word_deletion(self):
        editor = LineEditor("open https://a.b/c-d  ")
        _ = editor.handle(Key("backspace", alt=True))
        self.assertEqual(editor.text, "open https://a.b/c-")
        _ = editor.handle(Key("backspace", ctrl=True))
        self.assertEqual(editor.text, "open https://a.b/")
        _ = editor.handle(Key("home"))
        _ = editor.handle(Key("alt", "d"))
        self.assertEqual((editor.text, editor.cursor), (" https://a.b/", 0))
        _ = editor.handle(Key("delete", ctrl=True))
        self.assertEqual((editor.text, editor.cursor), ("://a.b/", 0))

    def test_unix_word_rubout(self):
        editor = LineEditor("get https://a.b/c-d  ")
        _ = editor.handle(Key("ctrl", "w"))
        self.assertEqual((editor.text, editor.cursor), ("get ", 4))
        _ = editor.handle(Key("ctrl", "w"))
        self.assertEqual((editor.text, editor.cursor), ("", 0))
        _ = editor.handle(Key("ctrl", "w"))
        self.assertEqual(editor.text, "")
        editor = editor_with("one two three", 7)
        _ = editor.handle(Key("ctrl", "w"))
        self.assertEqual((editor.text, editor.cursor), ("one  three", 4))

    def test_combining_marks_are_one_unit(self):
        text = "e\u0301\u0302xa\u0301"
        editor = LineEditor(text)
        positions = [editor.cursor]
        while editor.cursor > 0:
            _ = editor.handle(Key("left"))
            positions.append(editor.cursor)
        self.assertEqual(positions, [6, 4, 3, 0])
        while editor.cursor < len(text):
            _ = editor.handle(Key("right"))
            positions.append(editor.cursor)
        self.assertEqual(positions, [6, 4, 3, 0, 3, 4, 6])
        _ = editor.handle(Key("backspace"))
        self.assertEqual((editor.text, editor.cursor), ("e\u0301\u0302x", 4))
        _ = editor.handle(Key("home"))
        _ = editor.handle(Key("delete"))
        self.assertEqual((editor.text, editor.cursor), ("x", 0))

    def test_marks_without_a_base_join_the_inserted_character(self):
        editor = editor_with("\u0301z", 0)
        _ = editor.handle(Key("char", "x"))
        self.assertEqual((editor.text, editor.cursor), ("x\u0301z", 2))
        _ = editor.handle(Key("left"))
        self.assertEqual(editor.cursor, 0)

    def test_combining_mark_typed_after_a_character(self):
        editor = LineEditor("ab")
        _ = editor.handle(Key("left"))
        _ = editor.handle(Key("char", "\u0301"))
        self.assertEqual((editor.text, editor.cursor), ("a\u0301b", 2))
        _ = editor.handle(Key("left"))
        self.assertEqual(editor.cursor, 0)

    def test_words_with_combining_marks_and_wide_characters(self):
        editor = LineEditor("cafe\u0301 日本")
        _ = editor.handle(Key("alt", "b"))
        self.assertEqual(editor.cursor, 6)
        _ = editor.handle(Key("alt", "b"))
        self.assertEqual(editor.cursor, 0)
        _ = editor.handle(Key("alt", "f"))
        self.assertEqual(editor.cursor, 5)


class TestLineEditorRender(unittest.TestCase):
    def check(self, editor: LineEditor, width: int) -> tuple[str, int]:
        text, cursor = editor.text, editor.cursor
        visible, column = editor.render(width)
        if width < 1:
            self.assertEqual((visible, column), ("", 0))
            return visible, column
        scroll: int = private(editor, LineEditor, "scroll")
        message = f"{text!r} cursor={cursor} width={width} scroll={scroll}"
        self.assertLessEqual(Unicode.len(visible), width, message)
        self.assertTrue(0 <= column <= width - 1, message)
        self.assertEqual(visible, text[scroll : scroll + len(visible)], message)
        self.assertTrue(scroll <= cursor <= scroll + len(visible), message)
        self.assertEqual(column, Unicode.len(text[scroll:cursor]), message)
        stop = scroll + len(visible)
        if stop < len(text):
            # As much as fits is shown, and never half a cluster
            self.assertGreater(Unicode.len(text[scroll : stop + 1]), width, message)
            self.assertGreater(Unicode.len(text[stop]), 0, message)
        if 0 < scroll < len(text):
            self.assertGreater(Unicode.len(text[scroll]), 0, message)
        if cursor < len(text) and Unicode.len(text[cursor]) <= width:
            self.assertGreater(stop, cursor, message)  # the cursor character is shown
        return visible, column

    def test_short_text(self):
        self.assertEqual(LineEditor("abc").render(10), ("abc", 3))
        self.assertEqual(editor_with("abc", 1).render(10), ("abc", 1))
        self.assertEqual(LineEditor("日本").render(10), ("日本", 4))
        self.assertEqual(LineEditor("").render(1), ("", 0))

    def test_small_widths(self):
        self.assertEqual(LineEditor("abc").render(0), ("", 0))
        self.assertEqual(LineEditor("abc").render(-5), ("", 0))
        self.assertEqual(LineEditor("abc").render(1), ("", 0))
        self.assertEqual(editor_with("abc", 1).render(1), ("b", 0))
        self.assertEqual(editor_with("日", 0).render(1), ("", 0))
        self.assertEqual(editor_with("日", 0).render(2), ("日", 0))

    def test_text_exactly_filling_the_width(self):
        self.assertEqual(LineEditor("abcd").render(5), ("abcd", 4))
        self.assertEqual(LineEditor("abcd").render(4), ("bcd", 3))
        self.assertEqual(editor_with("abcd", 0).render(4), ("abcd", 0))

    def test_wide_characters_are_not_split(self):
        self.assertEqual(editor_with("ab日c", 0).render(3), ("ab", 0))
        self.assertEqual(editor_with("ab日c", 0).render(4), ("ab日", 0))
        visible, column = LineEditor("日本語").render(4)
        self.assertEqual((visible, column), ("語", 2))

    def test_combining_marks_are_shown_with_their_base(self):
        self.assertEqual(LineEditor("e\u0301e\u0301").render(3), ("e\u0301e\u0301", 2))
        self.assertEqual(editor_with("ae\u0301b", 0).render(2), ("ae\u0301", 0))

    def test_invariants(self):
        rng = random.Random(42)
        alphabet = "abc  xyz日本😀\u0301\u0302"
        texts = ["", "a", "日", "a" * 30, "日" * 15, "e\u0301" * 10, "日a" * 10]
        texts += [
            "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 40)))
            for _ in range(25)
        ]
        for text in texts:
            for width in (*range(-1, 14), 20, 50):
                editor = LineEditor(text)
                positions: list[int] = []
                for key in (Key("home"), Key("end")):
                    _ = editor.handle(key)
                    step = Key("right") if key.name == "home" else Key("left")
                    while True:
                        self.check(editor, width)
                        positions.append(editor.cursor)
                        before = editor.cursor
                        _ = editor.handle(step)
                        if editor.cursor == before:
                            break
                # Random jumps and edits from any scroll state
                for _ in range(20):
                    key = rng.choice(
                        (
                            Key("home"),
                            Key("end"),
                            Key("left"),
                            Key("right"),
                            Key("left", ctrl=True),
                            Key("backspace"),
                            Key("ctrl", "k"),
                            Key("char", rng.choice(alphabet)),
                        )
                    )
                    _ = editor.handle(key)
                    self.check(editor, width)

    def test_typing_scrolls_in_steps(self):
        editor = LineEditor()
        width = 12
        context = width // 4
        scrolls: list[int] = []
        for _ in range(60):
            _ = editor.handle(Key("char", "x"))
            _, column = editor.render(width)
            scroll: int = private(editor, LineEditor, "scroll")
            if scrolls and scroll != scrolls[-1]:
                self.assertEqual(column, context)  # context kept left of the cursor
            scrolls.append(scroll)
        changes = sum(1 for a, b in pairwise(scrolls) if a != b)
        self.assertGreater(changes, 0)
        self.assertLessEqual(changes, 60 // (width - 1 - context) + 1)

    def test_long_initial_text_shows_its_end(self):
        editor = LineEditor("abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(editor.render(10), ("rstuvwxyz", 9))

    def test_moving_within_the_view_does_not_scroll(self):
        editor = LineEditor("abcdefghijklmnopqrstuvwxyz")
        first, _ = editor.render(10)
        for column in range(8, 0, -1):
            _ = editor.handle(Key("left"))
            self.assertEqual(editor.render(10), (first, column))
        # At the left edge, the view scrolls to show context before the cursor
        _ = editor.handle(Key("left"))
        self.assertEqual(editor.cursor, 17)
        visible, column = editor.render(10)
        self.assertEqual((visible, column), ("klmnopqrst", 10 - 1 - 10 // 4))
        for _ in range(2):
            _ = editor.handle(Key("right"))
            self.assertEqual(editor.render(10)[0], visible)
        _ = editor.handle(Key("home"))
        self.assertEqual(editor.render(10), ("abcdefghij", 0))
        # Past the right edge, a quarter of the width is kept before the cursor
        _ = editor.handle(Key("end"))
        self.assertEqual(editor.render(10), ("yz", 10 // 4))

    def test_scrolls_back_when_the_text_shrinks(self):
        editor = LineEditor("a" * 30)
        self.assertEqual(editor.render(10), ("a" * 9, 9))
        self.assertEqual(private(editor, LineEditor, "scroll"), 21)
        for _ in range(25):
            _ = editor.handle(Key("backspace"))
        self.assertEqual(editor.render(10), ("aaaaa", 5))
        self.assertEqual(private(editor, LineEditor, "scroll"), 0)

    def test_render_after_kill_to_start(self):
        editor = LineEditor("a" * 30)
        _ = editor.render(10)
        _ = editor.handle(Key("ctrl", "u"))
        self.assertEqual(editor.render(10), ("", 0))


if __name__ == "__main__":
    unittest.main()
