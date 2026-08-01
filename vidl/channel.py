from datetime import datetime
from time import sleep, time

from .item import Item
from .message import ItemMessage, Message, Msg, PlaylistCountMessage, SleepMessage
from .util import Util


class Channel:
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
        with open(file_name) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("#"):
                    if line.count(";") > 0:
                        parameters = line.split(";")
                        if len(parameters) == Channel.header_len:
                            channels.append(Channel(*parameters, sub_level=0))
                    else:
                        channels.append(Channel(line, None, None, None, None))
        return channels

    @staticmethod
    def save_channels(channels, file_name):
        write_buffer = Channel.header
        for channel in channels:
            write_buffer += channel.write()
        if write_buffer != Channel.header:
            try:
                with open(file_name, "w") as f:
                    f.write(write_buffer)
            except FileNotFoundError as e:
                return e
        else:
            return "Incorrect channel specification at write"

    def __init__(
        self, name, url, last_dl_dte, last_at_dte, last_error, sub_level=0
    ) -> None:
        self.__name = name
        self.__url = url
        try:
            self.__last_download_date = int(last_dl_dte)
            self.__last_attempt_date = int(last_at_dte)
        except ValueError, TypeError:
            self.__last_download_date = 0
            self.__last_attempt_date = 0
        self.__last_error = last_error
        self.__sub_channels = []
        self.__sub_level = sub_level
        self.__item = None
        self.__active = False
        self.__slot_index = None
        self.__epoch_cutoff = None
        self.__halt_event = None

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

    def write(self):
        ret = (
            f"{self.__name};"
            + f"{self.__url};"
            + f"{self.__last_download_date};"
            + f"{self.__last_attempt_date};"
            + f"{self.__last_error}"
            + "\n"
        )
        if len(ret.split(";")) != Channel.header_len:
            return ""
        return ret

    def set_active(self):
        self.__reset_epoch_cutoff()
        self.__active = True

    def active(self):
        return self.__active

    # ----- Inside slot thread

    def download(self, slot, processor, queue, playlist_index=1):
        if self.__slot_index is None:
            self.__slot_index = slot
            self.__set_attempt_date()
            if self.__extract(processor, queue, playlist_index):
                self.__set_download_date()
        else:
            # Should not happen
            return
        self.__slot_index = None
        self.__active = False

    def __extract(self, processor, queue, playlist_index):
        if self.__halt_event is not None and self.__halt_event.is_set():
            self.__set_error("Got halted")
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
                    Message(
                        kind=Msg.INIT,
                        body=ItemMessage(
                            index=self.__slot_index,
                            provider="channel",
                            item=Item.get_item(info),
                            name=self.__name if self.__sub_level == 0 else None,
                            last_date=Util.get_date(self.__last_download_date),
                            playlist_index=playlist_index,
                        ),
                    )
                )
            if info.get("_type") == "playlist":
                if entries := list(info.get("entries")):
                    if self.__slot_index is not None:
                        queue.put(
                            Message(
                                kind=Msg.INIT,
                                body=PlaylistCountMessage(
                                    index=self.__slot_index,
                                    provider="channel",
                                    playlist_count=len(entries),
                                ),
                            )
                        )
                    for entry in entries:
                        url = entry.get("webpage_url") or entry.get("url")
                        self.__sub_channels.append(
                            Channel(
                                url,
                                None,
                                None,
                                None,
                                None,
                                sub_level=self.__sub_level + 1,
                            )
                        )
                if self.__sub_channels:
                    for playlist_index, channel in enumerate(
                        self.__sub_channels, start=1
                    ):
                        channel.set_active()
                        channel.set_halt_event(self.__halt_event)
                        channel.download(
                            self.__slot_index, processor, queue, playlist_index
                        )
                        self.__set_error(f"({channel.get_last_error()})")
            else:
                self.__item = Item.get_item(info)
                if self.__item.valid_format() and self.__item.within_cutoff(self):
                    process_time = int(time())
                    ret = self.__download(processor)
                    sleep_time = min(3600, int(time()) - process_time)
                    if self.__slot_index is not None:
                        queue.put(
                            Message(
                                kind=Msg.SLEEP,
                                body=SleepMessage(
                                    index=self.__slot_index,
                                    provider="channel",
                                    time_offset=sleep_time,
                                ),
                            )
                        )
                        for _ in range(sleep_time >> 3):
                            if (
                                self.__halt_event is not None
                                and not self.__halt_event.is_set()
                            ):
                                sleep(8)
                    return ret
                else:
                    self.__set_error("No formats or outside cutoff")
                    return False
        else:
            self.__set_error("Nothing to process")
            return False
        return True

    def __download(self, processor):
        if item := self.__item:
            return processor.download(item.original_url)

    def __set_attempt_date(self):
        self.__last_attempt_date = int(time())

    def __set_download_date(self):
        self.__last_download_date = int(time())

    def __reset_epoch_cutoff(self):
        self.__epoch_cutoff = None

    def __set_error(self, message):
        self.__last_error = message
