from copy import deepcopy
from queue import Queue
from threading import Event, Lock, Thread
from time import sleep
from typing import TYPE_CHECKING, cast

from yt_dlp import YoutubeDL

from .config import Config
from .hook import DownloadCancelled, Hook
from .logger import Logger
from .message import ErrorMessage, InitMessage, Message, Msg

if TYPE_CHECKING:
    from yt_dlp import _Params


class Slot:
    processor_lock = Lock()

    def __init__(self, index, view_queue) -> None:
        self.__index = index
        self.__view_queue = view_queue
        self.__channel = None
        self.__ready = False
        self.__queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"Slot-{index}")
        self.__thread.start()

    def process(self, channel):
        self.__ready = False
        self.__channel = channel
        self.__queue.put(channel)

    def ready(self):
        return self.__ready and self.__thread.is_alive()

    def channel(self):
        return self.__channel

    def __run(self):
        self.__ready = True
        self.__view_queue.put(
            Message(
                kind=Msg.INIT,
                body=InitMessage(index=self.__index, provider="slot"),
            )
        )
        while not self.__halt_event.is_set():
            channel = self.__queue.get()
            if channel is not None:
                with Slot.processor_lock:
                    processor = self.__setup()
                channel.set_halt_event(self.__halt_event)
                try:
                    channel.download(self.__index, processor, self.__view_queue)
                    sleep(3)
                except DownloadCancelled:
                    ...
                except Exception as e:
                    self.__view_queue.put(
                        Message(
                            Msg.ERROR, ErrorMessage(self.__index, "slot", None, str(e))
                        )
                    )
                finally:
                    self.__channel = None
                    self.__ready = True

    def __setup(self):
        hook = Hook(self.__view_queue, self.__halt_event, self.__index)
        settings = deepcopy(Config.ydl_settings)
        if cookie_browser := Config.settings["General"]["cookie_browser"]:
            settings["cookiesfrombrowser"] = (cookie_browser, None, None, None)
        settings["max_sleep_interval"] = Config.settings["Download"]["sleep_interval"]
        settings["paths"]["home"] = Config.settings["Paths"]["output"]
        settings["paths"]["temp"] = Config.settings["Paths"]["temporary"]
        settings["download_archive"] = Config.settings["Channels"]["archived"]
        settings["logger"] = Logger(self.__view_queue, self.__index)
        settings["progress_hooks"] = [hook.common]
        settings["postprocessor_hooks"] = [hook.common]

        return YoutubeDL(cast("_Params", dict(settings)))

    def halt(self):
        self.__ready = False
        self.__queue.put(None)
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
