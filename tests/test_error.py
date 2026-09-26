# pyright: standard
import unittest

from vidl.error import Error


class TestError(unittest.TestCase):
    def test_provider_is_parsed_from_bracket_prefix(self):
        error = Error("ERROR: [youtube] abc123: Video unavailable")
        self.assertEqual(error.provider, "youtube")

    def test_single_colon_keeps_the_identity_in_the_message(self):
        error = Error("[youtube] abc123: Video unavailable")
        self.assertIsNone(error.identity)
        self.assertEqual(error.message, "abc123: Video unavailable")

    def test_multiple_colons_split_the_identity(self):
        error = Error("[youtube] abc: Sign in: to confirm")
        self.assertEqual(error.identity, "abc")
        self.assertEqual(error.message, " Sign in: to confirm")

    def test_plain_message(self):
        error = Error("Something failed")
        self.assertIsNone(error.provider)
        self.assertIsNone(error.identity)
        self.assertEqual(error.message, "Something failed")

    def test_error_prefix_is_removed(self):
        self.assertEqual(Error("ERROR: Something failed").message, "Something failed")


if __name__ == "__main__":
    unittest.main()
