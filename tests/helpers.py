# pyright: standard
"""Shared fixtures and fakes for the ViDL tests."""

import copy
import io
import os
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping
from queue import Empty, Queue
from typing import Any, TypeVar, cast
from unittest import mock

from yt_dlp import YoutubeDL

from vidl.ansi import ANSI
from vidl.config import Config
from vidl.debug import Debug
from vidl.terminal import Terminal

T = TypeVar("T")


def private(obj: object, cls: type, name: str) -> Any:
    """Reads a name mangled attribute, e.g. private(view, View, "timer")."""
    return getattr(obj, f"_{cls.__name__}__{name}")


def drain(queue: Queue[T]) -> list[T]:
    """Returns all items currently in the queue."""
    items: list[T] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except Empty:
            return items


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Polls condition until it is true or the timeout expires."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class IsolatedTestCase(unittest.TestCase):
    """Restores global state (Config and Debug) after each test, and offers
    temporary directories and a fixed time zone."""

    def setUp(self) -> None:
        super().setUp()
        settings = copy.deepcopy(Config.settings)
        ydl_settings = copy.deepcopy(Config.ydl_settings)
        ini, sleep_interval = Config.ini, Config.SLEEP_INTERVAL

        def restore() -> None:
            Config.settings.clear()
            Config.settings.update(settings)
            Config.ydl_settings.clear()
            Config.ydl_settings.update(ydl_settings)
            Config.ini, Config.SLEEP_INTERVAL = ini, sleep_interval
            Debug.queue = Debug.thread = Debug.inactive = Debug.wrapper = None
            Debug.buffer = ""

        self.addCleanup(restore)

    def temp_dir(self) -> str:
        directory = tempfile.TemporaryDirectory(prefix="vidl-test-")
        self.addCleanup(directory.cleanup)
        return directory.name

    def use_utc(self) -> None:
        previous = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()

        def restore() -> None:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

        self.addCleanup(restore)

    def quiet_console(self, cols: int = 130, rows: int = 40) -> io.StringIO:
        """Captures stdout, makes stdin non-interactive and fixes the terminal
        size, for code that draws to the terminal."""
        stdout = io.StringIO()
        for patcher in (
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stdin", io.StringIO()),
            mock.patch(
                "vidl.terminal.size", return_value=os.terminal_size((cols, rows))
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return stdout


class FakeTerminal:
    """Records what a View draws, instead of writing to the terminal."""

    def __init__(self, rows: int = 40, cols: int = 130) -> None:
        # Typed as the real terminal, for passing to the code under test
        self.terminal: Terminal = cast(Terminal, self)
        self.rows, self.cols = rows, cols
        self.writes: list[tuple[int, int, str]] = []

    def print(self, string: str, row: int, col: int) -> None:
        self.writes.append((row, col, string))

    def get_size(self) -> tuple[int, int]:
        return self.rows, self.cols

    def clear(self) -> None:
        self.writes.clear()

    def flush(self) -> None: ...

    def row(self, row: int, col: int | None = None) -> str:
        """The last plain text written to a row, optionally at a column."""
        lines = [
            ANSI.remove(s)
            for r, c, s in self.writes
            if r == row and (col is None or c == col)
        ]
        return lines[-1] if lines else ""

    def text(self) -> str:
        return "\n".join(ANSI.remove(s) for _, _, s in self.writes)

    def raw(self) -> str:
        return "".join(s for _, _, s in self.writes)


def video(
    video_id: str,
    timestamp: int = 0,
    width: int = 1920,
    height: int = 1080,
    **extra: object,
) -> dict[str, object]:
    """An unprocessed yt-dlp video info dict with a single mp4 format."""
    info: dict[str, object] = {
        "id": video_id,
        "title": f"Title {video_id}",
        "webpage_url": f"https://v/{video_id}",
        "formats": [{"ext": "mp4", "width": width, "height": height}],
    }
    if timestamp:
        info["timestamp"] = timestamp
    info.update(extra)
    return info


def playlist(playlist_id: str, urls: list[str], **extra: object) -> dict[str, object]:
    """An unprocessed yt-dlp playlist info dict with url entries."""
    info: dict[str, object] = {
        "_type": "playlist",
        "id": playlist_id,
        "title": f"Playlist {playlist_id}",
        "entries": [{"_type": "url", "url": url} for url in urls],
    }
    info.update(extra)
    return info


class FakeProcessor:
    """Stands in for YoutubeDL. infos maps an URL to an info dict, None
    (already archived), an exception to raise, or a callable returning one
    of these."""

    def __init__(
        self, infos: Mapping[str, object] | None = None, archive: bool = False
    ) -> None:
        # Typed as the real processor, for passing to the code under test
        self.processor: YoutubeDL = cast(YoutubeDL, self)
        self.infos: dict[str, object] = dict(infos or {})
        self.params: dict[str, object] = (
            {"download_archive": "archive"} if archive else {}
        )
        self.extracted: list[str] = []
        self.downloaded: list[str] = []
        self.archived: list[str] = []
        self.download_result: object = 0
        self.on_download: Callable[[str], None] | None = None
        self.closed = False

    def extract_info(
        self, url: str, download: bool = False, process: bool = False
    ) -> object:
        self.extracted.append(url)
        value = self.infos[url]
        if callable(value):
            value = cast(Callable[[str], object], value)(url)
        if isinstance(value, BaseException):
            raise value
        return value

    def prepare_filename(self, info: dict[str, object], dir_type: str = "") -> str:
        return f"/tmp/{info.get('id')}.NA"

    def download(self, urls: list[str]) -> object:
        self.downloaded.append(urls[0])
        if self.on_download is not None:
            self.on_download(urls[0])
        if isinstance(self.download_result, BaseException):
            raise self.download_result
        return self.download_result

    def in_download_archive(self, info: dict[str, object]) -> bool:
        return info.get("id") in self.archived

    def record_download_archive(self, info: dict[str, object]) -> None:
        self.archived.append(str(info.get("id")))

    def close(self) -> None:
        self.closed = True
