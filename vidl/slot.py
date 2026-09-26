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
from .hook import DownloadCancelled, Hook
from .logger import Logger
from .message import ErrorMessage, InitMessage, Message


class Slot:
    processor_lock: Lock = Lock()

    def __init__(self, index: int, view_queue: Queue[Message]) -> None:
        self.__index = index
        self.__view_queue: Queue[Message] = view_queue
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
                        processor = self.__setup(channel)
                    channel.set_halt_event(self.__halt_event)
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
        message = f"{type(e).__name__}: {e}"
        channel.report_error(message)
        self.__view_queue.put(
            ErrorMessage(
                index=self.__index,
                provider=Message.Provider.SLOT,
                target=channel.get_name() or "",
                message=message,
            )
        )
        Debug.print(self.__index, "Unhandled exception:\n" + traceback.format_exc())

    def __close(self, processor: YoutubeDL):
        # Releases request handlers/sessions held by the processor
        try:
            processor.close()
        except Exception:  # noqa: BLE001
            Debug.print(self.__index, "Close failed:\n" + traceback.format_exc())

    def __setup(self, channel: Channel):
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
        return cast(Callable[[dict[str, object]], YoutubeDL], YoutubeDL)(settings)

    def halt(self):
        self.__ready = False
        self.__halt_event.set()
        self.__queue.put(None)

    def join(self):
        self.__thread.join()
