import csv
import os
import tempfile
from datetime import datetime
from threading import RLock
from time import time

from yt_dlp.YoutubeDL import YoutubeDL

from .config import Config
from .item import Item
from .message import (
    CountMessage,
    EntityMessage,
    ErrorMessage,
    MediaMessage,
    Message,
    SleepMessage,
)


class Channel:
    __lock = RLock()
    __header_columns = [
        "#",
        "URL",
        "Last Download",
        "Last Attempt",
        "Last Error Message",
    ]
    header_len = len(__header_columns)
    header = ";".join(__header_columns) + "\n"

    @staticmethod
    def load_channels(file_name):
        channels = []
        with open(file_name, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter=";"):
                if row and not row[0].lstrip().startswith("#"):
                    cells = [cell.strip() or None for cell in row]
                    if len(cells) == Channel.header_len:
                        channels.append(Channel(*cells, sub_level=0))
                    elif len(cells) > 0:
                        channels.append(Channel(cells[0], None, None, None, None))
        return channels

    @staticmethod
    def save_channels(channels, file_name):
        directory = os.path.dirname(os.path.abspath(file_name)) or "."
        rows = [channel.__row() for channel in channels]
        try:
            fd, temp = tempfile.mkstemp(dir=directory, prefix=".channels-")
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
                writer.writerow(Channel.__header_columns)
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, file_name)
        except OSError as e:
            return e
        return None

    def __init__(
        self, name, url, last_dl_dte, last_at_dte, last_error, sub_level=0
    ) -> None:
        self.__name = name
        self.__url = url
        try:
            self.__last_download_date = int(last_dl_dte)
            self.__last_attempt_date = int(last_at_dte)
        except (ValueError, TypeError):
            self.__last_download_date = 0
            self.__last_attempt_date = 0
        self.__last_error = last_error
        self.__sub_level = sub_level
        self.__active = False
        self.__slot_index = None
        self.__epoch_cutoff = None
        self.__halt_event = None

    def __row(self):
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
            self.__last_download_date or self.__last_attempt_date or 0
        )
        return ret

    def get_last_error(self):
        return self.__last_error

    def get_name(self):
        return self.__name

    def set_epoch_cutoff(self, epoch):
        if self.__epoch_cutoff is None:
            self.__epoch_cutoff = int(time()) - epoch
        return self.__epoch_cutoff

    def set_halt_event(self, halt_event):
        self.__halt_event = halt_event

    def set_active(self):
        self.__reset_epoch_cutoff()
        self.__active = True

    def active(self):
        return self.__active

    def report_error(self, message):
        self.__set_error(message)

    # ----- Inside slot thread

    def download(self, slot, processor: YoutubeDL, queue, playlist_index=1):
        if self.__slot_index is not None:
            return False

        self.__slot_index = slot
        try:
            self.__set_attempt_date()
            result = self.__extract(processor, queue, playlist_index)
            if result:
                self.__set_download_date()
            return result
        finally:
            self.__slot_index = None
            self.__active = False

    def __extract(self, processor, queue, playlist_index):
        if self.__halt_event is not None and self.__halt_event.is_set():
            self.__report_error(queue, "Got halted")
            return False
        if self.__url is None or self.__url == "None":
            self.__url = self.__name
        if info := processor.extract_info(self.__url, download=False, process=False):
            self.__name = (
                info.get("title")
                or info.get("channel")
                or info.get("uploader")
                or f"{info.get('extractor')} ({info.get('id')})"
            )

            if self.__slot_index is not None:
                queue.put(
                    EntityMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.CHANNEL,
                        entity=Item.get_entity(
                            info=info,
                            name=self.__name if self.__sub_level == 0 else "",
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
                sub_channels = []
                if entries := list(info.get("entries") or []):
                    if self.__slot_index is not None:
                        queue.put(
                            CountMessage(
                                index=self.__slot_index,
                                provider=Message.Provider.CHANNEL,
                                value=len(entries),
                            )
                        )
                    for entry in entries:
                        if isinstance(entry, dict) and (
                            url := (entry.get("webpage_url") or entry.get("url"))
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
                    for playlist_index, channel in enumerate(sub_channels, start=1):
                        channel.set_active()
                        channel.set_halt_event(self.__halt_event)
                        channel.download(
                            self.__slot_index, processor, queue, playlist_index
                        )
                        if error := channel.get_last_error():
                            self.__report_error(queue, f"({error})")
            else:
                format = Item.enumerate_best_format(info)
                if Item.no_vertical(format):
                    self.__report_error(queue, "Vertical video")
                    return False
                if not Item.high_resolution(format):
                    self.__report_error(queue, "Low resolution")
                    return False
                if not Item.within_cutoff(info, self):
                    self.__report_error(queue, "Outside cutoff")
                    return False
                if not Item.outside_deferred(info, format):
                    self.__report_error(queue, "Defer low resolution")
                    return False
                process_time = int(time())
                ret = self.__download(processor, info)
                cutoff = Config.settings["Download"]["post_sleep_cutoff"] * 60
                sleep_time = min(cutoff, int(time()) - process_time)
                if self.__slot_index is not None:
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
            # if not self.__last_error:
            #    self.__report_error(queue, "Nothing to process")
            return False
        return True

    def __report_error(self, queue, message):
        if self.__slot_index is not None:
            queue.put(
                ErrorMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    target=self.__name,
                    message=message,
                )
            )
        self.__set_error(message)

    def __download(self, processor, info):
        return (
            processor.download([info.get("original_url") or info.get("webpage_url")])
            == 0
        )

    def __set_attempt_date(self):
        with Channel.__lock:
            self.__last_attempt_date = int(time())

    def __set_download_date(self):
        with Channel.__lock:
            self.__last_download_date = int(time())

    def __reset_epoch_cutoff(self):
        with Channel.__lock:
            self.__epoch_cutoff = None

    def __set_error(self, message):
        with Channel.__lock:
            self.__last_error = message
