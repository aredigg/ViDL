class Logger:
    def __init__(self, view_queue, slot_index) -> None:
        self.__view_queue = view_queue
        self.__slot_index = slot_index

    def debug(self, message):
        print(message)

    def info(self, message):
        print(message)

    def warning(self, message):
        print(message)

    def error(self, message):
        print(message)
