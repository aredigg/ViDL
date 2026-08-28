import os
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from statistics import StatisticsError, median
from time import time
from typing import ClassVar

from .ansi import ANSI
from .config import Config
from .item import Item
from .terminal import Terminal
from .util import Util


class View:
    HEADER: int = -1
    HEADER_HEIGHT: int = 5
    ROW_MIN_SIZE: int = 12
    COL_MIN_SIZE: int = 120
    RING_SIZE: int = 20
    NF_PREFIX: str = ""
    NF_SUFFIX: str = ""
    NF_VIDEO: str = ""
    NF_AUDIO: str = ""
    NF_SUBTITLE: str = "󰨖"
    INVALID_TIIME: str = "--:-- "
    ANIMATED: ClassVar[list[str]] = ["○", "●", "○", "●"]
    COUNTDOWN: ClassVar[list[str]] = [
        "   ",
        " 1 ",
        " 2 ",
        " 3 ",
        " 4 ",
        " 5 ",
        " 6 ",
        " 7 ",
        " 8 ",
        " 9 ",
    ]

    @dataclass
    class Status:
        class State(Enum):
            INACTIVE = 0
            WAITING = 1
            SLEEPING = 2
            DOWNLOAD = 3
            DOWNLOAD_WAIT = 4
            DOWNLOAD_REQ_WAIT = 5
            PROCESS = 6
            PROCESS_WAIT = 7
            WARNING = 8
            ERROR = 9

        state: State
        provider: str = ""
        message: str = ""

        def __post_init__(self) -> None:
            self.provider = self.provider.replace(":", " ")

        def symbol(self, seq: int) -> str:
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
                case self.State.DOWNLOAD_REQ_WAIT:
                    return ANSI.Color.Cerise + View.ANIMATED[seq] + ANSI.Color.DefaultFg
                case self.State.PROCESS:
                    return (
                        ANSI.Color.PineGreen + View.ANIMATED[seq] + ANSI.Color.DefaultFg
                    )
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
                case self.State.DOWNLOAD_REQ_WAIT:
                    return "DR"
                case self.State.PROCESS:
                    return "PR"
                case self.State.PROCESS_WAIT:
                    return "PW"
                case self.State.WARNING:
                    return "WR"
                case self.State.ERROR:
                    return "ER"

        def emoji(self) -> str:
            match self.state:
                case self.State.INACTIVE:
                    return "⚫"
                case self.State.WAITING:
                    return "⏳"
                case self.State.SLEEPING:
                    return "💤"
                case self.State.DOWNLOAD:
                    return "🔴"
                case self.State.DOWNLOAD_WAIT:
                    return "⏸️"
                case self.State.DOWNLOAD_REQ_WAIT:
                    return "⏸️"
                case self.State.PROCESS:
                    return "🟢"
                case self.State.PROCESS_WAIT:
                    return "⏸️"
                case self.State.WARNING:
                    return "⚠️"
                case self.State.ERROR:
                    return "❗"

    @dataclass
    class Top:
        name: str = ""
        count: str = ""
        cutoff: str = ""

    @dataclass(frozen=True)
    class Size:
        rows: int
        cols: int
        origin_row: int = 1
        origin_col: int = 1

    def __init__(self, border: bool = True, header: bool = False) -> None:
        self.__size = View.Size(rows=1, cols=1)
        self.__border = border
        self.__header = header
        self.__visible = False
        self.__top_index: int = 0
        self.__view_top_decorator = ("", "")
        if Config.settings["General"]["nerd_fonts"]:
            self.__view_top_decorator = (View.NF_PREFIX, View.NF_SUFFIX)
        self.__status = View.Status(state=View.Status.State.INACTIVE)
        self.__timer: int | None = None
        self.__time_divider: str = ":"
        self.__top = View.Top()
        self.__temp_filepath: str | None = None
        self.__playlist_length = 0
        self.__bitrate_ring: deque[int] = deque(maxlen=View.RING_SIZE)
        self.__size_delta_ring: deque[int] = deque(maxlen=View.RING_SIZE)
        self.__time_delta_ring: deque[float] = deque(maxlen=View.RING_SIZE)
        self.__previous_elapsed = None
        self.__previous_size = None
        self.__item_entity: Item.Entity | None = None
        self.__item_progress: Item.Progress | None = None
        self.__item_media: Item.Media | None = None

    def __draw_header(self, terminal: Terminal) -> None:
        line = "𓃥  ViDL"
        if ANSI.len(line) < self.__size.cols - 10:
            terminal.print(line, 2, 10)
        if self.__status.message:
            line = f" --- {ANSI.Color.FashionBlue}{self.__status.message}{ANSI.Color.DefaultFg}"
            if ANSI.len(line) < self.__size.cols - 10:
                terminal.print(line, 3, 10)

    def __draw_border(self, terminal: Terminal) -> None:
        title = ""
        if ANSI.len(self.__top.name) < self.__size.cols - 12:
            title = " │ " + f"{self.__top.name} {self.__top.count}"
        header = (
            self.__view_top_decorator[0]
            + ANSI.Inverse
            + " "
            + f"{self.__top_index!s:>2.2}"
            + title
            + " "
            + ANSI.InverseReset
            + self.__view_top_decorator[1]
        )
        if self.__top.cutoff:
            header += (
                "──"
                + self.__view_top_decorator[0]
                + ANSI.Inverse
                + " "
                + self.__top.cutoff
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

    def __status_line(self, terminal: Terminal, sequence: int):
        line = f"  {self.__status.symbol(sequence)}  {self.timer()}"
        if self.__status.state == View.Status.State.DOWNLOAD:
            if self.__item_progress is not None:
                if self.__item_progress.size_total:
                    remaining_time, valid = self.__smooth_remaining_time()
                    remaining_string = (
                        Util.format_seconds(remaining_time)
                        if valid
                        else View.INVALID_TIIME
                    )
                    eta = (
                        f"ETA {Util.get_time(int(time()) + remaining_time)}"
                        if valid
                        else " " * 9
                    )
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
        else:
            if self.__status.provider or self.__status.message:
                provider = self.__status.provider
                message = self.__status.message
                if (len(provider) + len(message)) > (self.__size.cols - 14):
                    provider_len = self.__size.cols - 14 - len(message)
                    if provider_len > 0:
                        provider = ANSI.trim(provider, provider_len)
                    else:
                        provider = ""
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
        self.__print(terminal, 2, line)

    def __item_line(self, terminal: Terminal):
        if self.__item_entity is not None:
            max_len = self.__size.cols - 34
            if max_len > 0:
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
                    + f"{index_string!s:>4.4}"
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
                    ("─" * 11)
                    + "┴"
                    + ("─" * 6)
                    + "┼"
                    + ("─" * (self.__size.cols - 27)),
                    self.__size.origin_row + 5,
                    self.__size.origin_col + 4,
                )
        else:
            self.__print(terminal, 4)
            self.__print(terminal, 5)

    def __media_line(self, terminal: Terminal):
        OFFSET = 18
        if self.__item_media is not None:
            line = (
                " " * OFFSET
                + f"│ {Util.format_seconds(self.__item_media.length, two_parts=False)} "
            )
            extension = self.__item_media.extension.upper()
            container = self.__item_media.container.upper()
            line += f"{extension}" if extension else ""
            line += "/" if extension and container else " "
            line += f"{container} " if container else " "
            if self.__item_progress is not None and self.__item_entity is not None:
                line += f"{self.__item_entity.stream} ({self.__item_entity.age_limit}) "
                if self.__item_progress.size_total:
                    size = int(self.__item_progress.size_total) >> 20
                    line += f"{size:>6} MB"
            self.__print(terminal, 6, line)
            stats: list[str] = []
            if self.__item_media.video_stat:
                stats.append(self.__item_media.video_stat)
            if self.__item_media.audio_stat:
                stats.append(self.__item_media.audio_stat)
            line = " " * OFFSET + "│ " + " | ".join(stats)
            self.__print(terminal, 7, line)
            line = " " * OFFSET + f"│ {self.__item_media.subtitle_stat}"
            self.__print(terminal, 8, line)
            line = " " * OFFSET + "│"
            self.__print(terminal, 9, line)
        elif self.__item_entity is not None:
            for row in range(6, 10):
                line = " " * OFFSET + "│"
                self.__print(terminal, row, line)
        else:
            for row in range(6, 10):
                self.__print(terminal, row)

    def __print(self, terminal: Terminal, row: int, line: str = ""):
        line = ANSI.trim(line, self.__size.cols - 8)
        terminal.print(
            line + " " * max(0, self.__size.cols - ANSI.len(line) - 8),
            self.__size.origin_row + row,
            self.__size.origin_col + 4,
        )

    def __progress_meter(self, length: int, percent: float, kind: str = "-") -> str:
        if length < 1:
            return ""
        progress = int((length * min(100, max(0, percent))) / 100)
        remaining = length - progress
        meter = kind * progress + ANSI.Dim + kind * remaining + ANSI.DimReset
        return meter

    def __smooth_remaining_time(self) -> tuple[int, bool]:
        if self.__item_progress is not None:
            elapsed = self.__item_progress.time_current
            size = int(self.__item_progress.size_current)
            if not (
                len(self.__time_delta_ring) > 1 and len(self.__size_delta_ring) > 1
            ):
                self.__time_delta_ring.append(elapsed)
                self.__size_delta_ring.append(size)
            else:
                if self.__previous_elapsed is None:
                    self.__previous_elapsed = elapsed
                    self.__previous_size = size
                    return 0, False
                self.__time_delta_ring.append(elapsed - self.__time_delta_ring[-1])
                self.__size_delta_ring.append(size - self.__size_delta_ring[-1])
                elapsed_delta = sum(self.__time_delta_ring)
                size_delta = sum(self.__size_delta_ring)
                bitrate = 0
                if elapsed_delta and size_delta:
                    bitrate = int((size_delta << 3) / elapsed_delta) >> 10
                    self.__bitrate_ring.append(bitrate)
                if len(self.__bitrate_ring) != View.RING_SIZE:
                    return 0, False
                remaining_bits = int(self.__item_progress.size_total - size) >> 7
                try:
                    bitrate = median([b for b in self.__bitrate_ring if b > 0])
                except StatisticsError:
                    ...
                return (
                    (int(remaining_bits / bitrate), True) if bitrate > 0 else (0, False)
                )
        return 0, False

    def __update_filesize(self) -> None:
        size_current = 0
        if self.__temp_filepath is not None and self.__item_media is not None:
            temp_filepath = (
                self.__temp_filepath.removesuffix("NA") + self.__item_media.extension
            )
            for filename in [temp_filepath, temp_filepath + ".part"]:
                try:
                    size_current = os.path.getsize(filename)
                    match self.__status.state:
                        case (
                            View.Status.State.DOWNLOAD_WAIT
                            | View.Status.State.DOWNLOAD_REQ_WAIT
                        ):
                            self.__status.state = View.Status.State.DOWNLOAD
                        case _:
                            ...
                except OSError:
                    ...
        if self.__item_progress is None:
            self.__item_progress = Item.Progress(
                size_current=size_current, size_total=size_current
            )
        else:
            self.__item_progress.size_current = max(
                size_current, self.__item_progress.size_current
            )

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
        self.__previous_elapsed = None
        self.__previous_size = None
        #    self.__bitrate_ring.clear()
        self.__size_delta_ring.clear()
        self.__time_delta_ring.clear()

    def set_top_index(self, index: int) -> None:
        self.__top_index = index

    def get_top_index(self) -> int:
        return self.__top_index

    def update_status(self, terminal: Terminal, sequence: int) -> None:
        if not self.__header:
            self.__status_line(terminal, sequence)

    def update(self, terminal: Terminal, sequence: int) -> None:
        if self.__header:
            self.__draw_header(terminal)
        else:
            if self.__visible:
                self.__update_filesize()
                self.__item_line(terminal)
                self.__media_line(terminal)
                self.__draw_border(terminal)
                self.__status_line(terminal, sequence)

    def set_status(self, status: Status) -> None:
        self.__status = status

    def get_status(self) -> Status:
        return self.__status

    def set_timer(self, time_offset: int) -> None:
        self.__timer = int(time()) + time_offset

    def timer(self) -> str:
        if self.__timer is not None:
            match self.__status.state:
                case View.Status.State.DOWNLOAD:
                    return Util.format_seconds(max(0, int(time()) - self.__timer))
                case (
                    View.Status.State.SLEEPING
                    | View.Status.State.DOWNLOAD_WAIT
                    | View.Status.State.DOWNLOAD_REQ_WAIT
                ):
                    return Util.format_seconds(min(0, int(time()) - self.__timer))
                case _:
                    return Util.format_seconds(0)
        return View.INVALID_TIIME

    def countdown(self) -> str:
        seconds = 0
        match self.__status.state:
            case (
                View.Status.State.SLEEPING
                | View.Status.State.DOWNLOAD_WAIT
                | View.Status.State.DOWNLOAD_REQ_WAIT
            ):
                if self.__timer is not None:
                    seconds = int(time()) - self.__timer
            case View.Status.State.DOWNLOAD:
                seconds, _ = self.__smooth_remaining_time()
            case _:
                pass
        return (
            f" {View.COUNTDOWN[seconds]}"
            if 0 < seconds < 10
            else f" {View.COUNTDOWN[0]}"
        )

    def set_top(self, name: str) -> None:
        self.__top = View.Top(name=name)

    def reset_top(self) -> None:
        self.__top = View.Top()

    def set_visible(self, visible: bool):
        self.__visible = visible

    def set_count(self, value: int) -> None:
        if value > 0:
            self.__top = View.Top(self.__top.name, f"({value})")
            self.__playlist_length = value

    def set_cutoff(self, value: int) -> None:
        if value > 0:
            cutoff = Util.get_date(value)
            self.__top = View.Top(self.__top.name, self.__top.count, cutoff=cutoff)

    def set_filepath(self, filepath: str) -> None:
        self.__temp_filepath = filepath

    def set_item_entity(self, entity: Item.Entity) -> None:
        self.__item_entity = entity

    def set_item_progress(self, progress: Item.Progress) -> None:
        self.__item_progress = progress

    def set_item_media(self, media: Item.Media) -> None:
        self.__item_media = media
