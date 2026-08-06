import os
from dataclasses import dataclass, replace
from enum import Enum
from statistics import StatisticsError, median
from time import time

from .ansi import ANSI
from .config import Config
from .item import Item
from .ringbuffer import RingBuffer
from .terminal import Terminal
from .util import Util


class View:
    HEADER = -1
    HEADER_HEIGHT = 5
    ROW_MIN_SIZE = 12
    COL_MIN_SIZE = 120
    NF_PREFIX = ""
    NF_SUFFIX = ""

    @dataclass
    class Status:
        class State(Enum):
            INACTIVE = 0
            WAITING = 1
            SLEEPING = 2
            DOWNLOAD = 3
            DOWNLOAD_WAIT = 4
            PROCESS = 5
            PROCESS_WAIT = 6
            WARNING = 7
            ERROR = 8

        state: State
        provider: str = ""
        message: str = ""

        def __post_init__(self) -> None:
            self.provider = self.provider.replace(":", " ").capitalize()

        def symbol(self) -> str:
            match self.state:
                case self.State.INACTIVE:
                    return ANSI.Color.Black + "●" + ANSI.Color.DefaultFg
                case self.State.WAITING:
                    return "○"
                case self.State.SLEEPING:
                    return ANSI.Dim + "○" + ANSI.DimReset
                case self.State.DOWNLOAD:
                    return ANSI.Color.Cerise + "●" + ANSI.Color.DefaultFg
                case self.State.DOWNLOAD_WAIT:
                    return ANSI.Color.Cerise + "○" + ANSI.Color.DefaultFg
                case self.State.PROCESS:
                    return ANSI.Color.PineGreen + "●" + ANSI.Color.DefaultFg
                case self.State.PROCESS_WAIT:
                    return ANSI.Color.PineGreen + "○" + ANSI.Color.DefaultFg
                case self.State.WARNING:
                    return ANSI.Color.BurntSienna + "✖" + ANSI.Color.DefaultFg
                case self.State.ERROR:
                    return ANSI.Color.Cerise + "✖" + ANSI.Color.DefaultFg

        def abbreviation(self) -> str:
            match self.state:
                case self.State.INACTIVE:
                    return "IN"
                case self.State.WAITING:
                    return "WT"
                case self.State.SLEEPING:
                    return "SP"
                case self.State.DOWNLOAD:
                    return "DL"
                case self.State.DOWNLOAD_WAIT:
                    return "DW"
                case self.State.PROCESS:
                    return "PR"
                case self.State.PROCESS_WAIT:
                    return "PW"
                case self.State.WARNING:
                    return "WR"
                case self.State.ERROR:
                    return "ER"

    @dataclass
    class Top:
        name: str = ""
        count: str = ""

    @dataclass(frozen=True)
    class Size:
        rows: int
        cols: int
        origin_row: int = 1
        origin_col: int = 1

    def __init__(self, border=True, header=False) -> None:
        self.__size = View.Size(rows=1, cols=1)
        self.__border = border
        self.__header = header
        self.__top_index = 0
        self.__view_top_decorator = ("", "")
        if Config.settings["General"]["nerd_fonts"]:
            self.__view_top_decorator = (View.NF_PREFIX, View.NF_SUFFIX)
        self.__status = View.Status(state=View.Status.State.INACTIVE)
        self.__timer: int | None = None
        self.__time_divider: str = ":"
        self.__top = View.Top()
        self.__temp_filepath: str | None = None
        self.__playlist_length = 0
        self.__bitrate_ring = RingBuffer(40)
        self.__item_entity: Item.Entity | None = None
        self.__item_progress: Item.Progress | None = None
        self.__item_media: Item.Media | None = None

    def __draw_header(self, terminal: Terminal) -> None:
        line = "ViDL"
        if ANSI.len(line) < self.__size.cols - 10:
            terminal.print(line, 2, 10)

    def __draw_border(self, terminal: Terminal) -> None:
        title = ""
        if ANSI.len(self.__top.name) < self.__size.cols - 12:
            title = " │ " + f"{self.__top.name} {self.__top.count}"
        header = (
            self.__view_top_decorator[0]
            + ANSI.Inverse
            + " "
            + f"{str(self.__top_index):>2.2}"
            + title
            + " "
            + ANSI.InverseReset
            + self.__view_top_decorator[1]
        )
        terminal.print(
            "╭"
            + ("─" * 2)
            + header
            + ("─" * (self.__size.cols - 4 - ANSI.len(header)))
            + "╮",
            self.__size.origin_row,
            self.__size.origin_col,
        )
        for rb in range(
            self.__size.origin_row + 1, self.__size.origin_row + self.__size.rows - 1
        ):
            terminal.print("│", rb, self.__size.origin_col)
            terminal.print("│", rb, self.__size.origin_col + self.__size.cols - 1)
        terminal.print(
            "╰" + ("─" * (self.__size.cols - 2)) + "╯",
            self.__size.origin_row + self.__size.rows - 1,
            self.__size.origin_col,
        )

    def __status_line(self, terminal: Terminal):
        line = f"{self.__status.symbol()}  {self.timer()}"
        if self.__status.state == View.Status.State.DOWNLOAD:
            if self.__item_progress is not None:
                if self.__item_progress.size_total:
                    remaining_time = self.__smooth_remaining_time()
                    remaining_string = Util.format_seconds(remaining_time)
                    eta = f"ETA {Util.get_time(int(time()) + remaining_time)}"
                    length = (
                        self.__size.cols
                        - ANSI.len(line)
                        - ANSI.len(remaining_string)
                        - ANSI.len(eta)
                        - 19
                    )
                    meter = self.__progress_meter(
                        length, self.__item_progress.percent, "━"
                    )
                    line = line + "/ " + remaining_string + " " + meter + "  " + eta
                    terminal.print(
                        line, self.__size.origin_row + 2, self.__size.origin_col + 6
                    )
                elif self.__item_progress.size_current:
                    size = int(self.__item_progress.size_current) >> 20
                    line = line + " " + f"{size:>6} MB"
                    if self.__timer is not None:
                        timer = max(1, int(time()) - self.__timer)
                        if timer > 10:
                            bitrate = (
                                int(
                                    (int(self.__item_progress.size_current) << 3)
                                    / timer
                                )
                                >> 10
                            )
                            line = line + " " + f"{bitrate:>6} kbps"
                    line_len = ANSI.len(line)
                    if line_len < self.__size.cols - 10:
                        line = line + " " * (self.__size.cols - 10 - line_len)
                        terminal.print(
                            line,
                            self.__size.origin_row + 2,
                            self.__size.origin_col + 6,
                        )
        else:
            if self.__status.provider or self.__status.message:
                color = ""
                if self.__status.state == View.Status.State.ERROR:
                    color = ANSI.Color.Cerise
                elif self.__status.state == View.Status.State.WARNING:
                    color = ANSI.Color.BurntSienna
                else:
                    color = ANSI.Color.NeonChartreuse
                line = (
                    line
                    + f"  {self.__status.provider}: "
                    + f"{color}{self.__status.message}{ANSI.Color.DefaultFg}"
                )
            line_len = ANSI.len(line)
            if line_len < self.__size.cols - 10:
                line = line + " " * (self.__size.cols - 10 - line_len)
                terminal.print(
                    line, self.__size.origin_row + 2, self.__size.origin_col + 6
                )

    def __item_line(self, terminal: Terminal):
        if self.__item_entity is not None:
            max_len = self.__size.cols - 34
            index = 0
            index_string = ""
            if self.__item_entity.index:
                index = self.__item_entity.index
            if index > 0 and self.__playlist_length > 0:
                index = max(1, self.__playlist_length - index + 1)
                index_string = str(index)
            line = (
                f"{Util.get_date(self.__item_entity.date):>10.10}"
                + " │ "
                + f"{index_string:>4.4}"
                + " │ "
                + f"{self.__item_entity.title:{max_len}.{max_len}}"
            )
            line_len = ANSI.len(line)
            if line_len < self.__size.cols - 10:
                line = line + " " * (self.__size.cols - 8 - line_len)
                terminal.print(
                    line, self.__size.origin_row + 4, self.__size.origin_col + 4
                )
            if self.__item_entity.title != self.__item_entity.id:
                line = f" [{self.__item_entity.id}]"
                line_len = ANSI.len(line)
                if line_len < self.__size.cols - 10:
                    terminal.print(
                        line,
                        self.__size.origin_row + 4,
                        self.__size.origin_col + (self.__size.cols - 4 - line_len),
                    )
            terminal.print(
                ("─" * 11) + "┴" + ("─" * 6) + "┼" + ("─" * (self.__size.cols - 27)),
                self.__size.origin_row + 5,
                self.__size.origin_col + 4,
            )
        else:
            terminal.print(
                " " * (self.__size.cols - 2),
                self.__size.origin_row + 4,
                self.__size.origin_col + 1,
            )
            terminal.print(
                " " * (self.__size.cols - 2),
                self.__size.origin_row + 5,
                self.__size.origin_col + 1,
            )

    def __media_line(self, terminal: Terminal):
        max_len = self.__size.cols - 30
        if self.__item_media is not None:
            line = f"│ {Util.format_seconds(self.__item_media.length, two_parts=False)} {self.__item_media.extension}"
            if self.__item_progress is not None and self.__item_entity is not None:
                line += (
                    f" {self.__item_progress.extension.upper():<4.4}"
                    + f" {self.__item_entity.stream}"
                    + f" ({self.__item_entity.age_limit})"
                )
                if self.__item_progress.size_total:
                    size = int(self.__item_progress.size_total) >> 20
                    line = line + " " + f"{size:>6} MB"
            if ANSI.len(line) < max_len:
                terminal.print(
                    f"{line:<{max_len}.{max_len}}",
                    self.__size.origin_row + 6,
                    self.__size.origin_col + 22,
                )
            line = f"│ {self.__item_media.video_stat}"
            if ANSI.len(line) < max_len:
                terminal.print(
                    f"{line:<{max_len}.{max_len}}",
                    self.__size.origin_row + 7,
                    self.__size.origin_col + 22,
                )
            line = f"│ {self.__item_media.audio_stat}"
            if ANSI.len(line) < max_len:
                terminal.print(
                    f"{line:<{max_len}.{max_len}}",
                    self.__size.origin_row + 8,
                    self.__size.origin_col + 22,
                )
            line = f"│ {self.__item_media.subtitle_stat}"
            if ANSI.len(line) < max_len:
                terminal.print(
                    f"{line:<{max_len}.{max_len}}",
                    self.__size.origin_row + 9,
                    self.__size.origin_col + 22,
                )
        elif self.__item_entity is not None:
            for rb in range(6, 10):
                terminal.print(
                    "│" + " " * (max_len - 1),
                    self.__size.origin_row + rb,
                    self.__size.origin_col + 22,
                )
        else:
            for rb in range(6, 10):
                terminal.print(
                    " " * max_len,
                    self.__size.origin_row + rb,
                    self.__size.origin_col + 22,
                )

    def __progress_meter(self, length, percent, kind="-") -> str:
        if length < 1:
            return ""
        progress = int((length * min(100, max(0, percent))) / 100)
        remaining = length - progress
        meter = kind * progress + ANSI.Dim + kind * remaining + ANSI.DimReset
        return meter

    def __smooth_remaining_time(self) -> int:
        if self.__item_progress is not None:
            elapsed = self.__item_progress.time_current
            size = int(self.__item_progress.size_current)
            bitrate = 0
            if elapsed and size:
                bitrate = int((size << 3) / elapsed) >> 10
                if self.__item_progress is not None:
                    self.__bitrate_ring.append(bitrate)
            remaining_bits = int(self.__item_progress.size_total - size) >> 7
            try:
                bitrate = median([b for b in self.__bitrate_ring if b > 0])
            except StatisticsError:
                ...
            return int(remaining_bits / bitrate) if bitrate > 0 else 0
        return 0

    def __update_filesize(self) -> None:
        size_current = 0
        if self.__temp_filepath is not None:
            for filename in [self.__temp_filepath, self.__temp_filepath + ".part"]:
                try:
                    size_current = os.path.getsize(filename)
                except OSError:
                    ...
        if self.__item_progress is None:
            self.__item_progress = Item.Progress(
                size_current=size_current, size_total=size_current
            )
        else:
            if size_current > self.__item_progress.size_current:
                self.__item_progress.size_current = size_current

    def resize(
        self,
        rows: int | None = None,
        cols: int | None = None,
        origin_row: int | None = None,
        origin_col: int | None = None,
    ) -> Size:
        self.__size = replace(
            self.__size,
            rows=self.__size.rows if rows is None else rows,
            cols=self.__size.cols if cols is None else cols,
            origin_row=(self.__size.origin_row if origin_row is None else origin_row),
            origin_col=(self.__size.origin_col if origin_col is None else origin_col),
        )
        return self.__size

    def reset(self) -> None:
        self.__time_divider = ":"
        self.__temp_filepath = None
        self.__item_entity = None
        self.__item_progress = None
        self.__item_media = None

    def set_top_index(self, index: int) -> None:
        self.__top_index = index

    def get_top_index(self) -> int:
        return self.__top_index

    def update_status(self, terminal: Terminal) -> None:
        if self.__header:
            ...
        else:
            self.__status_line(terminal)

    def update(self, terminal: Terminal) -> None:
        if self.__header:
            self.__draw_header(terminal)
        else:
            self.__update_filesize()
            self.__item_line(terminal)
            self.__media_line(terminal)
            self.__draw_border(terminal)
            self.__status_line(terminal)

    def blink(self) -> None:
        if self.__time_divider == ":":
            self.__time_divider = " "
        else:
            self.__time_divider = ":"

    def set_status(self, status: Status) -> None:
        self.__status = status

    def get_status(self) -> Status:
        return self.__status

    def set_timer(self, time_offset: int) -> None:
        self.__timer = int(time()) + time_offset

    def timer(self) -> str:
        if self.__timer is not None:
            return Util.format_seconds(int(time()) - self.__timer)
        return "--:--"

    def set_top(self, name: str) -> None:
        self.__top = View.Top(name=name)

    def reset_top(self) -> None:
        self.__top = View.Top()

    def set_count(self, value: int) -> None:
        if value > 0:
            self.__top = View.Top(self.__top.name, f"({value})")
            self.__playlist_length = value

    def set_filepath(self, filepath: str) -> None:
        self.__temp_filepath = filepath

    def set_item_entity(self, entity: Item.Entity) -> None:
        self.__item_entity = entity

    def set_item_progress(self, progress: Item.Progress) -> None:
        self.__item_progress = progress

    def set_item_media(self, media: Item.Media) -> None:
        self.__item_media = media
