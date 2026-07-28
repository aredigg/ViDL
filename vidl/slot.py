from queue import Queue
from threading import Event, Thread
from typing import TYPE_CHECKING, cast

from yt_dlp import YoutubeDL

from .config import Config
from .hook import Hook
from .logger import Logger

if TYPE_CHECKING:
    from yt_dlp import _Params


class Slot:
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
        while not self.__halt_event.is_set():
            channel = self.__queue.get()
            if channel is not None:
                with self.__setup() as processor:
                    channel.download(self.__index, processor)
                    self.__channel = None
                    self.__ready = True

    def __setup(self):
        hook = Hook(self.__view_queue, self.__index)
        settings = Config.ydl_settings
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
