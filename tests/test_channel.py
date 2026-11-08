# pyright: standard
import os
import stat
import sys
import threading
import time
import unittest
from collections.abc import Callable, Mapping
from queue import Queue
from threading import Event
from typing import Any, TypeVar, cast
from unittest import mock

from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError, ExtractorError

from tests.helpers import FakeProcessor, IsolatedTestCase, drain, playlist, video
from vidl.channel import Channel, ChannelHelper
from vidl.config import Config
from vidl.cookies import CookieWatch, Refresh
from vidl.message import (
    CountMessage,
    CutoffMessage,
    EntityMessage,
    ErrorMessage,
    InfoMessage,
    MediaMessage,
    Message,
    PathMessage,
    SleepMessage,
    WarningMessage,
)
from vidl.metadata import MetadataContext, MetadataPostProcessor
from vidl.preflight import (
    Preflight,
    PreflightError,
    PreflightInterrupted,
    PreflightReason,
)
from vidl.shared import Shared
from vidl.suspension import Suspension
from vidl.util import Util

DAY = Util.SECONDS_PER_DAY
NOW = int(time.time())


M = TypeVar("M", bound=Message)


def download_error(exception: Exception) -> DownloadError:
    """A DownloadError as raised by yt-dlp while handling exception."""
    try:
        raise exception
    except Exception:  # noqa: BLE001
        return DownloadError(f"ERROR: {exception}", cast(Any, sys.exc_info()))


class ChannelTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        Config.SLEEP_INTERVAL = 0
        Config.settings["Download"].update(
            allow_vertical=False,
            minimum_resolution=0,
            playlist_cutoff=0,
            resolution_defer=0,
            sleep_interval=0,
            post_sleep_cutoff=0,
        )
        self.queue: Queue[Message] = Queue()
        self.halt = Event()

    def run_channel(
        self,
        processor: FakeProcessor,
        url: str = "https://c",
        shared: Shared | None = None,
        slot: int = 0,
        metadata: MetadataPostProcessor | None = None,
    ) -> tuple[Channel, bool]:
        channel = Channel(url, None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_shared(shared)
        channel.set_metadata_processor(metadata)
        channel.set_active()
        result = channel.download(slot, processor.processor, self.queue)
        return channel, result

    def messages(self, cls: type[M]) -> list[M]:
        return [m for m in drain(self.queue) if isinstance(m, cls)]

    def downloaded_ids(self, processor: FakeProcessor) -> list[str]:
        return [url.rsplit("/", 1)[1] for url in processor.downloaded]


class TestChannelState(ChannelTestCase):
    def test_row_from_constructor(self):
        channel = Channel("Name", "https://u", "10", "20", "error")
        self.assertEqual(channel.row(), ["Name", "https://u", 10, 20, "error"])
        self.assertEqual(channel.get_name(), "Name")
        self.assertEqual(channel.get_last_error(), "error")

    def test_invalid_dates_become_zero(self):
        channel = Channel("Name", None, "10", "not a date", None)
        self.assertEqual(channel.row(), ["Name", "", 0, 0, ""])

    def test_queue_key_is_the_attempt_then_the_download(self):
        cases = (
            (Channel("a", None, 100, 200, None), (200, 100)),
            (Channel("a", None, 0, 200, None), (200, 0)),
            # No attempt recorded, e.g. a file from an older version
            (Channel("a", None, 100, 0, None), (100, 100)),
            (Channel("a", None, None, None, None), (0, 0)),
        )
        for channel, key in cases:
            with self.subTest(key=key):
                self.assertEqual(channel.queue_key(), key)

    def test_a_failing_channel_goes_behind_the_others(self):
        # It was downloaded from long ago, but is tried most recently
        failing = Channel("failing", None, 100, 5000, "Network error")
        waiting = Channel("waiting", None, 4000, 4000, None)
        never = Channel("never", None, None, None, None)
        order = sorted([failing, waiting, never], key=Channel.queue_key)
        self.assertEqual([c.get_name() for c in order], ["never", "waiting", "failing"])

    def test_equal_attempts_are_ordered_by_download(self):
        a = Channel("a", None, 300, 500, None)
        b = Channel("b", None, 100, 500, None)
        self.assertEqual(sorted([a, b], key=Channel.queue_key), [b, a])

    def test_active_state(self):
        channel = Channel("a", None, None, None, None)
        self.assertFalse(channel.active())
        channel.set_active()
        self.assertTrue(channel.active())
        channel.set_inactive()
        self.assertFalse(channel.active())

    def test_epoch_cutoff_assignment_and_error_report(self):
        channel = Channel("a", None, None, None, None)
        channel.assign_epoch_cutoff((123, True))
        self.assertEqual(channel.get_epoch_cutoff(), (123, True))
        channel.report_error("failed")
        self.assertEqual(channel.get_last_error(), "failed")


class TestAttempts(ChannelTestCase):
    def test_a_failed_attempt_moves_the_channel_back(self):
        error = download_error(ExtractorError("gone", video_id="abc", expected=True))
        processor = FakeProcessor({"https://c": error})
        channel = Channel("https://c", None, 100, 200, None)
        channel.set_halt_event(self.halt)
        before = int(time.time())
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertGreaterEqual(channel.queue_key()[0], before)
        self.assertEqual(channel.row()[2], 100)  # no new download date

    def test_an_interrupted_attempt_keeps_its_place(self):
        processor = FakeProcessor({"https://c": playlist("p", ["https://v/a"])})
        self.halt.set()
        channel = Channel("https://c", None, 100, 200, "old")
        channel.set_halt_event(self.halt)
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertTrue(channel.interrupted())
        self.assertEqual(channel.queue_key(), (200, 100))

    def test_a_drain_between_entries_keeps_its_place(self):
        shared = Shared()
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": video("a"),
                "https://v/b": video("b"),
            }
        )
        processor.on_download = lambda _: shared.drain_event.set()
        channel = Channel("https://c", None, 100, 200, None)
        channel.set_halt_event(self.halt)
        channel.set_shared(shared)
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertEqual(channel.queue_key(), (200, 100))

    def test_a_completed_attempt_updates_both_dates(self):
        processor = FakeProcessor({"https://c": video("vid")})
        channel = Channel("https://c", None, 100, 200, "old")
        channel.set_halt_event(self.halt)
        before = int(time.time())
        self.assertTrue(channel.download(0, processor.processor, self.queue))
        attempt, download = channel.queue_key()
        self.assertGreaterEqual(attempt, before)
        self.assertGreaterEqual(download, before)


class TestVideo(ChannelTestCase):
    def test_successful_download(self):
        processor = FakeProcessor({"https://c": video("vid", NOW, channel="Uploader")})
        before = int(time.time())
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertEqual(processor.downloaded, ["https://v/vid"])
        name, url, downloaded, attempted, error = channel.row()
        self.assertEqual((name, url, error), ("Title vid", "https://c", ""))
        self.assertGreaterEqual(cast(int, downloaded), before)
        self.assertGreaterEqual(cast(int, attempted), before)
        self.assertFalse(channel.active())

    def test_messages_for_a_download(self):
        processor = FakeProcessor({"https://c": video("vid", NOW, channel="Uploader")})
        self.run_channel(processor)
        messages = drain(self.queue)
        types = [type(m) for m in messages]
        self.assertEqual(
            types,
            [EntityMessage, MediaMessage, PathMessage, SleepMessage, SleepMessage],
        )
        entity, _, path, pre_sleep, post_sleep = messages
        assert isinstance(entity, EntityMessage) and isinstance(path, PathMessage)
        assert isinstance(pre_sleep, SleepMessage)
        assert isinstance(post_sleep, SleepMessage)
        self.assertEqual(
            (entity.entity.id, entity.entity.top_name), ("vid", "Uploader")
        )
        self.assertEqual(path.path, "/tmp/vid.NA")
        self.assertEqual(pre_sleep.provider, Message.Provider.DOWNLOAD)
        self.assertEqual(post_sleep.provider, Message.Provider.CHANNEL)
        self.assertEqual(post_sleep.sleep_time, 0)

    def test_sleep_after_download_is_bounded(self):
        Config.settings["Download"].update(sleep_interval=0, post_sleep_cutoff=1)
        processor = FakeProcessor({"https://c": video("vid")})
        clock = [0.0]

        def download(_: str) -> None:
            clock[0] = 500.0  # the download takes 500 s

        processor.on_download = download
        sleeps: list[float] = []

        def sleep(_: Channel, seconds: float) -> bool:
            sleeps.append(seconds)
            return False

        with (
            mock.patch("vidl.channel.time", side_effect=lambda: clock[0]),
            mock.patch.object(Channel, "_Channel__sleep", sleep),
        ):
            self.run_channel(processor)
        (post_sleep,) = [
            m
            for m in self.messages(SleepMessage)
            if m.provider == Message.Provider.CHANNEL
        ]
        self.assertEqual(post_sleep.sleep_time, 60)  # capped at post_sleep_cutoff
        self.assertEqual(sleeps, [60])

    def test_halt_ends_the_sleep_after_download(self):
        Config.settings["Download"]["sleep_interval"] = 30
        processor = FakeProcessor({"https://c": video("vid")})
        processor.on_download = lambda _: self.halt.set()
        started = time.monotonic()
        _, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertLess(time.monotonic() - started, 5)

    def test_halt_during_the_sleep_after_download_ends_it(self):
        Config.settings["Download"]["sleep_interval"] = 30
        processor = FakeProcessor({"https://c": video("vid")})
        timer = threading.Timer(0.2, self.halt.set)
        self.addCleanup(timer.cancel)
        processor.on_download = lambda _: timer.start()
        started = time.monotonic()
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(channel.get_last_error())

    def test_vertical_video_is_archived_and_skipped(self):
        processor = FakeProcessor(
            {"https://c": video("vid", width=1080, height=1920)}, archive=True
        )
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(processor.archived, ["vid"])
        self.assertEqual(processor.downloaded, [])
        self.assertEqual(channel.get_last_error(), "Vertical video")
        (error,) = self.messages(ErrorMessage)
        self.assertEqual((error.target, error.message), ("vid", "Vertical video"))

    def test_vertical_video_is_downloaded_when_allowed(self):
        Config.settings["Download"]["allow_vertical"] = True
        processor = FakeProcessor({"https://c": video("vid", width=1080, height=1920)})
        self.assertTrue(self.run_channel(processor)[1])

    def test_low_resolution_is_archived_and_skipped(self):
        Config.settings["Download"]["minimum_resolution"] = 1080
        processor = FakeProcessor({"https://c": video("vid", height=720)}, archive=True)
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(processor.archived, ["vid"])
        self.assertEqual(channel.get_last_error(), "Low resolution")

    def test_archive_is_not_recorded_without_download_archive(self):
        Config.settings["Download"]["minimum_resolution"] = 1080
        processor = FakeProcessor({"https://c": video("vid", height=720)})
        self.run_channel(processor)
        self.assertEqual(processor.archived, [])

    def test_already_archived_video_is_not_recorded_twice(self):
        Config.settings["Download"]["minimum_resolution"] = 1080
        processor = FakeProcessor({"https://c": video("vid", height=720)}, archive=True)
        processor.archived.append("vid")
        self.run_channel(processor)
        self.assertEqual(processor.archived, ["vid"])

    def test_recent_video_is_deferred_without_archiving(self):
        Config.settings["Download"]["resolution_defer"] = 2
        processor = FakeProcessor({"https://c": video("vid", NOW - DAY)}, archive=True)
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual((processor.archived, processor.downloaded), ([], []))
        self.assertEqual(channel.get_last_error(), "Defer low resolution")

    def test_high_resolution_video_is_not_deferred(self):
        Config.settings["Download"]["resolution_defer"] = 2
        processor = FakeProcessor({"https://c": video("vid", NOW - DAY, 3840, 2160)})
        self.assertTrue(self.run_channel(processor)[1])

    def test_video_already_in_download_archive(self):
        processor = FakeProcessor({"https://c": None})
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(channel.get_last_error(), Channel.ALREADY_ARCHIVED)
        self.assertEqual(drain(self.queue), [])

    def test_failed_download_without_error_is_unknown(self):
        processor = FakeProcessor({"https://c": video("vid")})
        processor.download_result = 1
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(channel.get_last_error(), "Unknown error")

    def test_download_error_during_download(self):
        processor = FakeProcessor({"https://c": video("vid")})
        processor.download_result = download_error(
            ExtractorError(
                "Requested format is not available", video_id="vid", expected=True
            )
        )
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(channel.get_last_error(), "vid: Format not available")

    def test_name_is_used_as_url_when_url_is_missing(self):
        processor = FakeProcessor({"https://c": video("vid")})
        channel, _ = self.run_channel(processor)
        self.assertEqual(processor.extracted, ["https://c"])
        self.assertEqual(channel.row()[:2], ["Title vid", "https://c"])

    def test_nothing_to_extract(self):
        channel = Channel(None, None, None, None, None)
        channel.set_halt_event(self.halt)
        processor = FakeProcessor()
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertEqual(processor.extracted, [])
        self.assertEqual(channel.get_last_error(), "Unknown error")


class TestErrors(ChannelTestCase):
    def test_extractor_error(self):
        error = ExtractorError("Private video", video_id="abc", expected=True)
        processor = FakeProcessor({"https://c": download_error(error)})
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(channel.get_last_error(), "abc: Private video")
        (message,) = self.messages(ErrorMessage)
        self.assertEqual((message.target, message.message), ("abc", "Private video"))

    def test_other_download_error(self):
        # yt-dlp passes an empty exc_info when not handling an exception. "page"
        # does not look like an id, so the message is not split
        error = DownloadError(
            "ERROR: [generic] page: Unable to download: timeout",
            cast(Any, (None, None, None)),
        )
        processor = FakeProcessor({"https://c": error})
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        (message,) = self.messages(ErrorMessage)
        self.assertEqual(message.target, "https://c")
        self.assertEqual(message.message, "page: Unable to download: timeout")
        self.assertEqual(channel.get_last_error(), "page: Unable to download: timeout")

    def test_messages_with_urls_and_times_are_not_split(self):
        text = "Premieres in 12:30 at https://www.youtube.com/watch?v=x: soon"
        error = ExtractorError(text, video_id="vid12345678", expected=True)
        processor = FakeProcessor({"https://c": download_error(error)})
        channel, _ = self.run_channel(processor)
        (message,) = self.messages(ErrorMessage)
        self.assertEqual(message.target, "vid12345678")
        self.assertNotIn("https", message.message)
        self.assertTrue(cast(str, channel.get_last_error()).startswith("vid12345678: "))

    def test_error_summary_is_short(self):
        long_text = "Something odd happened " + "and more words " * 20
        error = ExtractorError(long_text, video_id="abc", expected=True)
        processor = FakeProcessor({"https://c": download_error(error)})
        channel, _ = self.run_channel(processor)
        (message,) = self.messages(ErrorMessage)
        self.assertLessEqual(len(message.message), 60)
        self.assertTrue(message.message.endswith("…"))
        self.assertLessEqual(len(cast(str, channel.get_last_error())), 65)

    def test_logger_errors_are_stored_in_the_short_form(self):
        channel = Channel("a", None, None, None, None)
        channel.report_error(
            "[youtube] abc123: Private video. Sign in if you've been granted access"
        )
        self.assertEqual(channel.get_last_error(), "abc123: Private video")

    def test_failures_are_stored_bounded_on_one_line(self):
        channel = Channel("a", None, None, None, None)
        channel.report_failure("RuntimeError:\n" + "x" * 500)
        error = cast(str, channel.get_last_error())
        self.assertEqual(len(error), Channel.FAILURE_MAX)
        self.assertNotIn("\n", error)
        self.assertTrue(error.startswith("RuntimeError: x") and error.endswith("…"))

    def test_network_errors_are_reported(self):
        reports: list[str] = []
        shared = Shared(
            network_error=lambda: reports.append("error"),
            network_success=lambda: reports.append("success"),
        )
        error = download_error(TransportError("connection reset"))
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": error,
                "https://v/b": video("b"),
            }
        )
        self.run_channel(processor, shared=shared)
        self.assertEqual(reports[0], "error")
        self.assertIn("success", reports[1:])

    def test_restriction_without_suspension_pauses_briefly(self):
        # Without shared state there is no suspension, only the grace pause
        error = ExtractorError("Video unavailable", video_id="abc", expected=True)
        processor = FakeProcessor({"https://c": download_error(error)})
        sleeps: list[float] = []

        def sleep(_: Channel, seconds: float) -> bool:
            sleeps.append(seconds)
            return False

        with mock.patch.object(Channel, "_Channel__sleep", sleep):
            channel, _ = self.run_channel(processor)
        self.assertEqual(sleeps, [Channel.RESTRICTION_PAUSE])
        self.assertEqual(channel.get_last_error(), "abc: Video unavailable")
        self.assertEqual(self.messages(SleepMessage), [])

    def test_halt_during_suspension_grace_skips_the_sleep(self):
        error = ExtractorError("Video unavailable", video_id="abc", expected=True)

        def halted_while_extracting(_: str) -> DownloadError:
            self.halt.set()
            return download_error(error)

        processor = FakeProcessor({"https://c": halted_while_extracting})
        started = time.monotonic()
        _, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertLess(time.monotonic() - started, Channel.RESTRICTION_PAUSE)
        self.assertEqual(self.messages(SleepMessage), [])

    def test_halted_channel_does_not_extract(self):
        self.halt.set()
        processor = FakeProcessor({"https://c": video("vid")})
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(processor.extracted, [])
        # Halting is neither a success nor an error, the channel keeps its place
        self.assertTrue(channel.interrupted())
        self.assertIsNone(channel.get_last_error())
        self.assertEqual(channel.queue_key(), (0, 0))


class TestPlaylist(ChannelTestCase):
    def test_all_entries_are_downloaded(self):
        processor = FakeProcessor(
            {
                "https://c": playlist(
                    "p", ["https://v/a", "https://v/b"], extractor_key="Tab"
                ),
                "https://v/a": video("a"),
                "https://v/b": video("b"),
            }
        )
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])
        self.assertIsNone(channel.get_last_error())
        messages = drain(self.queue)
        (count,) = [m for m in messages if isinstance(m, CountMessage)]
        (info,) = [m for m in messages if isinstance(m, InfoMessage)]
        self.assertEqual(count.value, 2)
        self.assertEqual((info.target, info.message), ("Playlist p", "Tab"))
        top_names = [
            m.entity.top_name for m in messages if isinstance(m, EntityMessage)
        ]
        self.assertEqual(top_names, ["Playlist p", "", ""])

    def test_entries_prefer_webpage_url_and_skip_missing_urls(self):
        info = playlist("p", [])
        info["entries"] = [
            {"webpage_url": "https://v/a", "url": "a"},
            {"title": "no url"},
        ]
        processor = FakeProcessor({"https://c": info, "https://v/a": video("a")})
        self.run_channel(processor)
        self.assertEqual(processor.extracted, ["https://c", "https://v/a"])

    def test_failing_entries_do_not_fail_the_playlist(self):
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": download_error(ExtractorError("gone", expected=True)),
                "https://v/b": video("b"),
            }
        )
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertEqual(self.downloaded_ids(processor), ["b"])
        self.assertIsNone(channel.get_last_error())

    def test_cutoff_is_relative_to_the_newest_video(self):
        Config.settings["Download"]["playlist_cutoff"] = 1
        t = NOW - 30 * DAY
        processor = FakeProcessor(
            {
                "https://c": playlist("p", [f"https://v/{v}" for v in "abcd"]),
                "https://v/a": video("a", t),
                "https://v/b": video("b", t - DAY // 2),
                "https://v/c": video("c", t - 2 * DAY),
                "https://v/d": video("d", t - DAY // 4),  # after the cutoff was passed
            }
        )
        self.run_channel(processor)
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])
        self.assertEqual(processor.extracted[-1], "https://v/c")
        cutoffs = {m.value for m in self.messages(CutoffMessage)}
        self.assertEqual(cutoffs, {t - DAY})

    def test_videos_without_timestamp_are_within_cutoff(self):
        Config.settings["Download"]["playlist_cutoff"] = 1
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": video("a"),
                "https://v/b": video("b"),
            }
        )
        self.run_channel(processor)
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])

    def test_each_tab_has_its_own_cutoff(self):
        Config.settings["Download"]["playlist_cutoff"] = 1
        t = NOW - 30 * DAY
        infos = {
            "https://c": playlist("c", ["https://c/videos", "https://c/shorts"]),
            "https://c/videos": playlist("videos", ["https://v/va", "https://v/vb"]),
            "https://c/shorts": playlist(
                "shorts", ["https://v/sa", "https://v/sb", "https://v/sc"]
            ),
            "https://v/va": video("va", t),
            "https://v/vb": video("vb", t - 2 * DAY),
            "https://v/sa": video("sa", t - 10 * DAY),
            "https://v/sb": video("sb", t - 10 * DAY - DAY // 2),
            "https://v/sc": video("sc", t - 12 * DAY),
        }
        processor = FakeProcessor(infos)
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertTrue(channel.is_playlist())
        self.assertEqual(self.downloaded_ids(processor), ["va", "sa", "sb"])

    def test_cutoff_is_reset_for_each_run(self):
        Config.settings["Download"]["playlist_cutoff"] = 1
        t = NOW - 30 * DAY
        infos = {
            "https://c": playlist("p", ["https://v/a", "https://v/b"]),
            "https://v/a": video("a", t),
            "https://v/b": video("b", t - 2 * DAY),
        }
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.download(0, FakeProcessor(infos).processor, self.queue)
        infos["https://v/a"] = video("a", t + 5 * DAY)
        processor = FakeProcessor(infos)
        channel.download(0, processor.processor, self.queue)
        self.assertEqual(self.downloaded_ids(processor), ["a"])
        self.assertEqual(channel.get_epoch_cutoff(), (t + 4 * DAY, True))

    def test_halt_while_listing_entries_skips_all_entries(self):
        def listing(_: str) -> dict[str, object]:
            self.halt.set()
            return playlist("p", ["https://v/a"])

        processor = FakeProcessor({"https://c": listing, "https://v/a": video("a")})
        channel, result = self.run_channel(processor)
        # Not completed, so it is neither a download nor an error
        self.assertFalse(result)
        self.assertTrue(channel.interrupted())
        self.assertEqual(processor.extracted, ["https://c"])
        self.assertEqual(channel.row()[2], 0)
        self.assertIsNone(channel.get_last_error())

    def test_halt_between_entries_does_not_record_a_download(self):
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": video("a"),
                "https://v/b": video("b"),
            }
        )
        processor.on_download = lambda _: self.halt.set()
        channel = Channel("https://c", None, None, None, "old error")
        channel.set_halt_event(self.halt)
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertEqual(self.downloaded_ids(processor), ["a"])
        self.assertTrue(channel.interrupted())
        self.assertEqual(channel.row()[2], 0)
        self.assertEqual(channel.get_last_error(), "old error")

    def test_positions_count_entries_without_url(self):
        info = playlist("p", [])
        info["entries"] = [{"title": "no url"}, {"url": "https://v/b"}]
        processor = FakeProcessor({"https://c": info, "https://v/b": video("b")})
        self.run_channel(processor)
        indexes = [
            m.entity.index for m in self.messages(EntityMessage) if m.entity.id == "b"
        ]
        self.assertEqual(indexes, [2])

    def test_empty_playlist(self):
        processor = FakeProcessor({"https://c": playlist("p", [])})
        channel, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertTrue(channel.is_playlist())
        self.assertEqual(self.messages(CountMessage), [])


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def unavailable(video_id: str = "abc") -> DownloadError:
    return download_error(
        ExtractorError("Video unavailable", video_id=video_id, expected=True)
    )


class SleepRecorder:
    """Replaces Channel.__sleep with patch(), records the requested times."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def patch(self) -> Any:
        def sleep(_channel: Channel, seconds: float) -> bool:
            self.sleeps.append(seconds)
            return False

        return mock.patch.object(Channel, "_Channel__sleep", sleep)


class TestSleep(ChannelTestCase):
    """Channel.__sleep with controlled clocks."""

    def setUp(self):
        super().setUp()
        self.wall = [1_000_000.0]
        self.mono = [500.0]
        for target, clock in (
            ("vidl.channel.time", self.wall),
            ("vidl.channel.monotonic", self.mono),
        ):
            patcher = mock.patch(target, side_effect=lambda c=clock: c[0])
            patcher.start()
            self.addCleanup(patcher.stop)
        step = mock.patch.object(Channel, "SLEEP_STEP", 0.01)
        step.start()
        self.addCleanup(step.stop)
        self.channel = Channel("a", None, None, None, None)
        self.channel.set_halt_event(self.halt)

    def sleep(self, seconds: float, change: Callable[[], None]) -> tuple[bool, float]:
        timer = threading.Timer(0.05, change)
        self.addCleanup(timer.cancel)
        timer.start()
        started = time.monotonic()
        result = self.channel._Channel__sleep(seconds)  # pyright: ignore[reportAttributeAccessIssue]
        return result, time.monotonic() - started

    def test_time_asleep_counts(self):
        # The computer slept: the wall clock passed the end, the monotonic
        # clock did not advance (macOS)
        def suspend() -> None:
            self.wall[0] += 600

        result, elapsed = self.sleep(300, suspend)
        self.assertFalse(result)
        self.assertLess(elapsed, 2)

    def test_set_back_wall_clock_does_not_extend_the_sleep(self):
        def set_back_and_pass() -> None:
            self.wall[0] -= 3600
            self.mono[0] += 300

        result, elapsed = self.sleep(300, set_back_and_pass)
        self.assertFalse(result)
        self.assertLess(elapsed, 2)

    def test_sleeps_until_the_end(self):
        def almost() -> None:
            self.wall[0] += 299
            self.mono[0] += 299

        timer = threading.Timer(0.3, lambda: self.halt.set())
        self.addCleanup(timer.cancel)
        timer.start()
        result, elapsed = self.sleep(300, almost)
        self.assertTrue(result)  # still sleeping when halted
        self.assertGreaterEqual(elapsed, 0.25)


class TestSuspension(ChannelTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.recorder = SleepRecorder()
        patcher = self.recorder.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        step = mock.patch.object(Suspension, "WAIT_STEP", 0.01)
        step.start()
        self.addCleanup(step.stop)

    def shared(self, slots: int) -> Shared:
        return Shared(suspension=Suspension(slots=slots, clock=self.clock))

    def test_single_slot_restriction_starts_the_suspension(self):
        shared = self.shared(1)
        channel, result = self.run_channel(
            FakeProcessor({"https://c": unavailable()}), shared=shared
        )
        self.assertFalse(result)
        self.assertTrue(shared.suspension.active())
        self.assertEqual(channel.get_last_error(), "abc: Video unavailable")
        self.assertEqual(self.recorder.sleeps, [])  # no grace pause

    def test_restriction_in_one_of_two_slots_only_pauses(self):
        shared = self.shared(2)
        self.run_channel(FakeProcessor({"https://c": unavailable()}), shared=shared)
        self.assertFalse(shared.suspension.active())
        self.assertEqual(self.recorder.sleeps, [Channel.RESTRICTION_PAUSE])

    def test_restrictions_in_both_slots_start_the_suspension(self):
        shared = self.shared(2)
        processor = FakeProcessor({"https://c": unavailable()})
        self.run_channel(processor, shared=shared, slot=0)
        self.assertFalse(shared.suspension.active())
        self.run_channel(processor, shared=shared, slot=1)
        self.assertTrue(shared.suspension.active())
        self.assertEqual(self.recorder.sleeps, [Channel.RESTRICTION_PAUSE])

    def test_the_same_slot_twice_does_not_start_the_suspension(self):
        shared = self.shared(2)
        processor = FakeProcessor({"https://c": unavailable()})
        self.run_channel(processor, shared=shared, slot=0)
        self.run_channel(processor, shared=shared, slot=0)
        self.assertFalse(shared.suspension.active())

    def test_rate_limits_and_bot_checks_are_restrictions(self):
        for text in (
            "Sign in to confirm you’re not a bot",
            "This content isn't available, try again later.",
            "HTTP Error 429: Too Many Requests",
        ):
            with self.subTest(text=text):
                shared = self.shared(1)
                error = download_error(ExtractorError(text, expected=True))
                self.run_channel(FakeProcessor({"https://c": error}), shared=shared)
                self.assertTrue(shared.suspension.active())

    def test_permanent_errors_do_not_count(self):
        shared = self.shared(1)
        for text in ("Private video", "This video has been removed by the uploader"):
            error = download_error(ExtractorError(text, expected=True))
            self.run_channel(FakeProcessor({"https://c": error}), shared=shared)
        self.assertFalse(shared.suspension.active())
        self.assertEqual(shared.suspension.restricted_slots(), 0)
        self.assertEqual(self.recorder.sleeps, [])

    def test_an_extracted_video_clears_the_slot(self):
        shared = self.shared(2)
        self.run_channel(FakeProcessor({"https://c": unavailable()}), shared=shared)
        self.assertEqual(shared.suspension.restricted_slots(), 1)
        self.run_channel(FakeProcessor({"https://c": video("v")}), shared=shared)
        self.assertEqual(shared.suspension.restricted_slots(), 0)
        self.run_channel(
            FakeProcessor({"https://c": unavailable()}), shared=shared, slot=1
        )
        self.assertFalse(shared.suspension.active())

    def test_the_next_entry_waits_for_the_suspension(self):
        shared = self.shared(1)
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": unavailable("a"),
                "https://v/b": video("b"),
            }
        )

        def end_suspension() -> None:
            self.clock.now += Suspension.DURATION

        timer = threading.Timer(0.2, end_suspension)
        self.addCleanup(timer.cancel)
        timer.start()
        started = time.monotonic()
        _, result = self.run_channel(processor, shared=shared)
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        self.assertTrue(result)
        self.assertEqual(self.downloaded_ids(processor), ["b"])
        required = [
            m
            for m in self.messages(SleepMessage)
            if m.required and m.provider == Message.Provider.CHANNEL
        ]
        self.assertEqual(len(required), 1)
        self.assertEqual(required[0].sleep_time, Suspension.DURATION)

    def test_halt_or_drain_ends_the_suspension_wait(self):
        for stop in ("halt", "drain"):
            with self.subTest(stop=stop):
                self.halt.clear()
                shared = self.shared(1)
                event = self.halt if stop == "halt" else shared.drain_event
                processor = FakeProcessor(
                    {
                        "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                        "https://v/a": unavailable("a"),
                        "https://v/b": video("b"),
                    }
                )
                timer = threading.Timer(0.1, event.set)
                self.addCleanup(timer.cancel)
                timer.start()
                channel, result = self.run_channel(processor, shared=shared)
                self.assertFalse(result)
                self.assertTrue(channel.interrupted())
                self.assertEqual(processor.downloaded, [])
                self.assertIsNone(channel.get_last_error())
                self.assertEqual(channel.row()[2], 0)


class TestDrain(ChannelTestCase):
    def setUp(self):
        super().setUp()
        self.shared = Shared()

    def test_the_running_download_completes(self):
        processor = FakeProcessor({"https://c": video("vid")})
        processor.on_download = lambda _: self.shared.drain_event.set()
        channel, result = self.run_channel(processor, shared=self.shared)
        self.assertTrue(result)
        self.assertEqual(processor.downloaded, ["https://v/vid"])
        self.assertGreater(cast(int, channel.row()[2]), 0)
        # The pause before the next download is skipped
        channel_sleeps = [
            m
            for m in self.messages(SleepMessage)
            if m.provider == Message.Provider.CHANNEL
        ]
        self.assertEqual(channel_sleeps, [])

    def test_stops_before_the_next_entry(self):
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": video("a"),
                "https://v/b": video("b"),
            }
        )
        processor.on_download = lambda _: self.shared.drain_event.set()
        channel = Channel("https://c", None, 100, 100, "previous")
        channel.set_halt_event(self.halt)
        channel.set_shared(self.shared)
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertEqual(self.downloaded_ids(processor), ["a"])
        self.assertTrue(channel.interrupted())
        # Neither a download nor an error is recorded
        self.assertEqual(channel.row()[2], 100)
        self.assertEqual(channel.get_last_error(), "previous")

    def test_a_drained_channel_does_not_start(self):
        self.shared.drain_event.set()
        processor = FakeProcessor({"https://c": video("vid")})
        channel, result = self.run_channel(processor, shared=self.shared)
        self.assertFalse(result)
        self.assertEqual(processor.extracted, [])
        self.assertIsNone(channel.get_last_error())
        self.assertEqual(drain(self.queue), [])

    def test_drain_ends_the_sleep_after_download(self):
        Config.settings["Download"]["sleep_interval"] = 30
        processor = FakeProcessor({"https://c": video("vid")})
        timer = threading.Timer(0.2, self.shared.drain_event.set)
        self.addCleanup(timer.cancel)
        processor.on_download = lambda _: timer.start()
        started = time.monotonic()
        _, result = self.run_channel(processor, shared=self.shared)
        self.assertTrue(result)
        self.assertLess(time.monotonic() - started, 5)

    def test_cleared_drain_continues_normally(self):
        self.shared.drain_event.set()
        self.shared.drain_event.clear()
        processor = FakeProcessor({"https://c": video("vid")})
        self.assertTrue(self.run_channel(processor, shared=self.shared)[1])


class TestScheduledVideo(ChannelTestCase):
    def scheduled(self, delay: float) -> FakeProcessor:
        available_at = int(time.time() + delay)
        formats = [
            {"ext": "mp4", "width": 1920, "height": 1080, "available_at": available_at}
        ]
        return FakeProcessor({"https://c": video("vid", formats=formats)})

    def test_the_wait_is_done_outside_yt_dlp(self):
        recorder = SleepRecorder()
        processor = self.scheduled(100)
        with recorder.patch():
            _, result = self.run_channel(processor)
        self.assertTrue(result)
        self.assertEqual(len(recorder.sleeps), 2)  # the wait, and the pause after
        self.assertGreater(recorder.sleeps[0], 95)
        self.assertLessEqual(recorder.sleeps[0], 100)
        (pre_sleep,) = [
            m
            for m in self.messages(SleepMessage)
            if m.provider == Message.Provider.DOWNLOAD
        ]
        self.assertTrue(pre_sleep.required)

    def test_halt_or_drain_ends_the_wait_without_downloading(self):
        for stop in ("halt", "drain"):
            with self.subTest(stop=stop):
                self.halt.clear()
                shared = Shared()
                event = self.halt if stop == "halt" else shared.drain_event
                processor = self.scheduled(60)
                timer = threading.Timer(0.1, event.set)
                self.addCleanup(timer.cancel)
                timer.start()
                started = time.monotonic()
                channel, result = self.run_channel(processor, shared=shared)
                self.assertLess(time.monotonic() - started, 5)
                self.assertFalse(result)
                self.assertTrue(channel.interrupted())
                self.assertEqual(processor.downloaded, [])
                self.assertIsNone(channel.get_last_error())

    def test_available_videos_are_not_waited_for(self):
        recorder = SleepRecorder()
        with recorder.patch():
            self.run_channel(self.scheduled(-100))
        self.assertEqual(len(recorder.sleeps), 1)  # only the pause after


class FakeMetadataPP:
    """Records the contexts set by the channel."""

    def __init__(self) -> None:
        self.contexts: list[MetadataContext | None] = []

    def set_context(self, context: MetadataContext | None) -> None:
        self.contexts.append(context)

    def current(self) -> MetadataContext | None:
        return self.contexts[-1] if self.contexts else None


AVATAR = "https://yt3.example/avatar=s0"
CHANNEL = "UC" + "x" * 22
UPLOADS_PLAYLIST = "UU" + "x" * 22
UPLOADS_URL = f"https://www.youtube.com/playlist?list={UPLOADS_PLAYLIST}"


class TestMetadata(ChannelTestCase):
    def setUp(self):
        super().setUp()
        Config.settings["Download"].update(metadata=True, thumbnail="channel")
        self.pp = FakeMetadataPP()
        self.seen: dict[str, MetadataContext | None] = {}

    def download(
        self, infos: dict[str, object], url: str = "https://c"
    ) -> FakeProcessor:
        processor = FakeProcessor(infos)
        processor.on_download = lambda url: self.seen.__setitem__(
            url.rsplit("/", 1)[1], self.pp.current()
        )
        self.run_channel(
            processor, url=url, metadata=cast(MetadataPostProcessor, self.pp)
        )
        return processor

    def channel_info(self, urls: list[str]) -> dict[str, object]:
        return playlist(
            CHANNEL,
            urls,
            extractor_key="YoutubeTab",
            channel_id=CHANNEL,
            channel="Chan",
            thumbnails=[{"id": "avatar_uncropped", "url": AVATAR}],
        )

    def uploads(self, *ids: str) -> dict[str, object]:
        """The uploads of the channel, newest first, as the infos of the
        processor: the address is listed in the infos."""
        urls = [f"https://v/{i}" for i in ids]
        info = playlist(
            UPLOADS_PLAYLIST,
            urls,
            extractor_key="YoutubeTab",
            playlist_count=len(urls),
        )
        return {UPLOADS_URL: info}

    def episodes(self) -> dict[str, int | None]:
        return {
            k: cast(MetadataContext, v).episode_number for k, v in self.seen.items()
        }

    def test_the_first_video_is_episode_1_and_the_next_2(self):
        urls = [f"https://v/{v}" for v in "abc"]  # newest first, as listed
        infos: dict[str, object] = {"https://c": self.channel_info(urls)}
        infos.update({url: video(url[-1]) for url in urls})
        infos.update(self.uploads("a", "b", "c"))
        self.download(infos)
        self.assertEqual(self.episodes(), {"a": 3, "b": 2, "c": 1})
        context = cast(MetadataContext, self.seen["a"])
        self.assertEqual((context.series, context.thumbnail_url), ("Chan", AVATAR))
        self.assertEqual(context.episode_order, "uploads")
        # The context is cleared after each download
        self.assertIsNone(self.pp.contexts[-1])

    def test_all_the_tabs_of_a_channel_are_one_sequence(self):
        # The order of publication over videos, shorts and streams together,
        # not the position within a tab
        infos: dict[str, object] = {
            "https://c": self.channel_info(["https://c/videos", "https://c/shorts"]),
            "https://c/videos": playlist(CHANNEL, ["https://v/va", "https://v/vb"]),
            "https://c/shorts": playlist(CHANNEL, ["https://v/sa", "https://v/sb"]),
            "https://v/va": video("va"),
            "https://v/vb": video("vb"),
            "https://v/sa": video("sa"),
            "https://v/sb": video("sb"),
        }
        infos.update(self.uploads("sa", "va", "sb", "vb"))
        self.download(infos)
        self.assertEqual(self.episodes(), {"sa": 4, "va": 3, "sb": 2, "vb": 1})
        self.assertTrue(
            all(cast(MetadataContext, v).series == "Chan" for v in self.seen.values())
        )

    def test_a_tab_on_its_own_is_numbered_by_the_uploads_too(self):
        infos: dict[str, object] = {
            "https://c/shorts": playlist(
                CHANNEL,
                ["https://v/s2", "https://v/s1"],
                extractor_key="YoutubeTab",
                channel_id=CHANNEL,
            ),
            "https://v/s2": video("s2"),
            "https://v/s1": video("s1"),
        }
        infos.update(self.uploads("s2", "v1", "s1"))
        self.download(infos, url="https://c/shorts")
        self.assertEqual(self.episodes(), {"s2": 3, "s1": 1})

    def test_a_playlist_is_numbered_in_its_own_order(self):
        urls = [f"https://v/{v}" for v in "abc"]
        infos: dict[str, object] = {
            "https://p": playlist("PLabc123", urls, extractor_key="YoutubeTab"),
            **{url: video(url[-1]) for url in urls},
        }
        self.download(infos, url="https://p")
        self.assertEqual(self.episodes(), {"a": 1, "b": 2, "c": 3})
        self.assertEqual(
            cast(MetadataContext, self.seen["a"]).episode_order, "playlist"
        )
        self.assertNotIn(UPLOADS_URL, infos)  # nothing else was asked for

    def test_other_sites_have_no_episodes(self):
        urls = ["https://v/a", "https://v/b"]
        infos: dict[str, object] = {
            "https://c": playlist("pl", urls, extractor_key="Vimeo"),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
        }
        self.download(infos)
        self.assertEqual(self.episodes(), {"a": None, "b": None})

    def test_the_uploads_are_only_listed_when_there_is_a_download(self):
        # Every video is rejected (vertical), so nothing is downloaded
        urls = ["https://v/a", "https://v/b"]
        infos: dict[str, object] = {
            "https://c": self.channel_info(urls),
            "https://v/a": video("a", width=1080, height=1920),
            "https://v/b": video("b", width=1080, height=1920),
            **self.uploads("a", "b"),
        }
        processor = self.download(infos)
        self.assertEqual(processor.downloaded, [])
        self.assertNotIn(UPLOADS_URL, processor.extracted)

    def test_the_uploads_are_listed_once_for_all_videos(self):
        urls = [f"https://v/{v}" for v in "abc"]
        infos: dict[str, object] = {"https://c": self.channel_info(urls)}
        infos.update({url: video(url[-1]) for url in urls})
        infos.update(self.uploads("a", "b", "c"))
        processor = self.download(infos)
        self.assertEqual(processor.extracted.count(UPLOADS_URL), 1)

    def test_the_uploads_are_listed_again_on_the_next_run(self):
        # A video was published meanwhile: the numbers of the old ones stay
        infos: dict[str, object] = {
            "https://c": self.channel_info(["https://v/a", "https://v/b"]),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
            **self.uploads("a", "b"),
        }
        processor = FakeProcessor(infos)
        processor.on_download = lambda url: self.seen.__setitem__(
            url.rsplit("/", 1)[1], self.pp.current()
        )
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_metadata_processor(cast(MetadataPostProcessor, self.pp))
        channel.set_active()
        channel.download(0, processor.processor, self.queue)
        self.assertEqual(self.episodes(), {"a": 2, "b": 1})
        processor.infos["https://c"] = self.channel_info(
            ["https://v/n", "https://v/a", "https://v/b"]
        )
        processor.infos["https://v/n"] = video("n")
        processor.infos.update(self.uploads("n", "a", "b"))
        self.seen.clear()
        channel.set_active()
        channel.download(0, processor.processor, self.queue)
        self.assertEqual(self.episodes(), {"n": 3, "a": 2, "b": 1})
        self.assertEqual(processor.extracted.count(UPLOADS_URL), 2)

    def test_a_list_that_cannot_be_loaded_gives_no_number(self):
        urls = ["https://v/a", "https://v/b"]
        infos: dict[str, object] = {
            "https://c": self.channel_info(urls),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
            UPLOADS_URL: download_error(ExtractorError("boom", expected=True)),
        }
        processor = self.download(infos)
        # The videos are downloaded, with no number rather than a wrong one
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])
        self.assertEqual(self.episodes(), {"a": None, "b": None})
        self.assertEqual(processor.extracted.count(UPLOADS_URL), 1)  # not again

    def test_an_interrupted_list_gives_no_number(self):
        urls = ["https://v/a", "https://v/b"]
        info = self.uploads("a", "b")
        cast("dict[str, Any]", info[UPLOADS_URL])["playlist_count"] = 5
        infos: dict[str, object] = {
            "https://c": self.channel_info(urls),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
            **info,
        }
        processor = self.download(infos)
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])
        self.assertEqual(self.episodes(), {"a": None, "b": None})

    def test_a_video_that_is_not_in_the_list_has_no_number(self):
        infos: dict[str, object] = {
            "https://c": self.channel_info(["https://v/a"]),
            "https://v/a": video("a"),
            **self.uploads("other"),
        }
        self.download(infos)
        self.assertEqual(self.episodes(), {"a": None})

    def test_the_panel_shows_the_episode_before_the_download(self):
        urls = ["https://v/a", "https://v/b"]
        infos: dict[str, object] = {
            "https://c": self.channel_info(urls),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
            **self.uploads("a", "b"),
        }
        self.download(infos)
        shown = {
            m.entity.id: m.entity.episode
            for m in self.messages(EntityMessage)
            if m.entity.episode
        }
        self.assertEqual(shown, {"a": 2, "b": 1})

    def test_no_episodes_without_metadata(self):
        Config.settings["Download"]["metadata"] = False
        urls = ["https://v/a"]
        infos: dict[str, object] = {
            "https://c": self.channel_info(urls),
            "https://v/a": video("a"),
            **self.uploads("a"),
        }
        processor = self.download(infos)
        self.assertNotIn(UPLOADS_URL, processor.extracted)
        self.assertEqual(
            [m for m in self.messages(EntityMessage) if m.entity.episode], []
        )

    def test_a_single_video_has_no_episode(self):
        self.download({"https://c": video("vid", channel="Chan")})
        context = cast(MetadataContext, self.seen["vid"])
        self.assertIsNone(context.episode_number)
        self.assertEqual(context.series, "Chan")
        self.assertEqual(context.season_number, 1)

    def test_the_channel_description_is_the_one_of_the_channel(self):
        info = self.channel_info(["https://v/a"])
        info["description"] = "  About the channel \n"
        self.download({"https://c": info, "https://v/a": video("a")})
        context = cast(MetadataContext, self.seen["a"])
        self.assertEqual(context.channel_description, "About the channel")

    def test_the_description_of_a_single_video_is_not_a_channel_description(self):
        self.download(
            {"https://c": video("vid", channel="Chan", description="The video")}
        )
        context = cast(MetadataContext, self.seen["vid"])
        self.assertIsNone(context.channel_description)

    def test_groups_of_tabs_and_playlists(self):
        infos: dict[str, object] = {
            "https://c": self.channel_info(
                [
                    "https://c/videos",
                    "https://c/shorts",
                    "https://c/streams",
                    "https://c/playlist?list=PLabc",
                ]
            ),
            "https://c/videos": playlist("v", ["https://v/va"], title="Chan - Videos"),
            "https://c/shorts": playlist("s", ["https://v/sa"]),
            "https://c/streams": playlist("l", ["https://v/la"]),
            "https://c/playlist?list=PLabc": playlist(
                "PLabc", ["https://v/pa"], title="  My   favourite\nvideos "
            ),
            "https://v/va": video("va"),
            "https://v/sa": video("sa"),
            "https://v/la": video("la"),
            "https://v/pa": video("pa"),
        }
        self.download(infos)
        groups = {k: cast(MetadataContext, v).group for k, v in self.seen.items()}
        self.assertEqual(
            groups,
            {
                "va": None,  # the plain videos tab is no grouping
                "sa": "Shorts",
                "la": "Live",
                "pa": "My favourite videos",
            },
        )

    def test_a_playlist_url_gives_its_title_as_group(self):
        url = "https://www.youtube.com/playlist?list=PLabc"
        infos: dict[str, object] = {
            url: playlist("PLabc", ["https://v/a"], title="Road trip"),
            "https://v/a": video("a"),
        }
        self.download(infos, url=url)
        self.assertEqual(cast(MetadataContext, self.seen["a"]).group, "Road trip")

    def test_a_channel_without_tab_has_no_group(self):
        infos: dict[str, object] = {
            "https://www.youtube.com/@chan": playlist("c", ["https://v/a"]),
            "https://v/a": video("a"),
        }
        self.download(infos, url="https://www.youtube.com/@chan")
        self.assertIsNone(cast(MetadataContext, self.seen["a"]).group)

    def test_unknown_urls_have_no_group(self):
        for url in ("https://c", "https://c/", "https://[bad", "not a url"):
            with self.subTest(url=url):
                self.seen.clear()
                infos: dict[str, object] = {
                    url: playlist("p", ["https://v/a"]),
                    "https://v/a": video("a"),
                }
                self.download(infos, url=url)
                self.assertIsNone(cast(MetadataContext, self.seen["a"]).group)

    def test_disabled_options(self):
        Config.settings["Download"].update(metadata=False, thumbnail="video")
        urls = ["https://v/a"]
        self.download({"https://c": self.channel_info(urls), "https://v/a": video("a")})
        self.assertEqual(self.seen["a"], MetadataContext())

    def test_without_a_metadata_processor_nothing_is_set(self):
        processor = FakeProcessor({"https://c": video("vid")})
        self.assertTrue(self.run_channel(processor)[1])


class FakePreflight:
    """Stands in for Preflight: finish() is recorded, on_start may raise."""

    def __init__(self) -> None:
        self.finished = 0

    def finish(self) -> None:
        self.finished += 1


class TempProcessor(FakeProcessor):
    """A processor whose temporary files live in a real directory."""

    def __init__(
        self,
        temp: str,
        infos: Mapping[str, object] | None = None,
        archive: bool = False,
    ) -> None:
        super().__init__(infos, archive)
        self.temp = temp

    def prepare_filename(self, info: dict[str, object], dir_type: str = "") -> str:
        return os.path.join(self.temp, "Chan", "2024-01", f"{info.get('id')}.NA")


class TestPreflight(ChannelTestCase):
    def setUp(self):
        super().setUp()
        self.temp = self.temp_dir()
        Config.settings["Paths"]["temporary"] = self.temp
        self.preflight = FakePreflight()
        self.slot_messages: list[Message] = []

    def refuse(
        self, error: BaseException, archive: bool = True, url: str = "https://c"
    ) -> tuple[Channel, bool, TempProcessor]:
        processor = TempProcessor(
            self.temp, {url: video("vid", NOW, channel="C")}, archive=archive
        )

        def refuse(_: str) -> None:
            raise error

        processor.on_download = refuse
        channel = Channel(url, None, 100, 200, "old")
        channel.set_halt_event(self.halt)
        channel.set_preflight(cast(Preflight, self.preflight))
        channel.set_active()
        result = channel.download(0, processor.processor, self.queue)
        return channel, result, processor

    def write(self, *parts: str) -> str:
        path = os.path.join(self.temp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            _ = f.write(b"x")
        return path

    def test_a_video_that_is_too_large_is_archived_and_skipped(self):
        error = PreflightError(
            PreflightReason.TOO_LARGE, "Too large (14G)", archive=True
        )
        channel, result, processor = self.refuse(error)
        self.assertFalse(result)
        self.assertEqual(processor.archived, ["vid"])
        self.assertEqual(channel.get_last_error(), "Too large (14G)")
        (message,) = self.messages(ErrorMessage)
        self.assertEqual((message.target, message.message), ("vid", "Too large (14G)"))
        self.assertEqual(channel.row()[2], 100)  # no download is recorded
        self.assertEqual(self.preflight.finished, 1)

    def test_other_refusals_are_not_archived(self):
        for reason, text in (
            (PreflightReason.HLS_ONLY, "HLS only, poor quality"),
            (PreflightReason.NO_SPACE, "No space in temp: need 12G, free 8G"),
        ):
            with self.subTest(reason=reason):
                error = PreflightError(reason, text, retry=True)
                channel, result, processor = self.refuse(error)
                self.assertFalse(result)
                self.assertEqual(processor.archived, [])
                self.assertEqual(channel.get_last_error(), text)

    def test_a_refusal_needs_no_pause_afterwards(self):
        Config.settings["Download"]["sleep_interval"] = 30
        error = PreflightError(PreflightReason.HLS_ONLY, "HLS only, poor quality")
        started = time.monotonic()
        self.refuse(error)
        self.assertLess(time.monotonic() - started, 5)
        sleeps = [
            m
            for m in self.messages(SleepMessage)
            if m.provider == Message.Provider.CHANNEL
        ]
        self.assertEqual(sleeps, [])

    def test_an_interruption_is_neither_an_error_nor_a_download(self):
        channel, result, processor = self.refuse(PreflightInterrupted())
        self.assertFalse(result)
        self.assertTrue(channel.interrupted())
        self.assertEqual(channel.get_last_error(), "old")
        self.assertEqual(channel.queue_key(), (200, 100))  # keeps its place
        self.assertEqual(self.messages(ErrorMessage), [])
        self.assertEqual(processor.archived, [])
        self.assertEqual(self.preflight.finished, 1)

    def test_finish_is_called_after_a_download_too(self):
        processor = TempProcessor(self.temp, {"https://c": video("vid")})
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_preflight(cast(Preflight, self.preflight))
        self.assertTrue(channel.download(0, processor.processor, self.queue))
        self.assertEqual(self.preflight.finished, 1)

    def test_finish_is_called_when_the_download_fails(self):
        error = download_error(ExtractorError("gone", video_id="vid", expected=True))
        processor = TempProcessor(self.temp, {"https://c": video("vid")})
        processor.download_result = error
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_preflight(cast(Preflight, self.preflight))
        self.assertFalse(channel.download(0, processor.processor, self.queue))
        self.assertEqual(self.preflight.finished, 1)

    def test_leftovers_of_a_refused_video_are_removed(self):
        subtitle = self.write("Chan", "2024-01", "vid.en.vtt")
        thumbnail = self.write("Chan", "2024-01", "vid.jpg")
        other = self.write("Chan", "2024-01", "other.en.vtt")
        longer = self.write("Chan", "2024-01", "vid2.jpg")
        elsewhere = self.write("Other", "vid.jpg")
        error = PreflightError(PreflightReason.HLS_ONLY, "HLS only, poor quality")
        self.refuse(error)
        self.assertFalse(os.path.exists(subtitle))
        self.assertFalse(os.path.exists(thumbnail))
        # Only files of this video are removed
        for path in (other, longer, elsewhere):
            self.assertTrue(os.path.exists(path), path)

    def test_leftovers_are_removed_when_interrupted_too(self):
        subtitle = self.write("Chan", "2024-01", "vid.en.vtt")
        self.refuse(PreflightInterrupted())
        self.assertFalse(os.path.exists(subtitle))

    def test_nothing_outside_the_temporary_directory_is_removed(self):
        outside = self.temp_dir()
        path = os.path.join(outside, "vid.jpg")
        with open(path, "wb") as f:
            _ = f.write(b"x")
        # The processor names its temporary files outside the temporary directory
        processor = TempProcessor(outside, {"https://c": video("vid")})
        processor.prepare_filename = lambda info, dir_type="": path  # type: ignore[method-assign]
        processor.on_download = lambda _: (_ for _ in ()).throw(
            PreflightError(PreflightReason.HLS_ONLY, "HLS only, poor quality")
        )
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_active()
        channel.download(0, processor.processor, self.queue)
        self.assertTrue(os.path.exists(path))

    def test_files_in_the_root_of_the_temporary_directory_are_kept(self):
        path = self.write("vid.jpg")
        processor = TempProcessor(self.temp, {"https://c": video("vid")})
        processor.prepare_filename = lambda info, dir_type="": os.path.join(  # type: ignore[method-assign]
            self.temp, "vid.NA"
        )
        processor.on_download = lambda _: (_ for _ in ()).throw(
            PreflightError(PreflightReason.HLS_ONLY, "HLS only, poor quality")
        )
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_active()
        channel.download(0, processor.processor, self.queue)
        self.assertTrue(os.path.exists(path))

    def test_a_refusal_inside_a_playlist_goes_on_with_the_next_entry(self):
        infos: dict[str, object] = {
            "https://c": playlist("p", ["https://v/a", "https://v/b"]),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
        }
        processor = TempProcessor(self.temp, infos)

        def refuse_first(url: str) -> None:
            if url.endswith("/a"):
                raise PreflightError(
                    PreflightReason.TOO_LARGE, "Too large (14G)", archive=True
                )

        processor.on_download = refuse_first
        channel = Channel("https://c", None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_preflight(cast(Preflight, self.preflight))
        channel.set_active()
        self.assertTrue(channel.download(0, processor.processor, self.queue))
        self.assertEqual(self.downloaded_ids(processor), ["a", "b"])
        self.assertEqual(self.preflight.finished, 2)


class FakeCookies:
    """Stands in for CookieWatch: returns the given results one by one."""

    def __init__(self, *results: Refresh) -> None:
        self.results = list(results)
        self.processors: list[object] = []

    def refresh(self, processor: object) -> Refresh:
        self.processors.append(processor)
        return self.results.pop(0) if self.results else Refresh.NOT_NEEDED


class TestCookies(ChannelTestCase):
    def run_with(
        self, cookies: FakeCookies, processor: FakeProcessor, url: str = "https://c"
    ) -> Channel:
        channel = Channel(url, None, None, None, None)
        channel.set_halt_event(self.halt)
        channel.set_cookies(cast(CookieWatch, cookies))
        channel.set_active()
        channel.download(0, processor.processor, self.queue)
        return channel

    def warnings(self) -> list[tuple[str, str]]:
        return [(m.target, m.message) for m in self.messages(WarningMessage)]

    def test_checked_before_extracting_and_after_the_download(self):
        cookies = FakeCookies()
        processor = FakeProcessor({"https://c": video("vid")})
        self.run_with(cookies, processor)
        self.assertEqual(cookies.processors, [processor.processor] * 2)
        self.assertEqual(self.warnings(), [])

    def test_checked_for_each_entry_of_a_playlist(self):
        cookies = FakeCookies()
        infos: dict[str, object] = {
            "https://c": playlist("p", ["https://v/a", "https://v/b"]),
            "https://v/a": video("a"),
            "https://v/b": video("b"),
        }
        self.run_with(cookies, FakeProcessor(infos))
        # The playlist, and for each video before extracting and after
        self.assertEqual(len(cookies.processors), 1 + 2 * 2)

    def test_the_result_is_shown_in_the_panel(self):
        for result, text in (
            (Refresh.REFRESHED, "Rotated, reloaded from the browser"),
            (Refresh.UNCHANGED, "Expired, log in again in the browser"),
            (Refresh.FAILED, "Rotated, reading the browser failed"),
        ):
            with self.subTest(result=result):
                cookies = FakeCookies(result)
                self.run_with(cookies, FakeProcessor({"https://c": video("vid")}))
                self.assertEqual(self.warnings(), [("Cookies", text)])

    def test_nothing_is_shown_when_nothing_happened(self):
        for result in (Refresh.NOT_NEEDED, Refresh.TOO_SOON):
            with self.subTest(result=result):
                cookies = FakeCookies(result, result)
                self.run_with(cookies, FakeProcessor({"https://c": video("vid")}))
                self.assertEqual(self.warnings(), [])

    def test_a_refresh_does_not_stop_the_download(self):
        cookies = FakeCookies(Refresh.REFRESHED)
        processor = FakeProcessor({"https://c": video("vid")})
        channel = self.run_with(cookies, processor)
        self.assertEqual(self.downloaded_ids(processor), ["vid"])
        self.assertIsNone(channel.get_last_error())

    def test_the_watch_goes_to_the_sub_channels(self):
        cookies = FakeCookies()
        infos: dict[str, object] = {
            "https://c": playlist("p", ["https://v/a"]),
            "https://v/a": video("a"),
        }
        self.run_with(cookies, FakeProcessor(infos))
        self.assertGreater(len(cookies.processors), 1)

    def test_no_watch_no_check(self):
        processor = FakeProcessor({"https://c": video("vid")})
        self.assertTrue(self.run_channel(processor)[1])
        self.assertEqual(self.warnings(), [])


class TestChannelHelper(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.temp_dir()
        self.file = os.path.join(self.directory, "channels")

    def write(self, content: str) -> None:
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(content)

    def test_load_pads_truncates_and_skips_rows(self):
        self.write(
            "#;URL;Last Download;Last Attempt;Last Error Message\n"
            "  # indented comment\n"
            "https://only.name\n"
            "Two;https://two\n"
            "Five;https://five;10;20;err\n"
            "Six;https://six;10;20;err;\n"
            ";;;;\n"
            "\n"
            ";https://nameless;;;\n"
            "  Spaced  ; https://spaced ;;;\n"
        )
        rows = [c.row() for c in ChannelHelper.load_channels(self.file)]
        self.assertEqual(
            rows,
            [
                ["https://only.name", "", 0, 0, ""],
                ["Two", "https://two", 0, 0, ""],
                ["Five", "https://five", 10, 20, "err"],
                ["Six", "https://six", 10, 20, "err"],
                ["", "https://nameless", 0, 0, ""],
                ["Spaced", "https://spaced", 0, 0, ""],
            ],
        )

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ChannelHelper.load_channels(self.file)

    def test_save_and_load_round_trip(self):
        channels = [
            Channel("Semi;colon", "https://a", 1, 2, 'Quote " error'),
            Channel("Plain", None, 0, 0, None),
        ]
        self.assertIsNone(ChannelHelper.save_channels(channels, self.file))
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.readline(), ChannelHelper.header)
        loaded = ChannelHelper.load_channels(self.file)
        self.assertEqual([c.row() for c in loaded], [c.row() for c in channels])

    def test_save_keeps_permissions_and_symlinks(self):
        real = os.path.join(self.directory, "real")
        self.write("")
        os.rename(self.file, real)
        os.chmod(real, 0o640)
        os.symlink(real, self.file)
        self.assertIsNone(
            ChannelHelper.save_channels([Channel("a", None, 0, 0, None)], self.file)
        )
        self.assertTrue(os.path.islink(self.file))
        self.assertEqual(stat.S_IMODE(os.stat(real).st_mode), 0o640)
        self.assertEqual(len(ChannelHelper.load_channels(self.file)), 1)

    def test_save_leaves_no_temporary_files(self):
        ChannelHelper.save_channels([Channel("a", None, 0, 0, None)], self.file)
        self.assertEqual(os.listdir(self.directory), ["channels"])

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions"
    )
    def test_save_error_is_returned(self):
        self.write("")
        os.chmod(self.directory, 0o500)
        try:
            error = ChannelHelper.save_channels(
                [Channel("a", None, 0, 0, None)], self.file
            )
        finally:
            os.chmod(self.directory, 0o700)
        self.assertIsInstance(error, OSError)
        self.assertEqual(os.listdir(self.directory), ["channels"])

    def test_failed_replace_removes_the_temporary_file(self):
        self.write("")
        with mock.patch("vidl.channel.os.replace", side_effect=OSError("disk full")):
            error = ChannelHelper.save_channels(
                [Channel("a", None, 0, 0, None)], self.file
            )
        self.assertEqual(str(error), "disk full")
        self.assertEqual(os.listdir(self.directory), ["channels"])


if __name__ == "__main__":
    unittest.main()
