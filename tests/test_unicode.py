# pyright: standard
import unittest

from vidl.unicode import Unicode


class TestUnicode(unittest.TestCase):
    def test_ascii_is_one_column_per_character(self):
        self.assertEqual(Unicode.len(""), 0)
        self.assertEqual(Unicode.len("abc 123"), 7)

    def test_east_asian_wide_characters_are_two_columns(self):
        self.assertEqual(Unicode.len("日本語"), 6)
        self.assertEqual(Unicode.len("ＡＢ"), 4)  # fullwidth

    def test_emoji_are_two_columns(self):
        self.assertEqual(Unicode.len("😀"), 2)
        self.assertEqual(Unicode.len("✅"), 2)
        self.assertEqual(Unicode.len("🟢"), 2)

    def test_combining_and_format_characters_are_zero_columns(self):
        self.assertEqual(Unicode.len("e\u0301"), 1)  # combining acute accent
        self.assertEqual(Unicode.len("\u200b"), 0)  # zero width space
        self.assertEqual(Unicode.len("\ufeff"), 0)  # byte order mark
        self.assertEqual(Unicode.len("\u2600\ufe0f"), 2)  # variation selector

    def test_zero_width_joiner_sequence(self):
        self.assertEqual(Unicode.len("👨\u200d👩"), 4)

    def test_box_drawing_is_one_column(self):
        self.assertEqual(Unicode.len("╭─│╯"), 4)


if __name__ == "__main__":
    unittest.main()
