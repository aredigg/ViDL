# pyright: standard
import unittest
from queue import Queue
from threading import Event
from unittest import mock

from tests.helpers import drain
from vidl.hook import DownloadCancelled, Hook
from vidl.message import MediaMessage, Message, ProgressMessage


class TestHook(unittest.TestCase):
    def setUp(self):
        self.queue: Queue[Message] = Queue()
        self.halt = Event()
        self.hook = Hook(self.queue, self.halt, 2)
        patcher = mock.patch("vidl.hook.Debug.print")
        self.debug_print = patcher.start()
        self.addCleanup(patcher.stop)

    def test_process_names(self):
        self.assertEqual(Hook.State.process("Merger"), "Merge")
        self.assertEqual(Hook.State.process("MoveFiles"), "Move")
        self.assertEqual(Hook.State.process("FixupM3u8"), "Normalize")
        self.assertEqual(Hook.State.process("Progress"), "Progress")
        self.assertEqual(Hook.State.process("Unknown"), "Unknown")

    def test_progress_and_media_are_sent(self):
        data: dict[str, object] = {
            "status": "downloading",
            "downloaded_bytes": 10,
            "total_bytes": 100,
            "info_dict": {"width": 1280, "height": 720, "ext": "mp4"},
        }
        self.hook.common(data)
        progress, media = drain(self.queue)
        assert isinstance(progress, ProgressMessage)
        assert isinstance(media, MediaMessage)
        self.assertEqual(
            (progress.index, progress.provider), (2, Message.Provider.HOOK)
        )
        self.assertEqual(progress.progress.percent, 10.0)
        self.assertTrue(media.media.video_stat.startswith("1280x720"))
        self.debug_print.assert_not_called()

    def test_state_changes_are_logged(self):
        self.hook.common({"status": "started", "postprocessor": "Merger"})
        self.debug_print.assert_called_once_with(2, "HOOK --> Merger: started")

    def test_halt_cancels_after_reporting(self):
        self.halt.set()
        with self.assertRaises(DownloadCancelled):
            self.hook.common({"status": "downloading"})
        self.assertEqual(len(drain(self.queue)), 2)


if __name__ == "__main__":
    unittest.main()
