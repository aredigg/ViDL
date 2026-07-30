from enum import Enum
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep, time

from .item import Item
from .message import (
    InitMessage,
    ItemMessage,
    Message,
    Msg,
    PlaylistCountMessage,
    SleepMessage,
)
from .terminal import Terminal
from .util import Util

# slot index, status_icon, timer, playlist_name (len)
# item_id, item_index, date, item_title
# video_length, video_stats, audio_stats, extension, file_size
# progress (time/time) (size/size)

# 01 X 00:00 Playlist Name (99) 2026-06-01
# abcdefghijkl  75 2026-07-01 Item name can be long
# 00:00:00 2000x1000 @60 SDR AVC1 44100x2 MP4A EN-US LIVE/STREAM 15
#  > XXXXXXXXXXXXXXX..... < 00:00/00:00 1200/3600 MB ETA 23:00
#  > ERROR: xxx


class View:
    index = 0

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
            self.__status = View.Status.INACTIVE
            self.__timer = None
            self.__top_name = ""
            self.__top_name_count = ""
            self.__top_name_last_dl = ""
            self.__item_id = ""
            self.__item_index = ""
            self.__item_date = ""
            self.__item_title = ""
            self.__media_length = ""
            self.__media_video_stats = ""
            self.__media_audio_stats = ""
            self.__media_subtitles = ""
            self.__media_extenstion = ""
            self.__media_live_state = ""
            self.__media_age_limit = ""
            self.__progress_time = None
            self.__progress_time_est = None
            self.__progress_file_size = None
            self.__progress_file_size_est = None
            self.__progress_eta = None
            self.__status_provider = ""
            self.__status_message = ""

        def set_timer(self, time_offset):
            self.__timer = int(time()) + time_offset

        def timer(self):
            if self.__timer is not None:
                return Util.format_seconds((time()) - self.__timer)
            return "--:--"

        def set_count(self, count):
            self.__top_name_count = f"({count})"

        def set_top(self, name, last_dl):
            if name is not None:
                self.__top_name = name
                self.__top_name_count = ""
                self.__top_name_last_dl = last_dl

        def set_item(self, id, index, item_date, title):
            self.__item_id = id
            self.__item_index = str(index)
            self.__item_date = Util.get_time(item_date)
            self.__item_title = title

        def set_media(self, length, video, audio, subtitle, extension, live, age):
            self.__media_length = Util.format_seconds(length, two_parts=False)
            self.__media_video_stats = video
            self.__media_audio_stats = audio
            self.__media_subtitles = subtitle
            self.__media_extenstion = extension
            self.__media_live_state = live
            self.__media_age_limit = age

        def set_progress(self, time_curr, time_est, size_curr, size_est, eta):
            self.__progress_time = time_curr
            self.__progress_time_est = time_est
            self.__progress_file_size = size_curr
            self.__progress_file_size_est = size_est
            self.__progress_eta = eta

        def set_status(self, provider, message):
            self.__status_provider = provider
            self.__status_message = message

        def output(self, line):
            match line:
                case 0:
                    return (
                        f"{str(self.__index + 1):>2.2} "
                        + f"{self.__status_icon()} "
                        + f"{self.timer()} "
                        + f"{self.__top_name} "
                        + f"{self.__top_name_count} "
                        + f"{self.__top_name_last_dl}"
                    )
                case 1:
                    return (
                        f"{self.__item_id:>20.20} "
                        + f"{self.__item_date:>10.10} "
                        + f"{self.__item_index:>4.4} "
                        + f"{self.__item_title}"
                    )
                case 2:
                    return (
                        f"{self.__media_length} "
                        + f"{self.__media_video_stats} "
                        + f"{self.__media_audio_stats} "
                        + f"{self.__media_subtitles} "
                        + f"{self.__media_extenstion} "
                        + f"{self.__media_live_state} "
                        + f"{self.__media_age_limit} "
                    )
                case 3:
                    if self.__status == View.Status.DOWNLOADING:
                        return (
                            f"{self.__progress_meter()} "
                            + f"{self.__progress_time}/{self.__progress_time_est} "
                            + f"{self.__progress_file_size}/{self.__progress_file_size_est} MB "
                            + f"ETA {self.__progress_eta}"
                        )
                    else:
                        return f"{self.__status_provider} " + f"{self.__status_message}"

        def __status_icon(self):
            match self.__status:
                case View.Status.INACTIVE:
                    return "○"
                case View.Status.WAITING:
                    return "○"
                case View.Status.SLEEPING:
                    return "○"
                case View.Status.DOWNLOADING:
                    return "●"
                case View.Status.PROCESSING:
                    return "●"
                case View.Status.WARNING:
                    return "○"
                case View.Status.ERROR:
                    return "○"

        def __progress_meter(self):
            return ""

    def __init__(self) -> None:
        self.__ready = False
        self.__queue = Queue()
        self.__input_queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(target=self.__run, name=f"View-{View.index}")
        self.__thread.start()
        View.index += 1
        self.__slots = {}

    def get_queues(self):
        return self.__queue, self.__input_queue

    def ready(self):
        return self.__ready and self.__thread.is_alive()

    def __run(self):
        with Terminal(self.__input_queue) as terminal:
            self.__ready = True
            while not self.__halt_event.is_set():
                try:
                    message = self.__queue.get(block=False)
                    if message.kind != Msg.HALT:
                        match message.kind:
                            case Msg.INIT:
                                if isinstance(message.body, InitMessage):
                                    self.__create_slot(message.body)
                                elif isinstance(message.body, ItemMessage):
                                    self.__update_item(message.body)
                                elif isinstance(message.body, PlaylistCountMessage):
                                    self.__update_count(message.body)
                            case Msg.SLEEP:
                                if isinstance(message.body, SleepMessage):
                                    self.__update_sleep(message.body)
                except Empty:
                    for slot_index, slot in self.__slots.items():
                        terminal.print(slot.output(0), (slot_index * 4) + 1, 1)
                        terminal.print(slot.output(1), (slot_index * 4) + 2, 1)
                        terminal.print(slot.output(2), (slot_index * 4) + 3, 1)
                        terminal.print(slot.output(3), (slot_index * 4) + 4, 1)
                    sleep(0.5)

    def halt(self):
        self.__ready = False
        self.__queue.put(Message(kind=Msg.HALT, body=None))
        self.__halt_event.set()

    def join(self):
        self.__thread.join()

    def __create_slot(self, body):
        if body.provider == "slot":
            self.__slots[body.index] = View.Slot(body.index)

    def __update_sleep(self, body):
        if body.index in self.__slots:
            if body.provider == "download":
                self.__slots[body.index].set_timer(body.time_offset)

    def __update_item(self, body):
        if body.index in self.__slots:
            if body.provider == "channel":
                item: Item = body.item
                slot: View.Slot = self.__slots[body.index]
                slot.set_top(body.name, body.last_date)
                slot.set_item(item.id, body.playlist_index, item.timestamp, item.title)
                video_stat = f"{item.width:>4}x{item.height:<4}@{int(item.fps)}({item.dynamic_range.upper()})({item.vcodec.upper()})"
                audio_stat = f"{item.asr}x{item.audio_channels} {item.acodec.upper()}"
                subtitle_stat = ""
                live_stat = ""
                if item.is_live:
                    live_stat = "LIVE"
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
            elif body.provider == "hook":
                ...

    def __update_count(self, body):
        if body.index in self.__slots:
            if body.provider == "channel":
                self.__slots[body.index].set_count(body.playlist_count)
