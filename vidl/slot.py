import traceback
from collections.abc import Callable
from copy import deepcopy
from queue import Queue
from threading import Event, Lock, Thread
from typing import cast

from yt_dlp import YoutubeDL

from .channel import Channel
from .config import Config
from .debug import Debug
from .error import Error
from .hook import DownloadCancelled, Hook
from .logger import Logger
from .message import ErrorMessage, InitMessage, Message
from .metadata import (
    MetadataPostProcessor,
    new_file_embed_thumbnail_pp,
    new_file_metadata_pp,
)
from .shared import Shared


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
                try:
                    with Slot.processor_lock:
                        processor, metadata_pp = self.__setup(channel)
                    channel.set_halt_event(self.__halt_event)
                    channel.set_shared(self.__shared)
                    channel.set_metadata_processor(metadata_pp)
                    # We could do something with the result
                    _ = channel.download(self.__index, processor, self.__view_queue)
                except DownloadCancelled:
                    ...
                except Exception as e:  # noqa: BLE001 - keep the slot alive
                    self.__report_exception(channel, e)
                finally:
                    if processor is not None:
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

    def __setup(
        self, channel: Channel
    ) -> tuple[YoutubeDL, MetadataPostProcessor | None]:
        hook = Hook(self.__view_queue, self.__halt_event, self.__index)
        settings: dict[str, object] = deepcopy(Config.ydl_settings)
        if cookie_browser := Config.settings["General"]["cookie_browser"]:
            settings["cookiesfrombrowser"] = (cookie_browser, None, None, None)
        settings["paths"] = {
            "home": cast(str, Config.settings["Paths"]["output"]),
            "temp": cast(str, Config.settings["Paths"]["temporary"]),
        }
        if archived := Config.settings["Channels"]["archived"]:
            settings["download_archive"] = archived
        settings["logger"] = Logger(self.__index, channel.report_error)
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
        # Only for new downloads, existing files are never touched
        if metadata:
            processor.add_post_processor(
                new_file_metadata_pp(processor), when="post_process"
            )
        if thumbnail in ("channel", "video"):
            processor.add_post_processor(
                new_file_embed_thumbnail_pp(processor), when="post_process"
            )
        return processor, metadata_pp

    def halt(self):
        self.__ready = False
        self.__halt_event.set()
        self.__queue.put(None)

    def join(self):
        self.__thread.join()
