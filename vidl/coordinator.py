from queue import Queue
from time import sleep

from .channel import Channel
from .config import Config
from .slot import Slot


class Coordinator:
    def __init__(self) -> None:
        self.__running = True
        self.__view_queue = Queue()
        self.__channels = []
        self.__slots = []
        self.__error = None
        try:
            with open(Config.settings["Channels"]["file_name"]) as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("#"):
                        if line.count(";") > 0:
                            parameters = line.split(";")
                            if len(parameters) == Channel.header_len:
                                self.__channels.append(Channel(*parameters))
                        else:
                            self.__channels.append(
                                Channel(line, None, None, None, None)
                            )
        except FileNotFoundError as e:
            self.__error = e
            self.__running = False
        number_of_slots = min(
            max(1, Config.settings["Channels"]["slots"]), len(self.__channels)
        )
        self.__slots = [Slot(i, self.__view_queue) for i in range(number_of_slots)]

    def __save_channels(self):
        write_buffer = Channel.header
        for channel in self.__channels:
            write_buffer += channel.write()
        if write_buffer != Channel.header:
            try:
                with open(Config.settings["Channels"]["file_name"], "w") as f:
                    f.write(write_buffer)
            except FileNotFoundError as e:
                self.__error = e
                self.__running = False
        else:
            self.__error = "Incorrect channel specification at write"
            self.__running = False

    def error(self):
        return self.__error

    def run(self):
        if not self.__running:
            return 1
        if not self.__channels:
            self.__error = "No channels"
            return 2
        else:
            self.__save_channels()
        while self.__running:
            try:
                channel = self.__next_channel()
                available_slot = None
                while available_slot is None:
                    if not (
                        available_slot := next(
                            (
                                slot
                                for slot in self.__slots
                                if slot.ready() and slot.channel() != channel
                            ),
                            None,
                        )
                    ):
                        sleep(1)
                if channel is not None:
                    channel.set_active()
                    available_slot.process(channel)
            except KeyboardInterrupt:
                self.__running = False
        for slot in self.__slots:
            slot.halt()
        for slot in self.__slots:
            slot.join()
        return 3 if self.__error is not None else 0

    def __next_channel(self):
        return min(
            (channel for channel in self.__channels if not channel.active()),
            key=lambda channel: channel.get_last_date(),
            default=None,
        )
