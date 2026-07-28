class Hook:
    def __init__(self, view_queue, slot_index) -> None:
        self.__view_queue = view_queue
        self.__slot_index = slot_index

    def common(self, data):
        print(f"HKK {self.__slot_index} --> Hook")
