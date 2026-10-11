# pyright: standard
import os
import stat
import sys
import threading
import time
import unittest
from queue import Queue
from threading import Event
from typing import Any, TypeVar, cast
from unittest import mock

from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError, ExtractorError

from tests.helpers import FakeProcessor, IsolatedTestCase, drain, playlist, video
from vidl.backoff import Backoff
from vidl.channel import Channel, ChannelHelper
from vidl.config import Config
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
)
from vidl.metadata import MetadataContext, MetadataPostProcessor
from vidl.shared import Shared
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

    def test_last_date_prefers_download_over_attempt(self):
        self.assertEqual(
            Channel("a", None, 100, 200, None).get_last_date().timestamp(), 100
        )
        self.assertEqual(
            Channel("a", None, 0, 200, None).get_last_date().timestamp(), 200
        )
        self.assertEqual(
            Channel("a", None, None, None, None).get_last_date().timestamp(), 0
        )

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

    def test_restriction_without_back_off_pauses_briefly(self):
        # Without shared state there is no back-off, only the grace pause
        error = ExtractorError("Video unavailable", video_id="abc", expected=True)
        processor = FakeProcessor({"https://c": download_error(error)})
        sleeps: list[float] = []

        def sleep(_: Channel, seconds: float) -> bool:
            sleeps.append(seconds)
            return False

        with mock.patch.object(Channel, "_Channel__sleep", sleep):
            channel, _ = self.run_channel(processor)
        self.assertEqual(sleeps, [Channel.BACKOFF_GRACE])
        self.assertEqual(channel.get_last_error(), "abc: Video unavailable")
        self.assertEqual(self.messages(SleepMessage), [])

    def test_halt_during_backoff_grace_skips_the_sleep(self):
        error = ExtractorError("Video unavailable", video_id="abc", expected=True)

        def halted_while_extracting(_: str) -> DownloadError:
            self.halt.set()
            return download_error(error)

        processor = FakeProcessor({"https://c": halted_while_extracting})
        started = time.monotonic()
        _, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertLess(time.monotonic() - started, Channel.BACKOFF_GRACE)
        self.assertEqual(self.messages(SleepMessage), [])

    def test_halted_channel_does_not_extract(self):
        self.halt.set()
        processor = FakeProcessor({"https://c": video("vid")})
        channel, result = self.run_channel(processor)
        self.assertFalse(result)
        self.assertEqual(processor.extracted, [])
        self.assertEqual(channel.get_last_error(), "Got halted")


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


class TestBackoff(ChannelTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.recorder = SleepRecorder()
        patcher = self.recorder.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        step = mock.patch.object(Backoff, "WAIT_STEP", 0.01)
        step.start()
        self.addCleanup(step.stop)

    def shared(self, slots: int) -> Shared:
        return Shared(backoff=Backoff(slots=slots, clock=self.clock))

    def test_single_slot_restriction_starts_the_back_off(self):
        shared = self.shared(1)
        channel, result = self.run_channel(
            FakeProcessor({"https://c": unavailable()}), shared=shared
        )
        self.assertFalse(result)
        self.assertTrue(shared.backoff.active())
        self.assertEqual(channel.get_last_error(), "abc: Video unavailable")
        self.assertEqual(self.recorder.sleeps, [])  # no grace pause

    def test_restriction_in_one_of_two_slots_only_pauses(self):
        shared = self.shared(2)
        self.run_channel(FakeProcessor({"https://c": unavailable()}), shared=shared)
        self.assertFalse(shared.backoff.active())
        self.assertEqual(self.recorder.sleeps, [Channel.BACKOFF_GRACE])

    def test_restrictions_in_both_slots_start_the_back_off(self):
        shared = self.shared(2)
        processor = FakeProcessor({"https://c": unavailable()})
        self.run_channel(processor, shared=shared, slot=0)
        self.assertFalse(shared.backoff.active())
        self.run_channel(processor, shared=shared, slot=1)
        self.assertTrue(shared.backoff.active())
        self.assertEqual(self.recorder.sleeps, [Channel.BACKOFF_GRACE])

    def test_the_same_slot_twice_does_not_start_the_back_off(self):
        shared = self.shared(2)
        processor = FakeProcessor({"https://c": unavailable()})
        self.run_channel(processor, shared=shared, slot=0)
        self.run_channel(processor, shared=shared, slot=0)
        self.assertFalse(shared.backoff.active())

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
                self.assertTrue(shared.backoff.active())

    def test_permanent_errors_do_not_count(self):
        shared = self.shared(1)
        for text in ("Private video", "This video has been removed by the uploader"):
            error = download_error(ExtractorError(text, expected=True))
            self.run_channel(FakeProcessor({"https://c": error}), shared=shared)
        self.assertFalse(shared.backoff.active())
        self.assertEqual(shared.backoff.restricted_slots(), 0)
        self.assertEqual(self.recorder.sleeps, [])

    def test_an_extracted_video_clears_the_slot(self):
        shared = self.shared(2)
        self.run_channel(FakeProcessor({"https://c": unavailable()}), shared=shared)
        self.assertEqual(shared.backoff.restricted_slots(), 1)
        self.run_channel(FakeProcessor({"https://c": video("v")}), shared=shared)
        self.assertEqual(shared.backoff.restricted_slots(), 0)
        self.run_channel(
            FakeProcessor({"https://c": unavailable()}), shared=shared, slot=1
        )
        self.assertFalse(shared.backoff.active())

    def test_the_next_entry_waits_for_the_back_off(self):
        shared = self.shared(1)
        processor = FakeProcessor(
            {
                "https://c": playlist("p", ["https://v/a", "https://v/b"]),
                "https://v/a": unavailable("a"),
                "https://v/b": video("b"),
            }
        )

        def end_back_off() -> None:
            self.clock.now += Backoff.DURATION

        timer = threading.Timer(0.2, end_back_off)
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
        self.assertEqual(required[0].sleep_time, Backoff.DURATION)

    def test_halt_or_drain_ends_the_back_off_wait(self):
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


class TestMetadata(ChannelTestCase):
    def setUp(self):
        super().setUp()
        Config.settings["Download"].update(metadata=True, thumbnail="channel")
        self.pp = FakeMetadataPP()
        self.seen: dict[str, MetadataContext | None] = {}

    def download(self, infos: dict[str, object]) -> FakeProcessor:
        processor = FakeProcessor(infos)
        processor.on_download = lambda url: self.seen.__setitem__(
            url.rsplit("/", 1)[1], self.pp.current()
        )
        self.run_channel(processor, metadata=cast(MetadataPostProcessor, self.pp))
        return processor

    def channel_info(self, urls: list[str]) -> dict[str, object]:
        return playlist(
            "p",
            urls,
            channel="Chan",
            thumbnails=[{"id": "avatar_uncropped", "url": AVATAR}],
        )

    def test_episodes_count_from_the_oldest_entry(self):
        urls = [f"https://v/{v}" for v in "abc"]
        infos: dict[str, object] = {"https://c": self.channel_info(urls)}
        infos.update({url: video(url[-1]) for url in urls})
        self.download(infos)
        self.assertEqual(
            {k: v.episode_number for k, v in self.seen.items() if v},
            {"a": 3, "b": 2, "c": 1},
        )
        context = cast(MetadataContext, self.seen["a"])
        self.assertEqual((context.series, context.thumbnail_url), ("Chan", AVATAR))
        # The context is cleared after each download
        self.assertIsNone(self.pp.contexts[-1])

    def test_nested_playlists_number_within_their_tab(self):
        infos: dict[str, object] = {
            "https://c": self.channel_info(["https://c/videos", "https://c/shorts"]),
            "https://c/videos": playlist("videos", ["https://v/va", "https://v/vb"]),
            "https://c/shorts": playlist("shorts", ["https://v/sa"]),
            "https://v/va": video("va"),
            "https://v/vb": video("vb"),
            "https://v/sa": video("sa"),
        }
        self.download(infos)
        episodes = {
            k: cast(MetadataContext, v).episode_number for k, v in self.seen.items()
        }
        self.assertEqual(episodes, {"va": 2, "vb": 1, "sa": 1})
        self.assertTrue(
            all(cast(MetadataContext, v).series == "Chan" for v in self.seen.values())
        )

    def test_a_single_video_has_no_episode(self):
        self.download({"https://c": video("vid", channel="Chan")})
        context = cast(MetadataContext, self.seen["vid"])
        self.assertIsNone(context.episode_number)
        self.assertEqual(context.series, "Chan")

    def test_disabled_options(self):
        Config.settings["Download"].update(metadata=False, thumbnail="video")
        urls = ["https://v/a"]
        self.download({"https://c": self.channel_info(urls), "https://v/a": video("a")})
        self.assertEqual(self.seen["a"], MetadataContext())

    def test_without_a_metadata_processor_nothing_is_set(self):
        processor = FakeProcessor({"https://c": video("vid")})
        self.assertTrue(self.run_channel(processor)[1])


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
