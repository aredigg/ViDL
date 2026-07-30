from time import sleep

from .channel import Channel
from .config import Config
from .slot import Slot
from .view import View


class Coordinator:
    def __init__(self) -> None:
        self.__running = True
        self.__view = View()
        self.__view_queue, self.__input_queue = self.__view.get_queues()
        self.__channels = []
        self.__slots = []
        self.__error = None
        try:
            self.__channels = Channel.load_channels(
                Config.settings["Channels"]["file_name"]
            )
        except FileNotFoundError as e:
            self.__error = e
            self.__running = False
        number_of_slots = min(
            max(1, Config.settings["Channels"]["slots"]), len(self.__channels)
        )
        self.__slots = [Slot(i, self.__view_queue) for i in range(number_of_slots)]

    def __save_channels(self):
        ret = Channel.save_channels(
            self.__channels, Config.settings["Channels"]["file_name"]
        )
        if ret is not None:
            self.__error = ret
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
                        self.__save_channels()
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
        self.__view.halt()
        self.__view.join()

        return 3 if self.__error is not None else 0

    def __next_channel(self):
        return min(
            (channel for channel in self.__channels if not channel.active()),
            key=lambda channel: channel.get_last_date(),
            default=None,
        )
