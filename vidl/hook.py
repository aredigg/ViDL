class DownloadCancelled(Exception):
    pass


class Hook:
    def __init__(self, view_queue, halt_event, slot_index) -> None:
        self.__view_queue = view_queue
        self.__halt_event = halt_event
        self.__slot_index = slot_index

    def common(self, data):
        print(f"HKK {self.__slot_index} --> Hook")
        if self.__halt_event.is_set():
            raise DownloadCancelled("Download cancelled due to halt")
