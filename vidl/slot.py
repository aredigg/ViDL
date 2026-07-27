from queue import Queue
from threading import Event, Thread


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
                channel.download(self.__index)
                self.__channel = None
                self.__ready = True

    def halt(self):
        self.__ready = False
        self.__queue.put(None)
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
