from enum import Enum

from .item import Item
from .message import MediaMessage, Message, ProgressMessage


class DownloadCancelled(Exception):
    pass


class Hook:
    class State(Enum):
        PROGRESS = "Progress"
        MERGE = "Merger"
        MOVE = "MoveFiles"
        NORMALIZE = "FixupM3u8"

        @staticmethod
        def process(state: str) -> str:
            processes = {
                Hook.State.PROGRESS: "Progress",
                Hook.State.MERGE: "Merge",
                Hook.State.MOVE: "Move",
                Hook.State.NORMALIZE: "Normalize",
            }
            return processes.get(Hook.State(state), state)

    class Status(Enum):
        DOWNLOADING = "downloading"
        STARTED = "started"
        FINISHED = "finished"

    def __init__(self, view_queue, halt_event, slot_index) -> None:
        self.__view_queue = view_queue
        self.__halt_event = halt_event
        self.__slot_index = slot_index

    def common(self, data):
        self.__view_queue.put(
            ProgressMessage(
                index=self.__slot_index,
                provider=Message.Provider.HOOK,
                progress=Item.get_progress(data=data),
            )
        )
        self.__view_queue.put(
            MediaMessage(
                index=self.__slot_index,
                provider=Message.Provider.HOOK,
                media=Item.get_media(info=data.get("info_dict", {})),
            )
        )

        if self.__halt_event.is_set():
            raise DownloadCancelled("Download cancelled due to halt")
