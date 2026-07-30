from vidl.item import Item
from vidl.message import ChannelMessage, Message, Msg


class DownloadCancelled(Exception):
    pass


class Hook:
    def __init__(self, view_queue, halt_event, slot_index) -> None:
        self.__view_queue = view_queue
        self.__halt_event = halt_event
        self.__slot_index = slot_index

    def common(self, data):
        self.__view_queue.put(
            Message(
                kind=Msg.INIT,
                body=ChannelMessage(
                    index=self.__slot_index,
                    provider="channel",
                    channel=Item(
                        *Item.get_details(data.get("info_dict", {}))
                        + Item.get_format(data.get("info_dict", {}))
                        + Item.get_status(data or {})
                    ),
                ),
            )
        )

        # self.__temp_writer(f"HKK {self.__slot_index} --> Hook")
        if self.__halt_event.is_set():
            raise DownloadCancelled("Download cancelled due to halt")

    def __temp_writer(self, message):
        # print(message)
        with open(f"temp_hook_{self.__slot_index}.log", "a") as f:
            f.write(f"{message}\n")
