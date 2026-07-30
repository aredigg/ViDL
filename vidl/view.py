from enum import Enum
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep, time

from .message import Message, Msg, ProviderMessage, SleepMessage
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
            self.__top_name_count = None
            self.__top_name_last_dl = ""
            self.__item_id = ""
            self.__item_index = None
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

        def output(self, line):
            match line:
                case 0:
                    return (
                        f"{str(self.__index + 1):>2.2} "
                        + f"{self.__status_icon()} "
                        + f"{self.timer()} "
                        + f"{self.__top_name} "
                        + ""
                        if not self.__top_name_count
                        else f"({self.__top_name_count}) "
                        + f"{self.__top_name_last_dl}"
                    )
                case 1:
                    return (
                        f"{self.__item_id:>20.20} "
                        + f"{self.__item_date:>10.10} "
                        + "     "
                        if not self.__item_index
                        else f"{self.__item_index:>4.4} " + f"{self.__item_title}"
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
                            if self.__progress_time is not None
                            else ""
                            + f"{self.__progress_file_size}/{self.__progress_file_size_est} MB "
                            if self.__progress_file_size is not None
                            else "" + f"ETA {self.__progress_eta}"
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
                                if isinstance(message.body, ProviderMessage):
                                    self.__create_slot(message.body)
                            case Msg.SLEEP:
                                if isinstance(message.body, SleepMessage):
                                    self.__update_sleep(message.body)
                except Empty:
                    for index, slot in self.__slots.items():
                        terminal.print(slot.output(0), (index * 4) + 1, 1)
                        terminal.print(slot.output(1), (index * 4) + 2, 1)
                        terminal.print(slot.output(2), (index * 4) + 3, 1)
                        terminal.print(slot.output(3), (index * 4) + 4, 1)
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
