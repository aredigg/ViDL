from queue import Queue
from time import sleep

from .slot import Slot
from .channel import Channel
from .config import Config

class Coordinator:

    def __init__(self) -> None:
        self.__running = True
        self.__view_queue = Queue()
        self.__channels = []
        self.__slots = []
        try:
            with open(Config.settings["Channels"]["file_name"]) as f:
                self.__channels = [
                    Channel(*p)
                    for line in f
                    if (s := line.strip()) and (p := s.split(";"))
                    and not s.startswith("#") and len(p) >= Channel.header_len
                ]
        except FileNotFoundError as e:
            print(f"File not found error {e}")
            self.__running = False
        number_of_slots = min(max(1, Config.settings["Channels"]["slots"]), len(self.__channels))
        self.__slots = [
            Slot(i, self.__view_queue) for i in range(number_of_slots)
        ]


    def run(self):
        while self.__running:
            try:
                channel = self.__next_channel()
                if not any(s.channel() == channel for s in self.__slots):
                    slot = None
                    while slot is None:
                        slot = next((s for s in self.__slots if s.ready()), None)
                        sleep(1)
            except KeyboardInterrupt:
                self.__running = False
        return 0

    def __next_channel(self):
        if not self.__channels:
            return None

        return min(
            self.__channels,
            key=lambda channel: channel.get_last_date()
        )
