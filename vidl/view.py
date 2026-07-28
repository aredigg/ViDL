from queue import Queue
from threading import Event, Thread
from time import sleep

from vidl.message import Message, Msg


class View:
    index = 0

    def __init__(self) -> None:
        self.__ready = False
        self.__queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"View-{View.index}")
        self.__thread.start()
        View.index += 1

    def get_queue(self):
        return self.__queue

    def ready(self):
        return self.__ready and self.__thread.is_alive()

    def __run(self):
        self.__ready = True
        while not self.__halt_event.is_set():
            message = self.__queue.get()
            if message.kind != Msg.HALT:
                sleep(0.1)

    def halt(self):
        self.__ready = False
        self.__queue.put(Message(kind=Msg.HALT, body=None))
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
