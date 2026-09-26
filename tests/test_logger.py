# pyright: standard
import unittest
from unittest import mock

from vidl.logger import Logger


class TestLogger(unittest.TestCase):
    def setUp(self):
        self.reported: list[str] = []
        patcher = mock.patch("vidl.logger.Debug.print")
        self.debug_print = patcher.start()
        self.addCleanup(patcher.stop)
        self.logger = Logger(3, self.reported.append)

    def logged(self) -> list[tuple[int, str]]:
        return [call.args for call in self.debug_print.call_args_list]

    def test_error_is_reported_without_prefix(self):
        self.logger.error("ERROR: [youtube] abc: Private video")
        self.assertEqual(self.reported, ["[youtube] abc: Private video"])
        self.assertEqual(
            self.logged(), [(3, f"ERR --> {'youtube':>20} | abc: Private video")]
        )

    def test_error_without_provider(self):
        self.logger.error("ERROR: plain")
        self.assertEqual(self.reported, ["plain"])
        self.assertEqual(self.logged(), [(3, "ERR --> plain")])

    def test_other_levels_are_logged_but_not_reported(self):
        self.logger.debug("[download] Destination: x.mp4")
        self.logger.info("[info] Downloading")
        self.logger.warning("plain warning")
        self.assertEqual(self.reported, [])
        self.assertEqual(
            [message.split(" -->")[0] for _, message in self.logged()],
            ["DBG", "INF", "WRN"],
        )
        self.assertTrue(self.logged()[0][1].endswith("| Destination: x.mp4"))
        self.assertEqual(self.logged()[2], (3, "WRN --> plain warning"))

    def test_long_provider_is_truncated(self):
        self.logger.debug("[" + "p" * 30 + "] message")
        self.assertIn("p" * 20 + " |", self.logged()[0][1])
        self.assertNotIn("p" * 21, self.logged()[0][1])


if __name__ == "__main__":
    unittest.main()
