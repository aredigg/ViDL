# pyright: standard
import os
import unittest
from dataclasses import replace
from typing import Any, ClassVar
from unittest import mock

from tests.helpers import FakeTerminal, IsolatedTestCase, private
from vidl.ansi import ANSI
from vidl.config import Config
from vidl.controls import Controls
from vidl.item import Item
from vidl.keyboard import Key, LineEditor
from vidl.monitor import DiskStatus, NetworkState, SystemStatus, VersionStatus
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
        # Status updates only redraw the input row of the header
        header.update_status(self.terminal.terminal, 0)
        self.assertEqual({row for row, _, _ in self.terminal.writes}, {4})

    def test_invisible_view_draws_no_status(self):
        # A hidden view keeps its old position, which may now belong to another view
        self.view.set_visible(False)
        self.view.set_status(View.Status(State.ERROR, provider="p", message="m"))
        self.terminal.clear()
        self.view.update_status(self.terminal.terminal, 0)
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

    def test_item_line_without_a_count_shows_no_number(self):
        self.view.set_item_entity(
            Item.Entity(
                "abc", 2, 0, 365 * Util.SECONDS_PER_DAY, "A title", "", 0, "", ""
            )
        )
        self.assertIn("1971-01-01 │      │ A title", self.draw().text())

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

    def status_line(self, provider: str, message: str, cols: int = 40) -> str:
        self.view.resize(cols=cols)
        self.view.set_status(
            View.Status(State.ERROR, provider=provider, message=message)
        )
        self.terminal.clear()
        self.view.update_status(self.terminal.terminal, 0)
        line = self.terminal.row(self.STATUS_ROW, self.CONTENT_COL)
        self.assertLessEqual(ANSI.len(line), cols - 8)
        return line.rstrip()

    def test_long_provider_is_trimmed_to_keep_the_message(self):
        line = self.status_line("p" * 100, "the message")
        self.assertTrue(line.endswith("p…: the message"), line)

    def test_long_message_drops_the_provider_and_is_marked(self):
        line = self.status_line("provider", "m" * 100)
        text = line.split(View.INVALID_TIME, 1)[1]  # after the timer
        self.assertNotIn("provider", text)
        self.assertNotIn(":", text)
        self.assertTrue(line.endswith("m…"), line)

    def test_message_without_provider_has_no_separator(self):
        line = self.status_line("", "just this")
        self.assertTrue(line.endswith("  just this"), line)
        self.assertNotIn(":", line.split(View.INVALID_TIME, 1)[1])

    def test_message_that_fits_is_unchanged(self):
        line = self.status_line("abc", "HTTP Error 429: Too Many", cols=80)
        self.assertTrue(line.endswith("abc: HTTP Error 429: Too Many"), line)

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


class TestHeader(ViewTestCase):
    WIDTH = 140

    def setUp(self):
        super().setUp()
        self.clock = [100.0]
        patcher = mock.patch("vidl.view.monotonic", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.header = View(header=True)
        self.header.resize(rows=View.HEADER_HEIGHT, cols=self.WIDTH)

    def rows(self) -> tuple[str, str, str]:
        self.draw(self.header)
        return self.terminal.row(2), self.terminal.row(3), self.terminal.row(4)

    def system(self, **changes: Any) -> SystemStatus:
        status = SystemStatus(
            disks=(DiskStatus("d/t", "/out", free=412 * 2**30, total=1084 * 2**30),),
            network=NetworkState.ONLINE,
            version=VersionStatus(running="2026.08.19"),
        )
        return replace(status, **changes)

    def test_lines_fit_the_width(self):
        self.header.set_system_status(self.system())
        self.header.set_header_status("x" * 500)
        self.header.set_notice("n" * 500)
        self.draw(self.header)
        for row, col, text in self.terminal.writes:
            self.assertLessEqual(col - 1 + ANSI.len(text), self.WIDTH, (row, text))

    def uptime_row(self, elapsed: int) -> str:
        with mock.patch("vidl.view.time", return_value=NOW + elapsed):
            return self.rows()[0]

    def test_clock_and_uptime(self):
        # The uptime is wall clock time, including time the computer slept
        self.clock[0] += 60  # the monotonic clock is not used for it
        first = self.uptime_row(2 * Util.SECONDS_PER_DAY + 3723)
        self.assertIn("ViDL", first)
        # Formatted as every other duration, in hours: no days, no word
        self.assertTrue(first.endswith(f"{Util.get_time()} | 49:03′"), first)
        self.assertNotIn("up", first)

    def test_the_uptime_is_formatted_by_format_seconds(self):
        for elapsed in (0, 59, 65, 3570, 3600, 3725, 86_400, 90_061):
            with self.subTest(elapsed=elapsed):
                expected = f"{Util.get_time()} | {Util.format_seconds(elapsed)}"
                self.assertTrue(self.uptime_row(elapsed).endswith(expected))
        self.assertTrue(self.uptime_row(65).endswith("| 01′05″"))

    def test_the_uptime_never_goes_negative(self):
        # The wall clock was set back
        row = self.uptime_row(-500)
        self.assertTrue(row.endswith(f"{Util.get_time()} | 00′00″"), row)

    def test_the_uptime_goes_first_when_narrow(self):
        self.header.resize(cols=26)
        row = self.uptime_row(3725)
        self.assertTrue(row.endswith(Util.get_time()), row)
        self.assertNotIn("|", row)

    def test_system_status(self):
        self.header.set_system_status(self.system())
        _, second, _ = self.rows()
        self.assertTrue(
            second.endswith("d/t 412G (38%) | \U000f0c54 | yt-dlp 2026.08.19"),
            second,
        )
        self.assertNotIn("free", second)
        self.assertNotIn("·", second)

    def test_nothing_is_shown_before_the_first_status(self):
        _, second, _ = self.rows()
        self.assertEqual(second.strip(), "")

    def test_low_disk_space_is_colored(self):
        for free, color in ((5, ANSI.Color.BurntSienna), (1, ANSI.Color.Cerise)):
            with self.subTest(free=free):
                disk = DiskStatus("d", "/out", free=free * 2**30, total=100 * 2**30)
                self.header.set_system_status(self.system(disks=(disk,)))
                self.draw(self.header)
                self.assertIn(f"{color}d {free}.0G ({free}%)", self.terminal.raw())

    def test_unavailable_disk(self):
        disk = DiskStatus("t", "/x", error="unavailable")
        self.header.set_system_status(self.system(disks=(disk,)))
        self.assertIn("t unavailable", self.rows()[1])

    NERD: ClassVar[dict[NetworkState, str]] = {
        NetworkState.ONLINE: "\U000f0c54",
        NetworkState.OFFLINE: "\U000f0c5f",
        NetworkState.UNKNOWN: "\U000f0c8a",
        NetworkState.DEGRADED: "\U000f0c9a",
    }
    PLAIN: ClassVar[dict[NetworkState, str]] = {
        NetworkState.ONLINE: "🌐 ✓",
        NetworkState.OFFLINE: "🌐 ✕",
        NetworkState.UNKNOWN: "🌐 ?",
        NetworkState.DEGRADED: "🌐 !",
    }

    def header_with(self, nerd_fonts: bool, **config: bool) -> View:
        Config.settings["General"].update(nerd_fonts=nerd_fonts, **config)
        header = View(header=True)
        header.resize(rows=View.HEADER_HEIGHT, cols=self.WIDTH)
        return header

    def network_segment(self, header: View, **changes: Any) -> str:
        header.set_system_status(self.system(**changes))
        self.draw(header)
        return self.terminal.row(3)

    def test_network_states_are_symbols_with_nerd_fonts(self):
        header = self.header_with(True)
        for state, symbol in self.NERD.items():
            with self.subTest(state=state):
                # Only a degraded network has a number, the errors
                second = self.network_segment(header, network=state, network_errors=3)
                count = " 3" if state is NetworkState.DEGRADED else ""
                self.assertIn(f"| {symbol}{count} |", second)
                for word in ("online", "offline", "degraded", "unknown", "error"):
                    self.assertNotIn(word, second)

    def test_network_states_are_plain_symbols_without_nerd_fonts(self):
        header = self.header_with(False)
        for state, symbol in self.PLAIN.items():
            with self.subTest(state=state):
                second = self.network_segment(header, network=state, network_errors=3)
                count = " 3" if state is NetworkState.DEGRADED else ""
                self.assertIn(f"| {symbol}{count} |", second)
                for nerd in self.NERD.values():
                    self.assertNotIn(nerd, second)

    def test_the_symbols_are_all_different(self):
        for column in (0, 1):
            symbols = [pair[column] for pair in View.NETWORK_SYMBOLS.values()]
            self.assertEqual(len(set(symbols)), 4)
        self.assertEqual(set(View.NETWORK_SYMBOLS), set(NetworkState))

    def test_a_degraded_network_shows_the_number_of_errors(self):
        header = self.header_with(True)
        second = self.network_segment(
            header, network=NetworkState.DEGRADED, network_errors=3
        )
        self.assertIn(
            f"{ANSI.Color.BurntSienna}{self.NERD[NetworkState.DEGRADED]} 3",
            self.terminal.raw(),
        )
        self.assertIn("\U000f0c9a 3 |", second)

    def test_the_colors_of_the_network_states(self):
        header = self.header_with(True)
        _ = self.network_segment(header, network=NetworkState.OFFLINE)
        self.assertIn(
            f"{ANSI.Color.Cerise}{self.NERD[NetworkState.OFFLINE]}", self.terminal.raw()
        )
        self.terminal.clear()
        _ = self.network_segment(header, network=NetworkState.ONLINE)
        raw = self.terminal.raw()
        self.assertNotIn(ANSI.Color.Cerise + self.NERD[NetworkState.ONLINE], raw)
        self.assertNotIn(ANSI.Color.BurntSienna + self.NERD[NetworkState.ONLINE], raw)

    def test_no_network_symbol_when_the_check_is_off(self):
        for nerd_fonts in (True, False):
            with self.subTest(nerd_fonts=nerd_fonts):
                header = self.header_with(nerd_fonts, network_check=False)
                for state in NetworkState:
                    second = self.network_segment(header, network=state)
                    for symbol in (*self.NERD.values(), *self.PLAIN.values()):
                        self.assertNotIn(symbol, second)

    def test_the_network_symbols_fit_the_width_calculation(self):
        # A wide globe takes two columns, a glyph one: nothing may overflow
        for nerd_fonts in (True, False):
            header = self.header_with(nerd_fonts)
            for state in NetworkState:
                _ = self.network_segment(header, network=state, network_errors=3)
                for _row, col, text in self.terminal.writes:
                    self.assertLessEqual(col - 1 + ANSI.len(text), self.WIDTH)
                self.terminal.clear()

    def test_updates_and_restarts(self):
        outdated = VersionStatus(running="2026.08.19", latest="2026.09.30")
        self.header.set_system_status(self.system(version=outdated))
        self.assertIn("yt-dlp 2026.08.19 → 2026.09.30", self.rows()[1])
        restart = VersionStatus(
            running="2026.08.19", installed="2026.09.30", latest="2026.09.30"
        )
        self.header.set_system_status(self.system(version=restart))
        self.assertIn("yt-dlp 2026.08.19, restart for 2026.09.30", self.rows()[1])

    def test_narrow_headers_drop_the_least_important_segments(self):
        self.header.resize(cols=40)
        self.header.set_system_status(self.system())
        _, second, _ = self.rows()
        self.assertIn("d/t 412G", second)
        self.assertIn("\U000f0c54", second)
        self.assertNotIn("yt-dlp", second)
        self.header.resize(cols=28)
        _, second, _ = self.rows()
        self.assertIn("d/t 412G", second)
        self.assertNotIn("\U000f0c54", second)

    def test_header_status_and_shutdown(self):
        self.header.set_header_status("Finishing 2 downloads")
        self.assertTrue(self.rows()[1].startswith("Finishing 2 downloads"))
        self.header.set_status(
            View.Status(State.INACTIVE, message="Shutdown in progress")
        )
        second = self.rows()[1]
        self.assertIn("Shutdown in progress", second)
        self.assertNotIn("Finishing", second)

    def test_hints_are_shown_by_default(self):
        self.assertEqual(self.rows()[2].strip(), Controls.HINTS)
        self.assertEqual(Controls.HINTS, "[Q] Quit | [C] Complete | [A] Add")
        self.assertIsNone(self.header.cursor_position())

    def test_no_separator_but_the_bar(self):
        for text in (Controls.HINTS, Controls.PROMPT_HINT, View.SEGMENT_SEPARATOR):
            self.assertNotIn("·", text)
        self.assertEqual(View.SEGMENT_SEPARATOR, " | ")
        self.assertEqual(Controls.PROMPT_HINT, "[Enter] Add | [Esc] Cancel")

    def test_notices_expire(self):
        self.header.set_notice("Added https://x")
        self.assertEqual(self.rows()[2].strip(), "Added https://x")
        self.assertIn(ANSI.Color.NeonChartreuse, self.terminal.raw())
        self.header.set_notice("Already listed", error=True)
        self.draw(self.header)
        self.assertIn(ANSI.Color.Cerise + "Already listed", self.terminal.raw())
        self.clock[0] += View.NOTICE_SECONDS
        self.assertEqual(self.rows()[2].strip(), Controls.HINTS)

    def test_prompt_and_cursor(self):
        editor = LineEditor("https://x")
        self.header.set_prompt(("Add channel", editor))
        third = self.rows()[2]
        self.assertTrue(third.startswith("Add channel: https://x"), third)
        self.assertTrue(third.rstrip().endswith(Controls.PROMPT_HINT))
        row, col = self.header.cursor_position() or (0, 0)
        self.assertEqual(row, 4)
        self.assertEqual(col, View.HEADER_COL + len("Add channel: https://x"))
        editor.handle(Key("home"))
        self.rows()
        self.assertEqual(
            self.header.cursor_position(),
            (4, View.HEADER_COL + len("Add channel: ")),
        )
        self.header.set_prompt(None)
        self.rows()
        self.assertIsNone(self.header.cursor_position())

    def test_long_input_scrolls_within_the_width(self):
        editor = LineEditor("https://example.com/" + "x" * 300)
        self.header.set_prompt(("Add channel", editor))
        self.draw(self.header)
        for _, col, text in self.terminal.writes:
            self.assertLessEqual(col - 1 + ANSI.len(text), self.WIDTH)
        _, col = self.header.cursor_position() or (0, 0)
        self.assertLess(col, self.WIDTH)

    def test_typing_is_drawn_by_status_updates(self):
        editor = LineEditor()
        self.header.set_prompt(("Add channel", editor))
        editor.handle(Key("char", "h"))
        self.terminal.clear()
        self.header.update_status(self.terminal.terminal, 0)
        self.assertTrue(self.terminal.row(4).startswith("Add channel: h"))


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
