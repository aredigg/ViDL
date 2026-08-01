import os
from enum import Enum
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep, time

from .ansi import ANSI
from .message import (
    ErrorMessage,
    FilePathMessage,
    InfoMessage,
    InitMessage,
    ItemMessage,
    Message,
    Msg,
    PlaylistCountMessage,
    SleepMessage,
    URLMessage,
    WarnMessage,
)
from .terminal import Terminal
from .util import Util


class View:
    index = 0
    ROW_SIZE = 12
    COL_SIZE = 120
    HEAD_SIZE = 5
    FULL_INTERVAL_COUNT = 4

    class Status(Enum):
        INACTIVE = 0
        WAITING = 1
        SLEEPING = 2
        DOWNLOADING = 3
        PROCESSING = 4
        WARNING = 5
        ERROR = 6

    class Slot:
        def __init__(self, index) -> None:
            self.__index = index
            self.__pos = None
            self.__size = None
            self.__status = View.Status.INACTIVE
            self.__timer = None
            self.__top_name = ""
            self.__top_name_count = ""
            self.__top_name_last_dl = ""
            self.__item_id = ""
            self.__item_count = 0
            self.__item_index = ""
            self.__item_date = ""
            self.__item_title = ""
            self.__media_length = ""
            self.__media_video_stats = ""
            self.__media_audio_stats = ""
            self.__media_subtitles = ""
            self.__media_extension = ""
            self.__media_live_state = ""
            self.__media_age_limit = ""
            self.__progress_time = None
            self.__progress_time_est = None
            self.__progress_file_size = None
            self.__progress_file_size_est = None
            self.__progress_frag_index = None
            self.__progress_frag_count = None
            self.__progress_bitrate = None
            self.__progress_eta = None
            self.__progress_percent = None
            self.__progress_status = None
            self.__status_provider = None
            self.__status_message = None
            self.__temp_filepath = None
            self.__last_id = None

        def reset(self):
            self.__item_id = ""
            self.__item_index = ""
            self.__item_date = ""
            self.__item_title = ""
            self.__media_length = ""
            self.__media_video_stats = ""
            self.__media_audio_stats = ""
            self.__media_subtitles = ""
            self.__media_extension = ""
            self.__media_live_state = ""
            self.__media_age_limit = ""
            self.__progress_time = None
            self.__progress_time_est = None
            self.__progress_file_size = None
            self.__progress_file_size_est = None
            self.__progress_frag_index = None
            self.__progress_frag_count = None
            self.__progress_bitrate = None
            self.__progress_eta = None
            self.__progress_percent = None
            self.__progress_status = None
            self.__status_provider = None
            self.__status_message = None
            self.__temp_filepath = None
            self.__last_id = None

        def get_id(self):
            return self.__item_id

        def set_position(self, row, column):
            self.__pos = (row, column)

        def get_position(self):
            return self.__pos

        def is_position(self, row, column):
            return (row, column) == self.__pos

        def set_size(self, slot_size):
            self.__size = slot_size

        def set_status(self, status):
            self.__status = status

        def get_status(self):
            return self.__status

        def set_timer(self, time_offset):
            self.__timer = int(time()) + time_offset

        def timer(self):
            if self.__timer is not None:
                return Util.format_seconds((time()) - self.__timer)
            return "--:--"

        def set_count(self, count):
            self.__top_name_count = f"({count})"
            self.__item_count = count

        def set_filepath(self, filepath):
            self.__temp_filepath = filepath

        def get_top(self):
            return self.__top_name

        def set_top(self, name, last_dl):
            if name is not None:
                self.__top_name = name
                self.__top_name_count = ""
                self.__top_name_last_dl = last_dl

        def set_item(self, id, index, item_date, title):
            self.__item_id = id
            if index:
                self.__item_index = str(self.__item_count - index + 1)
            self.__item_date = Util.get_date(item_date)
            self.__item_title = title

        def set_media(self, length, video, audio, subtitle, extension, live, age):
            self.__media_length = Util.format_seconds(length, two_parts=False)
            self.__media_video_stats = video
            self.__media_audio_stats = audio
            self.__media_subtitles = subtitle
            self.__media_extension = extension
            self.__media_live_state = live
            self.__media_age_limit = age

        def set_progress(
            self,
            time_curr,
            time_est,
            size_curr,
            size_est,
            frag_i,
            frag_c,
            bitrate,
            eta,
            percent,
            status,
        ):
            self.__progress_time = time_curr
            self.__progress_time_est = time_est
            self.__progress_file_size = size_curr
            self.__progress_file_size_est = size_est
            self.__progress_frag_index = frag_i
            self.__progress_frag_count = frag_c
            self.__progress_bitrate = bitrate
            self.__progress_eta = eta
            self.__progress_percent = percent
            self.__progress_status = status

        def set_status_message(self, provider: str, message):
            self.__status_provider = (
                provider.replace(":", " ").capitalize()
                if provider is not None
                else provider
            )
            self.__status_message = message

        def update(self, terminal, full):
            if self.__pos is not None and self.__size is not None:
                row, col = self.__pos
                hgt, wdt = self.__size
                row, col = ((row * hgt) + View.HEAD_SIZE, (col * wdt) + 1)
                if full or self.__item_id != self.__last_id:
                    self.__item_line(terminal, row, col, hgt, wdt)
                    self.__media_line(terminal, row, col, hgt, wdt)
                    self.__border(terminal, row, col, hgt, wdt)
                    if self.__temp_filepath is not None:
                        self.__update_filesize()
                    self.__last_id = self.__item_id
                self.__status_line(terminal, row, col, hgt, wdt)

        def __border(self, terminal, r, c, h, w):
            title = ""
            if len(self.__top_name) < w - 12:
                title = " │ " + f"{self.__top_name} {self.__top_name_count}"
            header = (
                ""
                + ANSI.Inverse
                + " "
                + f"{str(self.__index + 1):>2.2}"
                + title
                + " "
                + ANSI.InverseReset
                + ""
            )
            terminal.print(
                "╭" + ("─" * 2) + header + ("─" * (w - 4 - ANSI.len(header))) + "╮",
                r,
                c,
            )
            for rb in range(r + 1, r + h - 1):
                terminal.print("│", rb, c)
                terminal.print("│", rb, c + w - 1)
            terminal.print("╰" + ("─" * (w - 2)) + "╯", r + h - 1, c)

        def __status_line(self, terminal, r, c, h, w):
            line = f"{self.__status_icon()}" + "  " + f"{self.timer()}"
            if self.__status == View.Status.DOWNLOADING:
                if self.__progress_time_est is not None:
                    remaining_time = Util.format_seconds(self.__progress_time_est)
                    eta = f"ETA {Util.get_time(int(time()) + self.__progress_time_est)}"
                    length = (
                        w
                        - ANSI.len(line)
                        - ANSI.len(remaining_time)
                        - ANSI.len(eta)
                        - 19
                    )
                    meter = self.__progress_meter(length, self.__progress_percent, "━")
                    line = line + " / " + remaining_time + "  " + meter + "  " + eta
                    terminal.print(line, r + 2, c + 6)
                elif self.__progress_file_size:
                    size = int(self.__progress_file_size) >> 20
                    line = line + " " + f"{size:>6} MB"
                    line_len = ANSI.len(line)
                    if line_len < w - 10:
                        line = line + " " * (w - 10 - line_len)
                        terminal.print(line, r + 2, c + 6)
            elif (
                self.__status_provider is not None and self.__status_message is not None
            ):
                if self.__status == View.Status.ERROR:
                    line = (
                        line
                        + "  "
                        + f"{self.__status_provider}: "
                        + f"{ANSI.Color.Cerise}{self.__status_message}{ANSI.Color.DefaultFg}"
                    )
                elif self.__status == View.Status.WARNING:
                    line = (
                        line
                        + "  "
                        + f"{self.__status_provider}: "
                        + f"{ANSI.Color.BurntSienna}{self.__status_message}{ANSI.Color.DefaultFg}"
                    )
                else:
                    line = (
                        line
                        + "  "
                        + f"{ANSI.Dim}{self.__status_provider}: {ANSI.DimReset}"
                        + f"{ANSI.Color.NeonChartreuse}{self.__status_message}{ANSI.Color.DefaultFg}"
                    )
                line_len = ANSI.len(line)
                if line_len < w - 10:
                    line = line + " " * (w - 10 - line_len)
                    terminal.print(line, r + 2, c + 6)
            else:
                line_len = ANSI.len(line)
                if line_len < w - 10:
                    line = line + " " * (w - 10 - line_len)
                    terminal.print(line, r + 2, c + 6)

        def __item_line(self, terminal, r, c, h, w):
            if self.__item_date or self.__item_title or self.__item_id:
                line = (
                    f"{self.__item_date:>10.10}"
                    + " │ "
                    + f"{self.__item_index:>4.4}"
                    + " │ "
                    + f"{self.__item_title}"
                )
                line_len = ANSI.len(line)
                if line_len < w - 10:
                    line = line + " " * (w - 8 - line_len)
                    terminal.print(line, r + 4, c + 4)
                if self.__item_title != self.__item_id:
                    line = f"[{self.__item_id}]"
                    line_len = ANSI.len(line)
                    if line_len < w - 10:
                        terminal.print(line, r + 4, c + (w - 4 - line_len))
                terminal.print(
                    ("─" * 11) + "┴" + ("─" * 6) + "┼" + ("─" * (w - 27)),
                    r + 5,
                    c + 4,
                )
            else:
                terminal.print(" " * (w - 2), r + 4, c + 1)
                terminal.print(" " * (w - 2), r + 5, c + 1)

        def __media_line(self, terminal, r, c, h, w):
            start_line = "  "
            if (
                self.__media_length
                or self.__progress_file_size_est is not None
                or self.__media_extension
                or self.__media_video_stats
                or self.__media_audio_stats
                or self.__media_audio_stats
                or self.__media_subtitles
            ):
                start_line = "│ "
            line = start_line + f"{self.__media_length}"
            if self.__progress_file_size_est is not None:
                size = int(self.__progress_file_size_est) >> 20
                line = line + " " + f"{size:>6} MB"
            else:
                line = line + " " * 10
            line = (
                line
                + " "
                + f"{self.__media_extension.upper():<4.4}"
                + " "
                + f"{self.__media_live_state:>6.6}"
            )

            if self.__media_age_limit:
                line = line + " " + f"({self.__media_age_limit})"
            else:
                line = line + " " * 5
            if ANSI.len(line) < w - 30:
                terminal.print(line, r + 6, c + 22)
            line = start_line + f"{self.__media_video_stats:<28.28}"
            if ANSI.len(line) < w - 30:
                terminal.print(line, r + 7, c + 22)
            line = start_line + f"{self.__media_audio_stats:<28.28}"
            if ANSI.len(line) < w - 30:
                terminal.print(line, r + 8, c + 22)
            line = start_line + f"{self.__media_subtitles:<28.28}"
            if ANSI.len(line) < w - 30:
                terminal.print(line, r + 9, c + 22)

        def __status_icon(self):
            match self.__status:
                case View.Status.INACTIVE:
                    return (
                        ANSI.Dim
                        + ANSI.Color.Cerise
                        + "○"
                        + ANSI.Color.DefaultFg
                        + ANSI.DimReset
                    )
                case View.Status.WAITING:
                    return (
                        ANSI.Blink
                        + ANSI.Color.Cerise
                        + "●"
                        + ANSI.Color.DefaultFg
                        + ANSI.BlinkReset
                    )
                case View.Status.SLEEPING:
                    return ANSI.Color.Cerise + "○" + ANSI.Color.DefaultFg
                case View.Status.DOWNLOADING:
                    return ANSI.Color.Cerise + "●" + ANSI.Color.DefaultFg
                case View.Status.PROCESSING:
                    return ANSI.Color.PineGreen + "●" + ANSI.Color.DefaultFg
                case View.Status.WARNING:
                    return ANSI.Color.BurntSienna + "○" + ANSI.Color.DefaultFg
                case View.Status.ERROR:
                    return ANSI.Color.Cerise + "○" + ANSI.Color.DefaultFg

        def __progress_meter(self, length, percent, kind="-"):
            progress = int((length * percent) / 100)
            remaining = length - progress
            meter = kind * progress + ANSI.Dim + kind * remaining + ANSI.DimReset
            return meter

        def __progress_meter_long(self, length, percent):
            pad = ""
            if length & 1:
                pad = " "
            length = length >> 1
            progress = int((length * percent) / 100)
            remaining = length - progress
            meter = pad + "╺╸" * progress + ANSI.Dim + "╺╸" * remaining + ANSI.DimReset
            return meter

        def __update_filesize(self):
            if self.__temp_filepath is not None:
                for filename in [self.__temp_filepath, self.__temp_filepath + ".part"]:
                    try:
                        self.__progress_file_size = os.path.getsize(filename)
                    except FileNotFoundError, OSError:
                        ...

    def __init__(self) -> None:
        self.__ready = False
        self.__queue = Queue()
        self.__input_queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"View-{View.index}")
        self.__thread.start()
        View.index += 1
        self.__slots = {}
        self.__columns = 0
        self.__rows = 0
        self.__slot_size = None

    def get_queues(self):
        return self.__queue, self.__input_queue

    def ready(self):
        return self.__ready and self.__thread.is_alive()

    def __run(self):
        with Terminal(self.__input_queue) as terminal:
            self.__term_size(terminal=terminal)
            self.__term_header(terminal=terminal)
            self.__ready = True
            full_count = View.FULL_INTERVAL_COUNT
            while not self.__halt_event.is_set():
                try:
                    message = self.__queue.get(block=False)
                    if message.kind != Msg.HALT:
                        try:
                            if (
                                self.__slots[message.body.index].get_status()
                                == View.Status.INACTIVE
                            ):
                                self.__slots[message.body.index].set_status_message(
                                    None, None
                                )
                        except KeyError:
                            ...
                        match message.kind:
                            case Msg.INIT:
                                if isinstance(message.body, InitMessage):
                                    self.__create_slot(message.body)
                                elif isinstance(message.body, ItemMessage):
                                    self.__update_item(message.body)
                                elif isinstance(message.body, PlaylistCountMessage):
                                    self.__update_count(message.body)
                            case Msg.UPDATE:
                                if isinstance(message.body, ItemMessage):
                                    self.__update_item(message.body)
                            case Msg.SLEEP:
                                if isinstance(message.body, SleepMessage):
                                    self.__update_sleep(message.body)
                            case Msg.INFO:
                                if isinstance(message.body, FilePathMessage):
                                    self.__update_filepath(message.body)
                                elif isinstance(message.body, URLMessage):
                                    self.__update_url_message(message.body)
                                elif isinstance(message.body, InfoMessage):
                                    self.__update_info_message(message.body)
                            case Msg.WARN:
                                if isinstance(message.body, WarnMessage):
                                    self.__update_warning(message.body)
                            case Msg.ERROR:
                                if isinstance(message.body, ErrorMessage):
                                    self.__update_error(message.body)
                except Empty:
                    if full_count == 0:
                        for slot in self.__slots.values():
                            slot.update(terminal, True)
                        full_count = View.FULL_INTERVAL_COUNT
                    else:
                        for slot in self.__slots.values():
                            slot.update(terminal, False)
                        full_count -= 1
                    sleep(0.25)

    def halt(self):
        self.__ready = False
        self.__queue.put(Message(kind=Msg.HALT, body=None))
        self.__halt_event.set()

    def join(self):
        self.__thread.join()

    def __term_size(self, terminal):
        height, width = terminal.get_size()
        self.__rows = max(1, (height - View.HEAD_SIZE) // View.ROW_SIZE)
        self.__columns = max(1, (width - 1) // View.COL_SIZE)
        self.__slot_size = (View.ROW_SIZE, width // self.__columns)

    def __term_header(self, terminal):
        _, width = terminal.get_size()
        line = "ViDL"
        if ANSI.len(line) < width - 10:
            terminal.print(line, 2, 10)

    def __create_slot(self, body):
        if body.provider == "slot":
            slot = View.Slot(body.index)
            self.__assign_position(slot)
            self.__slots[body.index] = slot

    def __assign_position(self, slot):
        for row in range(self.__rows):
            for column in range(self.__columns):
                occupied = any(
                    current_slot.is_position(row, column)
                    for current_slot in self.__slots.values()
                )
                if not occupied:
                    slot.set_position(row, column)
                    slot.set_size(self.__slot_size)
                    return

    # TODO: Plan for overflow and reflow when size changes

    def __update_sleep(self, body):
        if body.index in self.__slots:
            if body.provider == "download":
                self.__slots[body.index].set_timer(body.time_offset + 1)
                self.__slots[body.index].set_status(View.Status.WAITING)
            elif body.provider == "channel" or body.provider == "sub_channel":
                self.__slots[body.index].set_timer(body.time_offset)
                self.__slots[body.index].reset()
                if body.provider == "sub_channel":
                    self.__slots[body.index].set_top("", "")
                self.__slots[body.index].set_status_message(
                    "Sleeping", Util.get_time(int(time()) + body.time_offset)
                )
                self.__slots[body.index].set_status(View.Status.SLEEPING)

    def __update_item(self, body):
        if body.index in self.__slots:
            item = body.item
            slot = self.__slots[body.index]
            if body.provider == "channel":
                slot.set_top(body.name, body.last_date)
                slot.set_item(item.id, body.playlist_index, item.timestamp, item.title)
                slot.set_filepath(None)
                self.__update_item_media(slot, item)
            elif body.provider == "hook":
                if slot.get_status() != View.Status.DOWNLOADING:
                    self.__update_item_media(slot, item)
                if (
                    item.processor.lower() == "progress"
                    and item.status.lower() == "downloading"
                ):
                    slot.set_status(View.Status.DOWNLOADING)
                elif item.status.lower() == "finished":
                    slot.set_status(View.Status.INACTIVE)
                else:
                    slot.set_status(View.Status.PROCESSING)
                bitrate = int(item.tbr)
                if item.elapsed and item.downloaded_bytes:
                    bitrate = int((item.downloaded_bytes << 3) / item.elapsed) >> 10
                rem_bits = int(item.total_bytes - item.downloaded_bytes) >> 7
                remaining = int(rem_bits / bitrate) if bitrate != 0 else None
                slot.set_progress(
                    item.elapsed,
                    remaining,
                    item.downloaded_bytes,
                    item.total_bytes,
                    item.fragment_index,
                    item.fragment_count,
                    bitrate,
                    item.eta,
                    item._percent,
                    (item.processor, item.status),
                )
                processes = {
                    "Merger": "Merge",
                    "MoveFiles": "Move",
                    "FixupM3u8": "Normalize",
                    "": "Unknown",
                }
                if item.processor != "progress":
                    self.__slots[body.index].set_status_message(
                        processes[item.processor], item.status
                    )

    def __update_item_media(self, slot, item):
        video_stat = audio_stat = subtitle_stat = live_stat = ""
        if item.width and item.height:
            video_stat = f"{str(item.width):>4.4}x{str(item.height):<4.4} @ {str(int(item.fps)):<3.3} {item.dynamic_range.upper():<6.6} {item.vcodec.upper()}"
        if item.asr and item.audio_channels:
            audio_stat = f"{str(item.asr):>6.6}x{str(item.audio_channels):<2.2} {item.acodec.upper()}"
        if item.is_live:
            live_stat = "LIVE"
            if not item.timestamp:
                slot.set_item(item.id, 0, item.epoch, item.title)
        elif item.live_status in ("is_upcoming", "was_live", "post_live"):
            live_stat = "STREAM"
        slot.set_media(
            item.duration,
            video_stat,
            audio_stat,
            subtitle_stat,
            item.ext,
            live_stat,
            item.age_limit,
        )

    def __update_filepath(self, body):
        if body.index in self.__slots:
            if body.provider == "download":
                self.__slots[body.index].set_filepath(body.path)
                self.__slots[body.index].set_status(View.Status.DOWNLOADING)

    def __update_url_message(self, body):
        pass
        # if body.index in self.__slots:
        # self.__slots[body.index].set_status_message(body.provider, body.url)
        # self.__slots[body.index].set_status(View.Status.PROCESSING)

    def __update_info_message(self, body):
        if body.index in self.__slots:
            if body.message == "Downloading":
                self.__slots[body.index].set_status_message(body.provider, body.target)
                self.__slots[body.index].set_status(View.Status.PROCESSING)
            if body.message == "Metadata":
                self.__slots[body.index].set_status_message(body.provider, body.target)
                self.__slots[body.index].set_status(View.Status.PROCESSING)

    def __update_count(self, body):
        if body.index in self.__slots:
            if body.provider == "channel":
                self.__slots[body.index].set_count(body.playlist_count)

    def __update_warning(self, body):
        if body.index in self.__slots:
            self.__slots[body.index].set_status_message(body.target, body.message)
            self.__slots[body.index].set_status(View.Status.WARNING)

    def __update_error(self, body):
        if body.index in self.__slots:
            message = body.message.split(":", maxsplit=1)
            if len(message) > 1:
                provider = message[0]
                message = message[1]
                self.__slots[body.index].set_status_message(provider, message)
            else:
                self.__slots[body.index].set_status_message(
                    f"{body.provider}/{body.target}", body.message
                )
            self.__slots[body.index].set_status(View.Status.ERROR)
