# pyright: standard
import os
import signal
import unittest
from collections.abc import Callable
from queue import Queue
from typing import ClassVar, cast
from unittest import mock

from yt_dlp import YoutubeDL

from tests.helpers import IsolatedTestCase, drain
from vidl.channel import Channel, ChannelHelper
from vidl.config import Config
from vidl.coordinator import Coordinator
from vidl.message import Message, RedrawMessage


class FakeViewController:
    instances: ClassVar[list["FakeViewController"]] = []

    def __init__(self) -> None:
        self.queue: Queue[Message] = Queue()
        self.input_queue: Queue[str] = Queue()
        self.halted = self.joined = False
        FakeViewController.instances.append(self)

    def get_queue(self) -> Queue[Message]:
        return self.queue

    def get_input_queue(self) -> Queue[str]:
        return self.input_queue

    def halt(self) -> None:
        self.halted = True

    def join(self) -> None:
        self.joined = True


class EmptyPlaylistProcessor:
    """Every URL is an empty playlist, so a download succeeds at once."""

    params: ClassVar[dict[str, object]] = {}

    def extract_info(
        self, url: str, download: bool = False, process: bool = False
    ) -> dict[str, object]:
        return {"_type": "playlist", "id": url, "title": url, "entries": []}


class FakeSlot:
    """Processes channels synchronously, calling on_process afterwards."""

    instances: ClassVar[list["FakeSlot"]] = []
    processed: ClassVar[list[str]] = []
    on_process: ClassVar[Callable[[int], None]] = staticmethod(lambda count: None)
    fail_on_create = False

    def __init__(self, index: int, view_queue: Queue[Message]) -> None:
        if FakeSlot.fail_on_create:
            raise RuntimeError("slot failed")
        self.index = index
        self.halted = self.joined = False
        FakeSlot.instances.append(self)

    def ready(self) -> bool:
        return not self.halted

    def process(self, channel: Channel) -> None:
        processor = cast(YoutubeDL, EmptyPlaylistProcessor())
        channel.download(self.index, processor, Queue())
        FakeSlot.processed.append(str(channel.row()[1]))
        FakeSlot.on_process(len(FakeSlot.processed))

    def halt(self) -> None:
        self.halted = True

    def join(self) -> None:
        self.joined = True


class CoordinatorTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        FakeViewController.instances = []
        FakeSlot.instances = []
        FakeSlot.processed = []
        FakeSlot.on_process = staticmethod(lambda count: None)
        FakeSlot.fail_on_create = False
        for target, fake in (
            ("vidl.coordinator.ViewController", FakeViewController),
            ("vidl.coordinator.Slot", FakeSlot),
        ):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
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

    def quit_after(self, count: int) -> None:
        def on_process(processed: int) -> None:
            if processed == count:
                FakeViewController.instances[0].input_queue.put("q")

        FakeSlot.on_process = staticmethod(on_process)

    def view(self) -> FakeViewController:
        return FakeViewController.instances[0]


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

    def test_failed_setup_stops_the_view(self):
        self.write_channels("a")
        FakeSlot.fail_on_create = True
        with self.assertRaises(RuntimeError):
            Coordinator()
        self.assertTrue(self.view().halted and self.view().joined)


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

    def test_quit_keys(self):
        for key in ("q", "Q", "\x03", "\x04"):
            with self.subTest(key=key):
                FakeViewController.instances = []
                FakeSlot.processed = []

                def on_process(processed: int, key: str = key) -> None:
                    FakeViewController.instances[0].input_queue.put(key)

                FakeSlot.on_process = staticmethod(on_process)
                self.assertEqual(Coordinator().run(), 0)
                self.assertEqual(len(FakeSlot.processed), 1)

    def test_other_keys_are_ignored(self):
        def on_process(processed: int) -> None:
            FakeViewController.instances[0].input_queue.put(
                "x" if processed == 1 else "q"
            )

        FakeSlot.on_process = staticmethod(on_process)
        Coordinator().run()
        self.assertEqual(len(FakeSlot.processed), 2)

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


if __name__ == "__main__":
    unittest.main()
