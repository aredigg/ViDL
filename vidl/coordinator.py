from queue import Empty
from signal import SIGTERM, SIGWINCH, signal
from time import sleep
from types import FrameType
from typing import cast

from .channel import Channel, ChannelHelper
from .config import Config
from .debug import Debug
from .message import RedrawMessage
from .slot import Slot
from .view_controller import ViewController


class Coordinator:
    SAVE_INTERVAL: int = 30

    def __init__(self) -> None:
        self.__running = True
        self.__view = ViewController()
        self.__view_queue = self.__view.get_queue()
        self.__input_queue = self.__view.get_input_queue()
        self.__channels = []
        self.__slots = []
        self.__error = None
        try:
            self.__channels: list[Channel] = ChannelHelper.load_channels(
                str(Config.settings["Channels"]["file_name"])
            )
        except FileNotFoundError as e:
            self.__error = e
            self.__running = False
        number_of_slots = min(
            max(1, cast(int, Config.settings["Channels"]["slots"] or 0)),
            len(self.__channels),
        )
        self.__slots = [Slot(i, self.__view_queue) for i in range(number_of_slots)]
        _ = signal(SIGTERM, self.halt)
        _ = signal(SIGWINCH, self.redraw)

    def __save_channels(self):
        ret = ChannelHelper.save_channels(
            self.__channels, str(Config.settings["Channels"]["file_name"])
        )
        if ret is not None:
            self.__error = ret
            self.__running = False

    def error(self):
        return self.__error

    def run(self):
        try:
            if not self.__running:
                return 1
            if not self.__channels:
                self.__error = "No channels"
                return 2
            else:
                self.__save_channels()
            save_counter = Coordinator.SAVE_INTERVAL
            while self.__running:
                try:
                    channel = self.__next_channel()
                    slot = next((slot for slot in self.__slots if slot.ready()), None)
                    if channel is not None and slot is not None:
                        channel.set_active()
                        slot.process(channel)
                    else:
                        sleep(1)
                    save_counter -= 1
                    if save_counter == 0:
                        self.__save_channels()
                        save_counter = Coordinator.SAVE_INTERVAL
                    self.__poll_input()
                except KeyboardInterrupt:
                    self.__running = False
        finally:
            for slot in self.__slots:
                slot.halt()
            for slot in self.__slots:
                slot.join()
            self.__view.halt()
            self.__view.join()
            self.__save_channels()

        return 3 if self.__error is not None else 0

    def __next_channel(self):
        return min(
            (channel for channel in self.__channels if not channel.active()),
            key=lambda channel: channel.get_last_date(),
            default=None,
        )

    def __poll_input(self):
        try:
            key = self.__input_queue.get_nowait()
        except Empty:
            return
        if key in ("q", "Q", "\x03", "\x04"):
            self.halt(0, None)

    def halt(self, _signum: int, _frame: FrameType | None) -> None:
        Debug.print(-1, "=== Shutdown requested ===")
        self.__running = False

    def redraw(self, _signum: int, _frame: FrameType | None) -> None:
        self.__view_queue.put(RedrawMessage())
