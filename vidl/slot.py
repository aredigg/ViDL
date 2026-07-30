from queue import Queue
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, cast

from yt_dlp import YoutubeDL

from .config import Config
from .hook import Hook
from .logger import Logger
from .message import InitMessage, Message, Msg

if TYPE_CHECKING:
    from yt_dlp import _Params


class Slot:
    index = 0
    processor_lock = Lock()

    def __init__(self, index, view_queue) -> None:
        self.__index = Slot.index
        self.__view_queue = view_queue
        self.__channel = None
        self.__ready = False
        self.__queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"Slot-{index}")
        self.__thread.start()
        Slot.index += 1

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
                channel.download(self.__index, processor, self.__view_queue)
                self.__channel = None
                self.__ready = True

    def __setup(self):
        hook = Hook(self.__view_queue, self.__halt_event, self.__index)
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
