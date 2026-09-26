# pyright: standard
import unittest
from queue import Queue
from typing import Any, ClassVar
from unittest import mock

from yt_dlp import YoutubeDL

from tests.helpers import IsolatedTestCase, drain, private, wait_until
from vidl.channel import Channel
from vidl.config import Config
from vidl.hook import DownloadCancelled
from vidl.logger import Logger
from vidl.message import ErrorMessage, InitMessage, Message
from vidl.slot import Slot


class FakeYoutubeDL:
    instances: ClassVar[list["FakeYoutubeDL"]] = []
    fail = False

    def __init__(self, settings: dict[str, Any]) -> None:
        if FakeYoutubeDL.fail:
            raise RuntimeError("setup failed")
        self.settings = settings
        self.closed = False
        FakeYoutubeDL.instances.append(self)

    def close(self) -> None:
        self.closed = True


class FakeChannel(Channel):
    def __init__(self, name: str, behaviour: bool | BaseException = True) -> None:
        super().__init__(name, None, None, None, None)
        self.behaviour = behaviour
        self.calls: list[tuple[int, YoutubeDL]] = []

    def download(
        self,
        slot: int,
        processor: YoutubeDL,
        queue: Queue[Message],
        playlist_index: int = 1,
    ) -> bool:
        self.calls.append((slot, processor))
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        return self.behaviour


class TestSlot(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        FakeYoutubeDL.instances = []
        FakeYoutubeDL.fail = False
        patcher = mock.patch("vidl.slot.YoutubeDL", FakeYoutubeDL)
        patcher.start()
        self.addCleanup(patcher.stop)
        Config.settings["General"]["cookie_browser"] = None
        Config.settings["Paths"].update(output="/out", temporary="/tmp/vidl")
        Config.settings["Channels"]["archived"] = "archived"
        self.queue: Queue[Message] = Queue()
        self.slot = Slot(2, self.queue)
        self.addCleanup(self.stop)
        self.assertTrue(wait_until(self.slot.ready))

    def stop(self) -> None:
        self.slot.halt()
        self.slot.join()

    def process(self, channel: Channel) -> None:
        channel.set_active()
        self.slot.process(channel)
        self.assertFalse(self.slot.ready())
        self.assertTrue(wait_until(self.slot.ready))

    def test_announces_itself(self):
        message = self.queue.get(timeout=5)
        self.assertEqual(message, InitMessage(index=2, provider=Message.Provider.SLOT))

    def test_processes_channel_and_releases_resources(self):
        channel = FakeChannel("a")
        self.process(channel)
        ((slot, processor),) = channel.calls
        self.assertEqual(slot, 2)
        self.assertIs(processor, FakeYoutubeDL.instances[0])
        self.assertTrue(FakeYoutubeDL.instances[0].closed)
        self.assertFalse(channel.active())

    def test_processor_settings(self):
        Config.settings["General"]["cookie_browser"] = "firefox"
        channel = FakeChannel("a")
        self.process(channel)
        settings = FakeYoutubeDL.instances[0].settings
        self.assertEqual(settings["cookiesfrombrowser"], ("firefox", None, None, None))
        self.assertEqual(settings["paths"], {"home": "/out", "temp": "/tmp/vidl"})
        self.assertEqual(settings["download_archive"], "archived")
        self.assertIsInstance(settings["logger"], Logger)
        self.assertEqual(len(settings["progress_hooks"]), 1)
        self.assertEqual(len(settings["postprocessor_hooks"]), 1)
        self.assertEqual(settings["format"], Config.ydl_settings["format"])
        self.assertEqual(Config.ydl_settings["paths"], {})  # the defaults are copied

    def test_optional_settings_are_left_out(self):
        Config.settings["Channels"]["archived"] = None
        self.process(FakeChannel("a"))
        settings = FakeYoutubeDL.instances[0].settings
        self.assertNotIn("cookiesfrombrowser", settings)
        self.assertNotIn("download_archive", settings)

    def test_logger_errors_are_reported_to_the_channel(self):
        channel = FakeChannel("a")
        self.process(channel)
        logger = FakeYoutubeDL.instances[0].settings["logger"]
        with mock.patch("vidl.logger.Debug.print"):
            logger.error("ERROR: failed")
        self.assertEqual(channel.get_last_error(), "failed")

    def test_unexpected_exception_is_reported_and_slot_survives(self):
        drain(self.queue)
        channel = FakeChannel("a", RuntimeError("boom: details"))
        self.process(channel)
        (message,) = [m for m in drain(self.queue) if isinstance(m, ErrorMessage)]
        self.assertEqual(message.provider, Message.Provider.SLOT)
        self.assertEqual(message.target, "a")
        self.assertEqual(message.message, "RuntimeError: boom: details")
        self.assertEqual(channel.get_last_error(), "RuntimeError: boom: details")
        self.assertFalse(channel.active())
        self.assertTrue(FakeYoutubeDL.instances[0].closed)
        self.process(FakeChannel("b"))  # still working
        self.assertEqual(len(FakeYoutubeDL.instances), 2)

    def test_cancelled_download_is_not_an_error(self):
        drain(self.queue)
        self.process(FakeChannel("a", DownloadCancelled("halt")))
        self.assertEqual(drain(self.queue), [])

    def test_setup_failure_is_reported(self):
        drain(self.queue)
        FakeYoutubeDL.fail = True
        channel = FakeChannel("a")
        self.process(channel)
        self.assertEqual(channel.calls, [])
        self.assertEqual(channel.get_last_error(), "RuntimeError: setup failed")
        self.assertFalse(channel.active())

    def test_halt_stops_the_thread(self):
        self.stop()
        self.assertFalse(self.slot.ready())

    def test_halt_event_is_given_to_the_channel(self):
        channel = FakeChannel("a")
        self.process(channel)
        self.slot.halt()
        self.assertTrue(private(channel, Channel, "halt_event").is_set())


if __name__ == "__main__":
    unittest.main()
