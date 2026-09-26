# pyright: standard
import os
import unittest
from unittest import mock

from tests.helpers import FakeTerminal, IsolatedTestCase, private
from vidl.ansi import ANSI
from vidl.item import Item
from vidl.util import Util
from vidl.view import View

NOW = 1_000_000.0
MIB = 2**20
State = View.Status.State


class ViewTestCase(IsolatedTestCase):
    """A visible 120x12 panel at row 6, drawn on a fake terminal at a fixed time."""

    STATUS_ROW = 8
    MEDIA_ROW = 12
    CONTENT_COL = 5  # the border is drawn on the same rows

    def setUp(self):
        super().setUp()
        self.use_utc()
        patcher = mock.patch("vidl.view.time", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.terminal = FakeTerminal()
        self.view = View()
        self.view.resize(rows=12, cols=120, origin_row=6, origin_col=1)
        self.view.set_visible(True)

    def draw(self, view: View | None = None) -> FakeTerminal:
        self.terminal.clear()
        (view or self.view).update(self.terminal.terminal, 0)
        return self.terminal


class TestStatus(unittest.TestCase):
    def test_provider_colons_are_replaced(self):
        self.assertEqual(View.Status(State.ERROR, provider="a:b").provider, "a b")

    def test_every_state_has_a_symbol_and_emoji(self):
        for state in State:
            status = View.Status(state)
            self.assertEqual(ANSI.len(status.symbol(0)), 1, state)
            self.assertTrue(status.emoji(), state)

    def test_animated_states_change_with_the_sequence(self):
        for state in (State.PROCESS, State.DOWNLOAD_REQ_WAIT):
            status = View.Status(state)
            self.assertNotEqual(status.symbol(0), status.symbol(1))
        self.assertEqual(
            View.Status(State.WAITING).symbol(0), View.Status(State.WAITING).symbol(1)
        )


class TestTimers(ViewTestCase):
    def test_no_timer(self):
        self.assertEqual(self.view.timer(), View.INVALID_TIME)

    def test_download_counts_up(self):
        self.view.set_status(View.Status(State.DOWNLOAD))
        self.view.set_timer(-65)
        self.assertEqual(self.view.timer(), "01′05″")

    def test_sleep_counts_down(self):
        for state in (State.SLEEPING, State.DOWNLOAD_WAIT, State.DOWNLOAD_REQ_WAIT):
            self.view.set_status(View.Status(state))
            self.view.set_timer(65)
            self.assertEqual(self.view.timer(), "01′05″")

    def test_other_states_show_zero(self):
        self.view.set_timer(65)
        self.assertEqual(self.view.timer(), "00′00″")

    def test_countdown_shows_the_last_seconds_of_a_sleep(self):
        self.view.set_status(View.Status(State.SLEEPING))
        self.view.set_timer(4)
        self.assertEqual(self.view.countdown().strip(), "4")
        self.view.set_timer(60)
        self.assertEqual(self.view.countdown().strip(), "")
        self.view.set_timer(-2)
        self.assertEqual(self.view.countdown().strip(), "")

    def test_countdown_is_blank_when_idle(self):
        self.view.set_timer(4)
        self.assertEqual(self.view.countdown().strip(), "")
        self.assertEqual(len(self.view.countdown()), len(View.COUNTDOWN[0]) + 1)


class TestDrawing(ViewTestCase):
    def test_header(self):
        header = View(header=True)
        header.resize(rows=View.HEADER_HEIGHT, cols=130)
        self.assertIn("ViDL", self.draw(header).row(2))
        header.set_status(View.Status(State.INACTIVE, message="Shutdown in progress"))
        self.assertIn("Shutdown in progress", self.draw(header).row(3))
        self.terminal.clear()
        header.update_status(self.terminal.terminal, 0)
        self.assertEqual(self.terminal.writes, [])

    def test_invisible_view_draws_nothing(self):
        self.view.set_visible(False)
        self.assertEqual(self.draw().writes, [])

    def test_border_with_title_count_and_cutoff(self):
        self.view.set_top_index(3)
        self.view.set_top("Channel")
        self.view.set_count(12)
        self.view.set_count(0)  # ignored
        self.view.set_cutoff(365 * Util.SECONDS_PER_DAY)
        top = self.draw().row(6)
        self.assertTrue(top.startswith("╭"))
        self.assertTrue(top.endswith("╮"))
        self.assertIn(" 3 │ Channel (12)", top)
        self.assertIn("1971-01-01", top)
        self.assertEqual(ANSI.len(top), 120)
        self.assertTrue(self.terminal.row(17).startswith("╰"))

    def test_set_top_clears_count_and_cutoff(self):
        self.view.set_top("First")
        self.view.set_count(5)
        self.view.set_top("Second")
        top = self.draw().row(6)
        self.assertIn("Second", top)
        self.assertNotIn("(5)", top)

    def test_item_line(self):
        self.view.set_count(10)
        self.view.set_item_entity(
            Item.Entity(
                "abc", 2, 0, 365 * Util.SECONDS_PER_DAY, "A title", "", 0, "", ""
            )
        )
        line = self.draw().text()
        self.assertIn("1971-01-01 │    9 │ A title", line)  # numbered from the oldest
        self.assertIn("[abc]", line)

    def test_media_lines(self):
        self.view.set_item_media(
            Item.Media(
                3725, "mp4", "https", "1920x1080@30 SDR AVC1", "48000x2 MP4A", "en/no"
            )
        )
        self.draw()
        rows = [
            self.terminal.row(self.MEDIA_ROW + i, self.CONTENT_COL) for i in range(3)
        ]
        self.assertIn("│ 01:02:05 MP4/HTTPS", rows[0])
        self.assertIn("│ 1920x1080@30 SDR AVC1 | 48000x2 MP4A", rows[1])
        self.assertIn("│ en/no", rows[2])

    def test_status_colors(self):
        for state, color in (
            (State.ERROR, ANSI.Color.Cerise),
            (State.WARNING, ANSI.Color.BurntSienna),
            (State.PROCESS, ANSI.Color.NeonChartreuse),
        ):
            self.view.set_status(View.Status(state, provider="prov", message="msg"))
            self.draw()
            self.assertIn(
                "prov: msg", self.terminal.row(self.STATUS_ROW, self.CONTENT_COL)
            )
            self.assertIn(color + "msg", self.terminal.raw())

    def test_long_provider_is_trimmed_to_keep_the_message(self):
        self.view.resize(cols=40)
        self.view.set_status(
            View.Status(State.ERROR, provider="p" * 100, message="the message")
        )
        self.terminal.clear()
        self.view.update_status(self.terminal.terminal, 0)
        line = self.terminal.row(self.STATUS_ROW, self.CONTENT_COL)
        self.assertTrue(line.rstrip().endswith("p: the message"), line)
        self.assertLessEqual(ANSI.len(line), 40 - 8)

    def test_download_with_known_size_shows_a_meter(self):
        self.view.set_status(View.Status(State.DOWNLOAD))
        self.view.set_item_progress(
            Item.Progress(size_current=MIB, size_total=2 * MIB, percent=50)
        )
        line = self.draw().row(self.STATUS_ROW, self.CONTENT_COL)
        self.assertIn("━", line)
        self.assertIn(View.INVALID_TIME, line)  # no estimate yet

    def test_download_with_unknown_size_shows_size_and_bitrate(self):
        self.view.set_status(View.Status(State.DOWNLOAD))
        self.view.set_timer(-20)
        self.view.set_item_progress(Item.Progress(size_current=5 * MIB))
        line = self.draw().row(self.STATUS_ROW, self.CONTENT_COL)
        self.assertIn("     5 MB", line)
        self.assertIn("2048 kbps", line)  # 5 MiB in 20 s


class TestRemainingTime(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.view.set_status(View.Status(State.DOWNLOAD))
        self.total = 500 * MIB

    def sample(self, elapsed: float, size: int) -> tuple[int, bool]:
        self.view.set_item_progress(
            Item.Progress(
                time_current=elapsed, size_current=size, size_total=self.total
            )
        )
        self.draw()
        return private(self.view, View, "remaining_time")

    def test_estimate_needs_a_full_window(self):
        results = [self.sample(s, s * MIB) for s in range(1, View.RING_SIZE + 2)]
        self.assertFalse(results[View.RING_SIZE - 1][1])
        self.assertEqual(results[View.RING_SIZE], (500 - (View.RING_SIZE + 1), True))

    def test_estimate_follows_the_current_rate(self):
        size, remaining, valid = 0, 0, False
        for second in range(1, 121):
            size += MIB if second <= 60 else MIB // 10
            remaining, valid = self.sample(second, size)
        expected = (self.total - size) / (MIB / 10)
        self.assertTrue(valid)
        self.assertLess(abs(remaining - expected) / expected, 0.1)

    def test_countdown_uses_the_estimate(self):
        self.total = 30 * MIB
        for second in range(1, 26):
            self.sample(second, second * MIB)
        self.assertEqual(self.view.countdown().strip(), "5")

    def test_restarted_progress_clears_the_window(self):
        for second in range(1, 5):
            self.sample(second, second * MIB)
        self.sample(1, MIB // 2)  # next format starts
        self.assertEqual(len(private(self.view, View, "sample_ring")), 1)

    def test_no_estimate_outside_downloads(self):
        self.view.set_status(View.Status(State.PROCESS))
        self.assertEqual(self.sample(1, MIB), (0, False))

    def test_reset_keeps_the_bitrate_history(self):
        for second in range(1, 5):
            self.sample(second, second * MIB)
        bitrates = len(private(self.view, View, "bitrate_ring"))
        self.view.reset()
        self.assertEqual(len(private(self.view, View, "bitrate_ring")), bitrates)
        self.assertEqual(len(private(self.view, View, "sample_ring")), 0)
        self.assertEqual(private(self.view, View, "remaining_time"), (0, False))


class TestFileSize(ViewTestCase):
    def test_partial_file_starts_the_download(self):
        directory = self.temp_dir()
        with open(os.path.join(directory, "vid.mp4.part"), "wb") as f:
            f.write(b"x" * 1234)
        self.view.set_filepath(os.path.join(directory, "vid.NA"))
        self.view.set_item_media(Item.Media(0, "mp4", "", "", "", ""))
        self.view.set_status(View.Status(State.DOWNLOAD_WAIT))
        self.view.set_timer(60)
        self.draw()
        self.assertEqual(self.view.get_status().state, State.DOWNLOAD)
        self.assertEqual(self.view.timer(), "00′00″")
        progress = private(self.view, View, "item_progress")
        self.assertEqual(progress.size_current, 1234)

    def test_missing_file_keeps_waiting(self):
        self.view.set_filepath(os.path.join(self.temp_dir(), "vid.NA"))
        self.view.set_item_media(Item.Media(0, "mp4", "", "", "", ""))
        self.view.set_status(View.Status(State.DOWNLOAD_WAIT))
        self.draw()
        self.assertEqual(self.view.get_status().state, State.DOWNLOAD_WAIT)


class TestState(ViewTestCase):
    def test_reset_clears_the_item(self):
        self.view.set_item_entity(Item.Entity("a", 1, 1, 0, "t", "", 0, "", ""))
        self.view.set_item_media(Item.Media(0, "", "", "", "", ""))
        self.view.set_filepath("/tmp/a.NA")
        self.view.reset()
        for name in ("item_entity", "item_media", "item_progress", "temp_filepath"):
            self.assertIsNone(private(self.view, View, name), name)

    def test_resize_changes_only_given_values(self):
        size = self.view.resize(cols=80)
        self.assertEqual(size, View.Size(rows=12, cols=80, origin_row=6, origin_col=1))

    def test_accessors(self):
        self.view.set_top_index(4)
        self.assertEqual(self.view.get_top_index(), 4)
        status = View.Status(State.WARNING)
        self.view.set_status(status)
        self.assertIs(self.view.get_status(), status)


if __name__ == "__main__":
    unittest.main()
