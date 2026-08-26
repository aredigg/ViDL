import csv
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from queue import Queue
from threading import Event, RLock
from time import time
from typing import ClassVar, cast

from yt_dlp.utils import DownloadError, ExtractorError
from yt_dlp.YoutubeDL import YoutubeDL

from .config import Config
from .debug import Debug
from .error import Error
from .item import Item
from .message import (
    CountMessage,
    CutoffMessage,
    EntityMessage,
    ErrorMessage,
    InfoMessage,
    MediaMessage,
    Message,
    PathMessage,
    SleepMessage,
)


class Channel:
    __lock = RLock()
    __write_archive_lock = RLock()

    def __init__(
        self,
        name: str | None,
        url: str | None,
        last_dl_dte: str | int | None,
        last_at_dte: str | int | None,
        last_error: str | None,
        sub_level: int = 0,
    ) -> None:
        self.__name = name
        self.__url = url
        self.__id: str | None = None
        try:
            self.__last_download_date = int(last_dl_dte or 0)
            self.__last_attempt_date = int(last_at_dte or 0)
        except (ValueError, TypeError):
            self.__last_download_date = 0
            self.__last_attempt_date = 0
        self.__last_error = last_error
        self.__sub_level = sub_level
        self.__active = False
        self.__slot_index: int | None = None
        self.__epoch_cutoff: int = 0
        self.__epoch_cutoff_passed: bool = False
        self.__halt_event: Event | None = None

    def row(self):
        with Channel.__lock:
            return [
                self.__name or "",
                self.__url or "",
                self.__last_download_date or 0,
                self.__last_attempt_date or 0,
                self.__last_error or "",
            ]

    def get_last_date(self):
        ret = datetime.fromtimestamp(
            self.__last_download_date or self.__last_attempt_date or 0, tz=timezone.utc
        ).astimezone()
        return ret

    def get_last_error(self) -> str | None:
        return self.__last_error

    def get_name(self) -> str | None:
        return self.__name

    def get_epoch_cutoff(self) -> tuple[int, bool]:
        return self.__epoch_cutoff, self.__epoch_cutoff_passed

    def assign_epoch_cutoff(self, cutoff: tuple[int, bool]):
        with Channel.__lock:
            self.__epoch_cutoff = cutoff[0]
            self.__epoch_cutoff_passed = cutoff[1]

    def set_halt_event(self, halt_event: Event | None):
        self.__halt_event = halt_event

    def set_active(self):
        self.__active = True

    def active(self):
        return self.__active

    def report_error(self, message: str):
        self.__set_error(message)

    # ----- Inside slot thread

    def download(
        self,
        slot: int,
        processor: YoutubeDL,
        queue: Queue[Message],
        playlist_index: int = 1,
    ):
        if self.__slot_index is not None:
            return False

        self.__slot_index = slot
        if self.__sub_level == 0:
            self.__reset_epoch_cutoff()
        try:
            self.__set_attempt_date()
            result = self.__extract(processor, queue, playlist_index)
            if result:
                self.__set_download_date()
            return result
        finally:
            self.__slot_index = None
            self.__active = False

    def __extract(
        self, processor: YoutubeDL, queue: Queue[Message], playlist_index: int
    ) -> bool:
        if self.__halt_event is not None and self.__halt_event.is_set():
            self.__report_error(queue, "Got halted")
            return False
        if self.__slot_index is None:
            return False
        if self.__url is None or self.__url == "None":
            self.__url = self.__name
        info: Mapping[str, object] = {}
        try:
            if self.__url is not None:
                info = processor.extract_info(self.__url, download=False, process=False)
        except DownloadError as e:
            self.__handle_error_exception(queue, e)
            return False
        if info:
            info = cast(dict[str, object], info)
            self.__name = (
                Item.get_str(info, "title")
                or Item.get_str(info, "channel")
                or Item.get_str(info, "uploader")
                or f"{Item.get_str(info, 'extractor')} ({Item.get_str(info, 'id')})"
            )
            self.__id = Item.get_str(info, "id")

            queue.put(
                EntityMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    entity=Item.get_entity(
                        info=info,
                        name=Item.get_str(info, "channel") or self.__name
                        if self.__sub_level == 0
                        else "",
                        index=playlist_index,
                    ),
                )
            )
            queue.put(
                MediaMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    media=Item.get_media(
                        info=Item.requested_format(info),
                    ),
                )
            )
            if info.get("_type") == "playlist":
                queue.put(
                    InfoMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.CHANNEL,
                        target=self.__name,
                        message=Item.get_str(info, "extractor_key")
                        or Item.get_str(info, "extractor")
                        or "Playlist",
                    )
                )

                sub_channels: list[Channel] = []
                if entries := Item.get_lst(info, "entries"):
                    queue.put(
                        CountMessage(
                            index=self.__slot_index,
                            provider=Message.Provider.CHANNEL,
                            value=len(entries),
                        )
                    )
                    for entry in cast(list[dict[str, object]], entries):
                        if (
                            not (
                                self.__halt_event is not None
                                and self.__halt_event.is_set()
                            )
                        ) and (
                            url := Item.get_str(entry, "webpage_url")
                            or Item.get_str(entry, "url")
                        ):
                            sub_channels.append(
                                Channel(
                                    url,
                                    None,
                                    None,
                                    None,
                                    None,
                                    sub_level=self.__sub_level + 1,
                                )
                            )
                if sub_channels:
                    for enum_playlist_index, channel in enumerate(
                        sub_channels, start=1
                    ):
                        if not self.__epoch_cutoff_passed:
                            channel.set_active()
                            channel.assign_epoch_cutoff(self.get_epoch_cutoff())
                            channel.set_halt_event(self.__halt_event)
                            _ = channel.download(
                                self.__slot_index, processor, queue, enum_playlist_index
                            )
                            self.assign_epoch_cutoff(channel.get_epoch_cutoff())
            else:
                format = Item.enumerate_best_format(info)
                if Item.no_vertical(format):
                    self.__record_archive(processor, info)
                    self.__report_error(queue, "Vertical video")
                    return False
                if not Item.high_resolution(format):
                    self.__record_archive(processor, info)
                    self.__report_error(queue, "Low resolution")
                    return False
                if not Item.outside_deferred(info, format):
                    self.__report_error(queue, "Defer low resolution")
                    return False
                if not self.__within_cutoff(info):
                    self.__epoch_cutoff_passed = True
                    self.__report_error(queue, "Outside cutoff")
                    return False
                else:
                    if self.__epoch_cutoff != 0:
                        queue.put(
                            CutoffMessage(
                                index=self.__slot_index,
                                provider=Message.Provider.CHANNEL,
                                value=self.__epoch_cutoff,
                            )
                        )

                record_download_archive = cast(
                    Callable[[dict[str, object]], None],
                    processor.record_download_archive,
                )
                record_download_archive(info)
                path = cast(
                    Callable[[dict[str, object], str], str], processor.prepare_filename
                )
                queue.put(
                    PathMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.DOWNLOAD,
                        path=path(info, "temp"),
                    )
                )
                sleep_time = (Item.get_int(format, "available_at") or time()) - time()
                sleep_time = max(Config.SLEEP_INTERVAL, sleep_time + 1)
                queue.put(
                    SleepMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.DOWNLOAD,
                        sleep_time=sleep_time,
                        required=sleep_time > Config.SLEEP_INTERVAL,
                    )
                )
                process_time = int(time())
                ret = self.__download(processor, queue, info)
                min_sleep = cast(int, Config.settings["Download"]["sleep_interval"])
                cutoff = (
                    cast(int, (Config.settings["Download"]["post_sleep_cutoff"])) * 60
                )

                sleep_time = max(min_sleep, min(cutoff, int(time()) - process_time))
                queue.put(
                    SleepMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.CHANNEL,
                        sleep_time=sleep_time,
                    )
                )
                sleep_until = int(time()) + sleep_time
                while int(time()) < sleep_until:
                    if self.__halt_event is not None and self.__halt_event.wait(1):
                        return ret
                return ret
        else:
            return False
        return True

    def __download(
        self, processor: YoutubeDL, queue: Queue[Message], info: Mapping[str, object]
    ) -> bool:
        try:
            return (
                processor.download(
                    [
                        Item.get_str(info, "original_url")
                        or Item.get_str(info, "webpage_url")
                    ]
                )
                == 0
            )
        except DownloadError as e:
            self.__handle_error_exception(queue, e)
            return False

    def __handle_error_exception(self, queue: Queue[Message], e: DownloadError):
        real = e.exc_info[1]
        if isinstance(real, ExtractorError):
            error = Error(str(real.cause or real))
            self.__report_error(queue, error.message, real.video_id)
        else:
            error = Error(str(e))
            self.__report_error(queue, error.message, error.identity)
        Debug.print(str(e))

    def __report_error(
        self, queue: Queue[Message], message: str | None, target: str | None = None
    ):
        if self.__slot_index is not None:
            queue.put(
                ErrorMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    target=(self.__id or self.__name or "")
                    if target is None
                    else target,
                    message=message or "",
                )
            )
        self.__set_error(message)

    def __record_archive(self, processor: YoutubeDL, info: dict[str, object]):
        if processor.params.get("download_archive"):
            with Channel.__write_archive_lock:
                in_download_archive = cast(
                    Callable[[dict[str, object]], bool], processor.in_download_archive
                )
                if not in_download_archive(info):
                    record_download_archive = cast(
                        Callable[[dict[str, object]], None],
                        processor.record_download_archive,
                    )
                    record_download_archive(info)

    def __set_attempt_date(self):
        with Channel.__lock:
            self.__last_attempt_date = int(time())

    def __set_download_date(self):
        with Channel.__lock:
            self.__last_download_date = int(time())

    def __reset_epoch_cutoff(self):
        with Channel.__lock:
            self.__epoch_cutoff = 0
            self.__epoch_cutoff_passed = False

    def __set_error(self, message: str | None):
        with Channel.__lock:
            self.__last_error = message

    def __set_epoch_cutoff(self, epoch: int, timestamp: int):
        with Channel.__lock:
            if self.__epoch_cutoff == 0:
                self.__epoch_cutoff = timestamp - epoch
            return self.__epoch_cutoff

    def __within_cutoff(self, info: dict[str, object]) -> bool:
        timestamp: int = Item.get_int(info, "timestamp")
        if cutoff := cast(int, Config.settings["Download"]["playlist_cutoff"]):
            return not timestamp or (
                timestamp >= self.__set_epoch_cutoff(cutoff * 86_400, timestamp)
            )
        return True


class ChannelHelper:
    __header_columns: ClassVar = [
        "#",
        "URL",
        "Last Download",
        "Last Attempt",
        "Last Error Message",
    ]
    header_len: int = len(__header_columns)
    header: str = ";".join(__header_columns) + "\n"

    @staticmethod
    def load_channels(file_name: str) -> list[Channel]:
        channels: list[Channel] = []
        with open(file_name, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter=";"):
                if row and not row[0].lstrip().startswith("#"):
                    cells = [cell.strip() or None for cell in row]
                    if len(cells) == ChannelHelper.header_len:
                        channels.append(Channel(*cells, sub_level=0))
                    elif len(cells) > 0:
                        channels.append(Channel(cells[0], None, None, None, None))
        return channels

    @staticmethod
    def save_channels(channels: list[Channel], file_name: str):
        directory = os.path.dirname(os.path.abspath(file_name)) or "."
        rows = [channel.row() for channel in channels]
        try:
            fd, temp = tempfile.mkstemp(dir=directory, prefix=".channels-")
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
                writer.writerow(ChannelHelper.__header_columns)
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, file_name)
        except OSError as e:
            return e
        return None
