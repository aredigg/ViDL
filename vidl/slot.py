import traceback
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from queue import Queue
from threading import Event, Lock, Thread
from typing import cast

from yt_dlp import YoutubeDL

from .channel import Channel
from .config import Config
from .cookies import CookieWatch
from .debug import Debug
from .error import Error
from .hook import DownloadCancelled, Hook
from .logger import Logger
from .message import ErrorMessage, InfoMessage, InitMessage, Message
from .metadata import (
    MetadataPostProcessor,
    new_file_atom_pp,
    new_file_embed_subtitle_pp,
    new_file_embed_thumbnail_pp,
    new_file_metadata_pp,
)
from .preflight import GIB, Preflight
from .shared import Shared


@dataclass(frozen=True)
class Setup:
    """A downloader for one channel, and what the channel needs to work with it."""

    processor: YoutubeDL
    metadata: MetadataPostProcessor | None
    preflight: Preflight
    cookies: CookieWatch | None


class Slot:
    processor_lock: Lock = Lock()

    def __init__(
        self, index: int, view_queue: Queue[Message], shared: Shared | None = None
    ) -> None:
        self.__index = index
        self.__view_queue: Queue[Message] = view_queue
        self.__shared: Shared = shared if shared is not None else Shared()
        self.__ready = False
        self.__queue: Queue[Channel | None] = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"Slot-{index}")
        self.__thread.start()

    def process(self, channel: Channel):
        self.__ready = False
        self.__queue.put(channel)

    def ready(self):
        return self.__ready and self.__thread.is_alive()

    # Processing a channel. A slot whose thread has ended is never busy
    def busy(self):
        return not self.__ready and self.__thread.is_alive()

    def __run(self):
        self.__ready = True
        self.__view_queue.put(
            InitMessage(index=self.__index, provider=Message.Provider.SLOT)
        )
        while not self.__halt_event.is_set():
            channel = self.__queue.get()
            if channel is not None:
                processor: YoutubeDL | None = None
                cookies: CookieWatch | None = None
                try:
                    with Slot.processor_lock:
                        setup = self.__setup(channel)
                    processor, cookies = setup.processor, setup.cookies
                    channel.set_halt_event(self.__halt_event)
                    channel.set_shared(self.__shared)
                    channel.set_metadata_processor(setup.metadata)
                    channel.set_preflight(setup.preflight)
                    channel.set_cookies(setup.cookies)
                    # We could do something with the result
                    _ = channel.download(self.__index, processor, self.__view_queue)
                except DownloadCancelled:
                    ...
                except Exception as e:  # noqa: BLE001 - keep the slot alive
                    self.__report_exception(channel, e)
                finally:
                    if processor is not None:
                        if cookies is not None:
                            cookies.log_state(processor, "at the end")
                        self.__close(processor)
                    channel.set_inactive()
                    self.__ready = True

    def __report_exception(self, channel: Channel, e: Exception):
        error = Error.from_exception(e)
        # The panel shows the short summary, the channels file keeps the
        # exception text, the debug log the traceback
        channel.report_failure(error.message)
        self.__view_queue.put(
            ErrorMessage(
                index=self.__index,
                provider=Message.Provider.SLOT,
                target=channel.get_name() or "",
                message=error.summary,
            )
        )
        Debug.print(self.__index, "Unhandled exception:\n" + traceback.format_exc())

    def __close(self, processor: YoutubeDL):
        # Releases request handlers/sessions held by the processor
        try:
            processor.close()
        except Exception:  # noqa: BLE001
            Debug.print(self.__index, "Close failed:\n" + traceback.format_exc())

    def __setup(self, channel: Channel) -> Setup:
        hook = Hook(self.__view_queue, self.__halt_event, self.__index)
        settings: dict[str, object] = deepcopy(Config.ydl_settings)
        cookies: CookieWatch | None = None
        if cookie_browser := Config.settings["General"]["cookie_browser"]:
            settings["cookiesfrombrowser"] = (cookie_browser, None, None, None)
            cookies = CookieWatch(self.__index, str(cookie_browser))
        settings["paths"] = {
            "home": cast(str, Config.settings["Paths"]["output"]),
            "temp": cast(str, Config.settings["Paths"]["temporary"]),
        }
        if archived := Config.settings["Channels"]["archived"]:
            settings["download_archive"] = archived
        settings["logger"] = Logger(
            self.__index,
            channel.report_error,
            cookies.note_rotation if cookies is not None else None,
        )
        settings["progress_hooks"] = [hook.common]
        settings["postprocessor_hooks"] = [hook.common]
        download = Config.settings["Download"]
        metadata = bool(download.get("metadata"))
        thumbnail = download.get("thumbnail")
        if thumbnail in ("channel", "video"):
            settings["writethumbnail"] = True
        processor = cast(Callable[[dict[str, object]], YoutubeDL], YoutubeDL)(settings)
        metadata_pp: MetadataPostProcessor | None = None
        if metadata or thumbnail == "channel":
            # Fills in the episode, series and channel cover before the
            # thumbnail is written, see vidl/metadata.py
            metadata_pp = MetadataPostProcessor()
            processor.add_post_processor(metadata_pp, when="video")
        # Just before the download starts: poor sources, large files, disk space
        preflight = self.__preflight()
        processor.add_post_processor(preflight, when="before_dl")
        # Only for new downloads, existing files are never touched. The order
        # matters: embedding subtitles remuxes the file, which drops tags that
        # were written before, and the thumbnail is embedded last
        if settings.get("writesubtitles"):
            processor.add_post_processor(
                new_file_embed_subtitle_pp(processor), when="post_process"
            )
        if metadata:
            processor.add_post_processor(
                new_file_metadata_pp(processor), when="post_process"
            )
            processor.add_post_processor(
                new_file_atom_pp(processor), when="post_process"
            )
        if thumbnail in ("channel", "video"):
            processor.add_post_processor(
                new_file_embed_thumbnail_pp(processor), when="post_process"
            )
        if cookies is not None:
            # Reads the cookies of the browser now, under the processor lock,
            # and shows in the debug log which ones were loaded
            cookies.log_state(processor, "loaded")
        return Setup(processor, metadata_pp, preflight, cookies)

    def __preflight(self) -> Preflight:
        download = Config.settings["Download"]
        paths = Config.settings["Paths"]
        return Preflight(
            self.__index,
            self.__shared.gate,
            self.__shared.ledger,
            self.__halt_event,
            self.__shared.drain_event,
            maximum_size=Slot.__gigabytes(download.get("maximum_size")),
            exclusive_size=Slot.__gigabytes(download.get("exclusive_size")),
            temp_dir=cast(str | None, paths.get("temporary")),
            output_dir=cast(str | None, paths.get("output")),
            notify=self.__notify,
        )

    # Shown in the panel while the download waits, e.g. for a large download
    def __notify(self, text: str) -> None:
        self.__view_queue.put(
            InfoMessage(
                index=self.__index,
                provider=Message.Provider.CHANNEL,
                target="Waiting",
                message=text,
            )
        )

    @staticmethod
    def __gigabytes(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return int(value * GIB) if value > 0 else 0

    def halt(self):
        self.__ready = False
        self.__halt_event.set()
        self.__queue.put(None)

    def join(self):
        self.__thread.join()
