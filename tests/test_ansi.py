# pyright: standard
import unittest

from vidl.ansi import ANSI


class TestANSI(unittest.TestCase):
    def test_remove_strips_sgr_osc_and_cursor_sequences(self):
        string = (
            f"{ANSI.Bold}a{ANSI.Reset}{ANSI.title_bar('title')}b"
            f"{ANSI.print('c', 3, 4)}{ANSI.Alternate.Enter}"
        )
        self.assertEqual(ANSI.remove(string), "abc")

    def test_len_ignores_sequences(self):
        self.assertEqual(ANSI.len(f"{ANSI.Color.Cerise}ab{ANSI.Color.DefaultFg}"), 2)

    def test_len_counts_wide_characters(self):
        self.assertEqual(ANSI.len(f"{ANSI.Inverse}日本{ANSI.InverseReset}"), 4)

    def test_trim_returns_string_unchanged_when_it_fits(self):
        self.assertEqual(ANSI.trim("abc", 3), "abc")
        self.assertEqual(ANSI.trim("abc", 10), "abc")

    def test_trim_keeps_sequences_and_appends_reset(self):
        trimmed = ANSI.trim(f"{ANSI.Inverse}abcdef{ANSI.InverseReset}", 3)
        self.assertEqual(ANSI.remove(trimmed), "abc")
        self.assertTrue(trimmed.startswith(ANSI.Inverse))
        self.assertTrue(trimmed.endswith(ANSI.Reset))

    def test_trim_does_not_split_wide_characters(self):
        self.assertEqual(ANSI.remove(ANSI.trim("a日b", 2)), "a")
        self.assertEqual(ANSI.remove(ANSI.trim("a日b", 3)), "a日")

    def test_trim_to_zero_or_negative_length(self):
        self.assertEqual(ANSI.remove(ANSI.trim("abc", 0)), "")
        self.assertEqual(ANSI.remove(ANSI.trim("abc", -5)), "")

    def test_color_from_hex(self):
        self.assertEqual(ANSI.Color.fg("#D9FF2F"), ANSI.Color.NeonChartreuse)
        self.assertEqual(ANSI.Color.fg("000000"), ANSI.Color.Black)
        self.assertEqual(ANSI.Color.bg("#0a0B0c"), "\x1b[48;2;10;11;12m")

    @unittest.skipUnless(__debug__, "assertions are disabled")
    def test_color_rejects_short_hex(self):
        with self.assertRaises(AssertionError):
            ANSI.Color.fg("#fff")

    def test_cursor_and_osc_sequences(self):
        self.assertEqual(ANSI.print("x", 2, 3), "\x1b[2;3Hx")
        self.assertEqual(ANSI.print(), "\x1b[1;1H")
        self.assertEqual(ANSI.title_bar("t"), "\x1b]2;t\x1b\\")
        self.assertEqual(ANSI.notify("n"), "\x1b]9;n\x1b\\")


if __name__ == "__main__":
    unittest.main()
