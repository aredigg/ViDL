# pyright: standard
import os
import signal
import threading
import time
import unittest
from collections.abc import Callable, Sequence
from queue import Queue
from typing import ClassVar, cast
from unittest import mock

from yt_dlp import YoutubeDL

from tests.helpers import IsolatedTestCase, drain
from vidl.channel import Channel, ChannelHelper
from vidl.command import AddChannelCommand, Command, DrainCommand, QuitCommand
from vidl.config import Config
from vidl.coordinator import Coordinator
from vidl.message import (
    HeaderMessage,
    Message,
    NoticeMessage,
    RedrawMessage,
    SleepMessage,
    SystemStatusMessage,
)
from vidl.monitor import SystemStatus
from vidl.shared import Shared


class FakeViewController:
    instances: ClassVar[list["FakeViewController"]] = []

    def __init__(self) -> None:
        self.queue: Queue[Message] = Queue()
        self.command_queue: Queue[Command] = Queue()
        self.halted = self.joined = False
        FakeViewController.instances.append(self)

    def get_queue(self) -> Queue[Message]:
        return self.queue

    def get_command_queue(self) -> Queue[Command]:
        return self.command_queue

    def halt(self) -> None:
        self.halted = True

    def join(self) -> None:
        self.joined = True


class FakeMonitor:
    instances: ClassVar[list["FakeMonitor"]] = []

    def __init__(
        self,
        publish: Callable[[SystemStatus], None],
        paths: Sequence[tuple[str, str]],
        update_check: bool = True,
        network_check: bool = True,
    ) -> None:
        self.publish = publish
        self.paths = list(paths)
        self.update_check, self.network_check = update_check, network_check
        self.started = self.halted = self.joined = False
        self.network_errors = 0
        FakeMonitor.instances.append(self)

    def start(self) -> None:
        self.started = True

    def halt(self) -> None:
        self.halted = True

    def join(self) -> None:
        self.joined = True

    def report_network_error(self) -> None:
        self.network_errors += 1

    def report_network_success(self) -> None:
        self.network_errors = 0


class EmptyPlaylistProcessor:
    """Every URL is an empty playlist, so a download succeeds at once."""

    params: ClassVar[dict[str, object]] = {}

    def extract_info(
        self, url: str, download: bool = False, process: bool = False
    ) -> dict[str, object]:
        return {"_type": "playlist", "id": url, "title": url, "entries": []}


class FakeSlot:
    """Processes channels synchronously, calling on_process afterwards. A slot
    stays busy for busy_seconds after processing, as if still downloading."""

    instances: ClassVar[list["FakeSlot"]] = []
    processed: ClassVar[list[str]] = []
    on_process: ClassVar[Callable[[int], None]] = staticmethod(lambda count: None)
    fail_on_create = False
    busy_seconds = 0.0

    def __init__(
        self, index: int, view_queue: Queue[Message], shared: Shared | None = None
    ) -> None:
        if FakeSlot.fail_on_create:
            raise RuntimeError("slot failed")
        self.index = index
        self.shared = shared
        self.halted = self.joined = False
        self.busy_until = 0.0
        FakeSlot.instances.append(self)

    def ready(self) -> bool:
        return not self.halted and not self.busy()

    def busy(self) -> bool:
        return not self.halted and time.monotonic() < self.busy_until

    def process(self, channel: Channel) -> None:
        processor = cast(YoutubeDL, EmptyPlaylistProcessor())
        channel.download(self.index, processor, Queue())
        FakeSlot.processed.append(str(channel.row()[1]))
        self.busy_until = time.monotonic() + FakeSlot.busy_seconds
        FakeSlot.on_process(len(FakeSlot.processed))

    def halt(self) -> None:
        self.halted = True

    def join(self) -> None:
        self.joined = True


class CoordinatorTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        FakeViewController.instances = []
        FakeMonitor.instances = []
        FakeSlot.instances = []
        FakeSlot.processed = []
        FakeSlot.on_process = staticmethod(lambda count: None)
        FakeSlot.fail_on_create = False
        FakeSlot.busy_seconds = 0.0
        for target, fake in (
            ("vidl.coordinator.ViewController", FakeViewController),
            ("vidl.coordinator.Slot", FakeSlot),
            ("vidl.coordinator.Monitor", FakeMonitor),
        ):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        idle = mock.patch.object(Coordinator, "IDLE_WAIT", 0.01)
        idle.start()
        self.addCleanup(idle.stop)
        handlers = {
            s: signal.getsignal(s)
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGWINCH)
        }

        def restore_signals() -> None:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)

        self.addCleanup(restore_signals)
        self.directory = self.temp_dir()
        self.file = os.path.join(self.directory, "channels")
        Config.settings["Channels"]["file_name"] = self.file

    def write_channels(self, *rows: str) -> None:
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    def command(self, command: Command) -> None:
        FakeViewController.instances[0].command_queue.put(command)

    def quit_after(self, count: int) -> None:
        def on_process(processed: int) -> None:
            if processed == count:
                self.command(QuitCommand())

        FakeSlot.on_process = staticmethod(on_process)

    def quit_later(self, delay: float = 0.3) -> None:
        timer = threading.Timer(delay, self.command, (QuitCommand(),))
        self.addCleanup(timer.cancel)
        timer.start()

    def view(self) -> FakeViewController:
        return FakeViewController.instances[0]

    def messages(self, cls: type) -> list:
        return [m for m in drain(self.view().queue) if isinstance(m, cls)]


class TestStartup(CoordinatorTestCase):
    def test_missing_channels_file(self):
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 1)
        self.assertIsInstance(coordinator.error(), FileNotFoundError)
        self.assertEqual(FakeSlot.instances, [])
        self.assertTrue(self.view().halted and self.view().joined)

    def test_unreadable_channels_file(self):
        os.mkdir(self.file)
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 1)
        self.assertIsInstance(coordinator.error(), OSError)

    def test_channels_file_with_invalid_encoding(self):
        with open(self.file, "wb") as f:
            f.write(b"\xff\xfe\xfa;bad\n")
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 1)
        self.assertIsInstance(coordinator.error(), UnicodeDecodeError)

    def test_no_channels(self):
        self.write_channels("#;URL;Last Download;Last Attempt;Last Error Message")
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 2)
        self.assertEqual(coordinator.error(), "No channels")
        self.assertTrue(self.view().joined)

    def test_number_of_slots(self):
        self.write_channels("a", "b", "c")
        for slots, expected in ((0, 1), (1, 1), (2, 2), (10, 3)):
            with self.subTest(slots=slots):
                FakeSlot.instances = []
                Config.settings["Channels"]["slots"] = slots
                Coordinator()
                self.assertEqual(
                    [s.index for s in FakeSlot.instances], list(range(expected))
                )

    def test_slots_share_one_state_sized_to_the_slots(self):
        self.write_channels("a", "b", "c")
        Config.settings["Channels"]["slots"] = 2
        Coordinator()
        shared = {id(s.shared) for s in FakeSlot.instances}
        self.assertEqual(len(shared), 1)
        backoff = cast(Shared, FakeSlot.instances[0].shared).backoff
        self.assertEqual(backoff.threshold(), 2)  # both of two slots

    def test_failed_setup_stops_the_view(self):
        self.write_channels("a")
        FakeSlot.fail_on_create = True
        with self.assertRaises(RuntimeError):
            Coordinator()
        self.assertTrue(self.view().halted and self.view().joined)
        (monitor,) = FakeMonitor.instances
        self.assertFalse(monitor.started)
        self.assertTrue(monitor.halted and monitor.joined)


class TestMonitor(CoordinatorTestCase):
    def test_monitor_lifecycle_and_paths(self):
        self.write_channels("a")
        Config.settings["Paths"].update(output="/out", temporary="/tmp/vidl-x")
        Config.settings["General"].update(update_check=False, network_check=True)
        self.quit_after(1)
        Coordinator().run()
        (monitor,) = FakeMonitor.instances
        self.assertTrue(monitor.started and monitor.halted and monitor.joined)
        self.assertEqual(monitor.paths, [("dl", "/out"), ("tmp", "/tmp/vidl-x")])
        self.assertEqual((monitor.update_check, monitor.network_check), (False, True))

    def test_monitor_is_not_started_without_channels(self):
        Coordinator().run()
        (monitor,) = FakeMonitor.instances
        self.assertFalse(monitor.started)
        self.assertTrue(monitor.halted and monitor.joined)

    def test_status_is_published_to_the_view(self):
        self.write_channels("a")
        Coordinator()
        status = SystemStatus()
        FakeMonitor.instances[0].publish(status)
        self.assertEqual(
            self.messages(SystemStatusMessage), [SystemStatusMessage(status)]
        )

    def test_slots_report_network_health_to_the_monitor(self):
        self.write_channels("a")
        Coordinator()
        shared = cast(Shared, FakeSlot.instances[0].shared)
        shared.network_error()
        shared.network_error()
        self.assertEqual(FakeMonitor.instances[0].network_errors, 2)
        shared.network_success()
        self.assertEqual(FakeMonitor.instances[0].network_errors, 0)


class TestRun(CoordinatorTestCase):
    def setUp(self):
        super().setUp()
        self.write_channels(
            "C;https://c;300;300;",
            "A;https://a;100;100;",
            "B;https://b;200;200;",
        )

    def test_channels_are_processed_oldest_first_until_quit(self):
        self.quit_after(4)
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 0)
        self.assertIsNone(coordinator.error())
        self.assertEqual(
            FakeSlot.processed, ["https://a", "https://b", "https://c", "https://c"]
        )

    def test_shutdown_stops_everything_and_saves(self):
        self.quit_after(1)
        Coordinator().run()
        self.assertTrue(all(s.halted and s.joined for s in FakeSlot.instances))
        self.assertTrue(self.view().halted and self.view().joined)
        rows = {c.row()[1]: c.row() for c in ChannelHelper.load_channels(self.file)}
        self.assertGreater(cast(int, rows["https://a"][2]), 100)  # download updated
        self.assertEqual(rows["https://b"][2], 200)
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.readline(), ChannelHelper.header)

    def test_quit_command_stops_after_the_current_channel(self):
        self.quit_after(1)
        self.assertEqual(Coordinator().run(), 0)
        self.assertEqual(len(FakeSlot.processed), 1)

    def test_quit_while_idle(self):
        FakeSlot.busy_seconds = 3600  # the only slot never becomes ready again
        self.quit_later(0.1)
        started = time.monotonic()
        self.assertEqual(Coordinator().run(), 0)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(FakeSlot.processed), 1)

    def test_sigterm_handler_stops_the_run(self):
        coordinator = Coordinator()
        FakeSlot.on_process = staticmethod(
            lambda count: coordinator.halt(signal.SIGTERM, None)
        )
        self.assertEqual(signal.getsignal(signal.SIGTERM), coordinator.halt)
        self.assertEqual(coordinator.run(), 0)
        self.assertEqual(len(FakeSlot.processed), 1)

    def test_keyboard_interrupt_stops_gracefully(self):
        def interrupt(processed: int) -> None:
            raise KeyboardInterrupt

        FakeSlot.on_process = staticmethod(interrupt)
        coordinator = Coordinator()
        self.assertEqual(coordinator.run(), 0)
        self.assertTrue(self.view().joined)

    def test_ctrl_c_is_ignored_once_shutting_down(self):
        self.quit_after(1)
        Coordinator().run()
        self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)

    def test_window_resize_requests_a_redraw(self):
        coordinator = Coordinator()
        self.assertEqual(signal.getsignal(signal.SIGWINCH), coordinator.redraw)
        coordinator.redraw(signal.SIGWINCH, None)
        self.assertEqual(drain(self.view().queue), [RedrawMessage()])

    def test_channels_are_saved_periodically(self):
        saves: list[int] = []
        original = ChannelHelper.save_channels

        def save(channels: list[Channel], file_name: str):
            saves.append(len(FakeSlot.processed))
            return original(channels, file_name)

        self.quit_after(Coordinator.SAVE_INTERVAL + 1)
        with mock.patch.object(ChannelHelper, "save_channels", side_effect=save):
            Coordinator().run()
        self.assertEqual(
            saves, [0, Coordinator.SAVE_INTERVAL, Coordinator.SAVE_INTERVAL + 1]
        )

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions"
    )
    def test_save_failure_ends_the_run(self):
        coordinator = Coordinator()
        os.chmod(self.directory, 0o500)
        try:
            status = coordinator.run()
        finally:
            os.chmod(self.directory, 0o700)
        self.assertEqual(status, 3)
        self.assertIsInstance(coordinator.error(), PermissionError)
        self.assertEqual(FakeSlot.processed, [])


class TestDrain(CoordinatorTestCase):
    def setUp(self):
        super().setUp()
        self.write_channels("A;https://a;100;100;", "B;https://b;200;200;")

    def shared(self) -> Shared:
        return cast(Shared, FakeSlot.instances[0].shared)

    def test_no_new_channels_and_quit_once_downloads_finish(self):
        FakeSlot.busy_seconds = 0.2
        FakeSlot.on_process = staticmethod(lambda count: self.command(DrainCommand()))
        coordinator = Coordinator()
        started = time.monotonic()
        self.assertEqual(coordinator.run(), 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.15)  # waited for it
        self.assertIsNone(coordinator.error())
        self.assertEqual(FakeSlot.processed, ["https://a"])
        self.assertTrue(self.shared().drain_event.is_set())
        statuses = [m.status for m in self.messages(HeaderMessage)]
        self.assertTrue(any(s.startswith("Finishing 1 download,") for s in statuses))
        self.assertTrue(all(s.halted for s in FakeSlot.instances))

    def test_quits_at_once_when_nothing_is_running(self):
        FakeSlot.on_process = staticmethod(lambda count: self.command(DrainCommand()))
        self.assertEqual(Coordinator().run(), 0)
        self.assertEqual(FakeSlot.processed, ["https://a"])

    def test_drain_can_be_cancelled(self):
        FakeSlot.busy_seconds = 0.05

        def on_process(count: int) -> None:
            if count == 1:
                self.command(DrainCommand())
                self.command(DrainCommand())
            elif count == 3:
                self.command(QuitCommand())

        FakeSlot.on_process = staticmethod(on_process)
        self.assertEqual(Coordinator().run(), 0)
        self.assertEqual(len(FakeSlot.processed), 3)
        self.assertFalse(self.shared().drain_event.is_set())
        notices = [m.text for m in self.messages(NoticeMessage)]
        self.assertIn("Continuing", notices)

    def test_quit_during_drain_is_immediate(self):
        FakeSlot.busy_seconds = 3600

        def on_process(count: int) -> None:
            self.command(DrainCommand())
            self.command(QuitCommand())

        FakeSlot.on_process = staticmethod(on_process)
        started = time.monotonic()
        self.assertEqual(Coordinator().run(), 0)
        self.assertLess(time.monotonic() - started, 5)


class TestBackoff(CoordinatorTestCase):
    def setUp(self):
        super().setUp()
        self.write_channels("A;https://a;100;100;", "B;https://b;200;200;")
        Config.settings["Channels"]["slots"] = 2

    def test_no_channels_are_started_during_a_back_off(self):
        coordinator = Coordinator()
        backoff = cast(Shared, FakeSlot.instances[0].shared).backoff
        backoff.report_restriction(0)
        backoff.report_restriction(1)
        self.assertTrue(backoff.active())
        self.quit_later(0.3)
        self.assertEqual(coordinator.run(), 0)
        self.assertEqual(FakeSlot.processed, [])
        messages = drain(self.view().queue)
        sleeps = [m for m in messages if isinstance(m, SleepMessage)]
        # Each idle slot is shown the back-off once
        self.assertEqual(sorted(m.index for m in sleeps), [0, 1])
        self.assertTrue(all(m.required and m.sleep_time > 0 for m in sleeps))
        headers = [m for m in messages if isinstance(m, HeaderMessage)]
        self.assertTrue(headers[0].status.startswith("Backing off until"))
        self.assertIn("💤", headers[0].title)

    def test_channels_start_again_when_the_back_off_ends(self):
        coordinator = Coordinator()
        backoff = cast(Shared, FakeSlot.instances[0].shared).backoff
        backoff.report_restriction(0)
        backoff.report_restriction(1)
        self.quit_after(1)
        with mock.patch.object(
            backoff, "active", side_effect=[True, True, False] + [False] * 100
        ):
            self.assertEqual(coordinator.run(), 0)
        self.assertEqual(FakeSlot.processed, ["https://a"])


class TestAddChannel(CoordinatorTestCase):
    def setUp(self):
        super().setUp()
        self.write_channels("Some;https://www.youtube.com/@some;100;100;")

    def run_with(self, *commands: Command) -> Coordinator:
        coordinator = Coordinator()
        for command in commands:
            self.command(command)
        self.quit_later(0.3)
        FakeSlot.busy_seconds = 3600  # nothing is processed after the first channel
        self.assertEqual(coordinator.run(), 0)
        return coordinator

    def notices(self) -> list[tuple[str, bool]]:
        return [(m.text, m.error) for m in self.messages(NoticeMessage)]

    def test_a_new_channel_is_added_and_saved(self):
        self.run_with(AddChannelCommand("https://www.youtube.com/@new"))
        urls = [c.row()[1] for c in ChannelHelper.load_channels(self.file)]
        names = [c.row()[0] for c in ChannelHelper.load_channels(self.file)]
        self.assertIn("https://www.youtube.com/@new", names + urls)
        self.assertIn(("Added https://www.youtube.com/@new", False), self.notices())

    def test_it_is_saved_at_once(self):
        saves: list[list[str]] = []
        original = ChannelHelper.save_channels

        def save(channels: list[Channel], file_name: str):
            saves.append([str(c.row()[0]) for c in channels])
            return original(channels, file_name)

        with (
            mock.patch.object(ChannelHelper, "save_channels", side_effect=save),
            mock.patch.object(Coordinator, "SAVE_INTERVAL", 10**9),
        ):
            self.run_with(AddChannelCommand("https://example.com/new"))
        # Saved at start, when added, and at the end
        self.assertEqual(len(saves), 3)
        self.assertNotIn("https://example.com/new", saves[0])
        self.assertIn("https://example.com/new", saves[1])

    def test_duplicates_are_rejected(self):
        for text in (
            "https://www.youtube.com/@some",
            "https://youtube.com/@some/",
            "HTTPS://WWW.YOUTUBE.COM/@some",
            "https://m.youtube.com/@some",
        ):
            with self.subTest(text=text):
                FakeViewController.instances = []
                self.run_with(AddChannelCommand(text))
                self.assertEqual(len(ChannelHelper.load_channels(self.file)), 1)
                ((notice, error),) = self.notices()
                self.assertTrue(error and notice.startswith("Already listed: "))

    def test_paths_are_case_sensitive(self):
        self.run_with(AddChannelCommand("https://www.youtube.com/@SOME"))
        self.assertEqual(len(ChannelHelper.load_channels(self.file)), 2)

    def test_non_urls_are_rejected(self):
        for text in ("q", "ftp://example.com/x", "https://", "has space https://x"):
            with self.subTest(text=text):
                FakeViewController.instances = []
                self.run_with(AddChannelCommand(text))
                self.assertEqual(len(ChannelHelper.load_channels(self.file)), 1)
                ((notice, error),) = self.notices()
                self.assertTrue(error and notice.startswith("Not added"))

    def test_an_extra_slot_is_started_when_allowed(self):
        Config.settings["Channels"]["slots"] = 2
        self.run_with(AddChannelCommand("https://example.com/new"))
        self.assertEqual([s.index for s in FakeSlot.instances], [0, 1])
        backoff = cast(Shared, FakeSlot.instances[0].shared).backoff
        self.assertEqual(backoff.threshold(), 2)
        self.assertIs(FakeSlot.instances[1].shared, FakeSlot.instances[0].shared)

    def test_no_extra_slot_beyond_the_configured_number(self):
        Config.settings["Channels"]["slots"] = 1
        self.run_with(AddChannelCommand("https://example.com/new"))
        self.assertEqual(len(FakeSlot.instances), 1)


class TestNormalizeUrl(unittest.TestCase):
    def test_normalize_url(self):
        cases = [
            ("https://www.youtube.com/@Some/", "youtube.com/@Some"),
            ("http://M.YouTube.com/@Some", "youtube.com/@Some"),
            ("https://youtube.com/playlist?list=PLx", "youtube.com/playlist?list=PLx"),
            ("https://example.com/a#fragment", "example.com/a"),
            ("  https://example.com  ", "example.com"),
            ("example.com/a", None),
            ("ftp://example.com", None),
            ("https://", None),
            ("https://exa mple.com", None),
            ("https://[::1", None),
            ("", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(Coordinator.normalize_url(text), expected)


if __name__ == "__main__":
    unittest.main()
