import csv
from queue import Empty
from signal import SIG_IGN, SIGINT, SIGTERM, SIGWINCH, signal
from time import time
from types import FrameType
from typing import cast
from urllib.parse import urlsplit

from .backoff import Backoff
from .channel import Channel, ChannelHelper
from .command import AddChannelCommand, Command, DrainCommand, QuitCommand
from .config import Config
from .debug import Debug
from .message import (
    HeaderMessage,
    Message,
    NoticeMessage,
    RedrawMessage,
    SleepMessage,
    SystemStatusMessage,
)
from .monitor import Monitor, SystemStatus
from .shared import Shared
from .slot import Slot
from .util import Util
from .view_controller import ViewController


class Coordinator:
    SAVE_INTERVAL: int = 30
    # Seconds to wait for a command when there is nothing to start
    IDLE_WAIT: float = 1.0

    def __init__(self) -> None:
        self.__running = True
        self.__draining = False
        self.__channels: list[Channel] = []
        self.__slots: list[Slot] = []
        self.__error: object = None
        self.__shared = Shared()
        self.__monitor: Monitor | None = None
        # Idle slots already shown the current back-off
        self.__backoff_shown: set[int] = set()
        self.__header: tuple[str, str] = ("", "")
        self.__view = ViewController()
        self.__view_queue = self.__view.get_queue()
        self.__command_queue = self.__view.get_command_queue()
        # Threads are running from here on, they must be stopped if setup fails
        try:
            try:
                self.__channels = ChannelHelper.load_channels(
                    str(Config.settings["Channels"]["file_name"])
                )
            except (OSError, ValueError, csv.Error) as e:
                self.__error = e
                self.__running = False
            number_of_slots = min(self.__slot_limit(), len(self.__channels))
            monitor = self.__monitor = self.__create_monitor()
            self.__shared = Shared(
                backoff=Backoff(slots=number_of_slots),
                network_error=monitor.report_network_error,
                network_success=monitor.report_network_success,
            )
            self.__slots = [
                Slot(i, self.__view_queue, self.__shared)
                for i in range(number_of_slots)
            ]
            if self.__running:
                monitor.start()
        except BaseException:
            self.__stop()
            raise
        _ = signal(SIGTERM, self.halt)
        _ = signal(SIGWINCH, self.redraw)

    def __create_monitor(self) -> Monitor:
        general = Config.settings["General"]
        paths = Config.settings["Paths"]
        return Monitor(
            publish=self.__publish_system_status,
            paths=[
                ("dl", str(paths["output"] or "")),
                ("tmp", str(paths["temporary"] or "")),
            ],
            update_check=bool(general.get("update_check")),
            network_check=bool(general.get("network_check")),
        )

    # Called on the monitor thread
    def __publish_system_status(self, status: SystemStatus) -> None:
        self.__view_queue.put(SystemStatusMessage(status=status))

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
                    channel = None
                    if not self.__draining and not self.__backoff_active():
                        channel = self.__next_channel()
                    slot = next((slot for slot in self.__slots if slot.ready()), None)
                    if channel is not None and slot is not None:
                        channel.set_active()
                        slot.process(channel)
                        self.__handle_commands(timeout=None)
                    else:
                        # Waiting for a command is the pause when idle
                        self.__handle_commands(timeout=Coordinator.IDLE_WAIT)
                    save_counter -= 1
                    if save_counter == 0:
                        self.__save_channels()
                        save_counter = Coordinator.SAVE_INTERVAL
                    if self.__draining and not self.__busy_slots():
                        Debug.print(-1, "=== Downloads finished, quitting ===")
                        self.__running = False
                    self.__update_header()
                except KeyboardInterrupt:
                    self.__running = False
        finally:
            self.__stop()
            self.__save_channels()

        return 3 if self.__error is not None else 0

    def __stop(self):
        # Shutdown is final, so Ctrl-C stays ignored from here on. An interrupted
        # shutdown leaves threads running and the terminal in the alternate screen
        _ = signal(SIGINT, SIG_IGN)
        if self.__monitor is not None:
            self.__monitor.halt()
        for slot in self.__slots:
            slot.halt()
        for slot in self.__slots:
            slot.join()
        if self.__monitor is not None:
            self.__monitor.join()
        self.__view.halt()
        self.__view.join()

    def __slot_limit(self) -> int:
        return max(1, cast(int, Config.settings["Channels"]["slots"] or 0))

    def __busy_slots(self) -> int:
        return sum(1 for slot in self.__slots if slot.busy())

    # No channels are started during a global back-off, and idle slots show it
    def __backoff_active(self) -> bool:
        backoff = self.__shared.backoff
        if not backoff.active():
            self.__backoff_shown.clear()
            return False
        for index, slot in enumerate(self.__slots):
            if index not in self.__backoff_shown and slot.ready():
                self.__backoff_shown.add(index)
                self.__view_queue.put(
                    SleepMessage(
                        index=index,
                        provider=Message.Provider.CHANNEL,
                        sleep_time=backoff.remaining(),
                        required=True,
                    )
                )
        return True

    # The lasting states shown in the header: a pending quit and a back-off
    def __update_header(self) -> None:
        status: list[str] = []
        title: list[str] = []
        if self.__draining:
            busy = self.__busy_slots()
            status.append(
                f"Finishing {busy} download{'s' if busy != 1 else ''}, "
                + "c to continue, q to quit now"
            )
            title.append("⏹️")
        backoff = self.__shared.backoff
        if backoff.active():
            until = Util.get_time(int(time() + backoff.remaining()))
            status.append(f"Backing off until {until}, too many restricted slots")
            title.append(f"💤 {until}")
        header = (" · ".join(status), " ".join(title))
        if header != self.__header:
            self.__header = header
            self.__view_queue.put(HeaderMessage(status=header[0], title=header[1]))

    def __next_channel(self):
        return min(
            (channel for channel in self.__channels if not channel.active()),
            key=lambda channel: channel.get_last_date(),
            default=None,
        )

    # Handles queued commands, waiting up to timeout for the first one
    def __handle_commands(self, timeout: float | None) -> None:
        try:
            if timeout is None:
                command = self.__command_queue.get_nowait()
            else:
                command = self.__command_queue.get(timeout=timeout)
        except Empty:
            return
        while True:
            self.__handle_command(command)
            try:
                command = self.__command_queue.get_nowait()
            except Empty:
                return

    def __handle_command(self, command: Command) -> None:
        match command:
            case QuitCommand():
                self.halt(0, None)
            case DrainCommand():
                self.__toggle_drain()
            case AddChannelCommand(text=text):
                self.__add_channel(text)
            case _:
                Debug.print(-1, f"Unknown command {command!r}")

    def __toggle_drain(self) -> None:
        self.__draining = not self.__draining
        if self.__draining:
            Debug.print(-1, "=== Finishing downloads before quitting ===")
            self.__shared.drain_event.set()
        else:
            Debug.print(-1, "=== Finishing cancelled ===")
            self.__shared.drain_event.clear()
            self.__notice("Continuing")
        self.__update_header()

    def __add_channel(self, text: str) -> None:
        text = text.strip()
        key = Coordinator.normalize_url(text)
        if key is None:
            self.__notice(f"Not added, not an http(s) URL: {text}", error=True)
            return
        for channel in self.__channels:
            name, url = cast(tuple[str, str], tuple(channel.row()[:2]))
            if key in (Coordinator.normalize_url(url), Coordinator.normalize_url(name)):
                self.__notice(f"Already listed: {name or url}", error=True)
                return
        self.__channels.append(Channel(text, None, None, None, None))
        Debug.print(-1, f"Channel added: {text}")
        self.__save_channels()
        if self.__error is not None:
            return
        if len(self.__slots) < min(self.__slot_limit(), len(self.__channels)):
            # Fewer slots were started than configured, as there were fewer channels
            self.__slots.append(
                Slot(len(self.__slots), self.__view_queue, self.__shared)
            )
            self.__shared.backoff.set_slots(len(self.__slots))
        self.__notice(f"Added {text}")

    def __notice(self, text: str, error: bool = False) -> None:
        self.__view_queue.put(NoticeMessage(text=text, error=error))

    # A comparable form of an http(s) URL, or None for anything else: the
    # scheme and host are case insensitive, "www."/"m." and a trailing "/" are
    # ignored, the path and query are kept as they are (ids are case sensitive)
    @staticmethod
    def normalize_url(text: str) -> str | None:
        text = text.strip()
        if not text or any(c.isspace() for c in text):
            return None
        try:
            parts = urlsplit(text)
            host = parts.hostname
        except ValueError:
            return None
        if parts.scheme.lower() not in ("http", "https") or not host:
            return None
        host = host.lower()
        for prefix in ("www.", "m."):
            host = host.removeprefix(prefix)
        path = parts.path.rstrip("/")
        query = f"?{parts.query}" if parts.query else ""
        return f"{host}{path}{query}"

    def halt(self, _signum: int, _frame: FrameType | None) -> None:
        Debug.print(-1, "=== Shutdown requested ===")
        self.__running = False

    def redraw(self, _signum: int, _frame: FrameType | None) -> None:
        self.__view_queue.put(RedrawMessage())
