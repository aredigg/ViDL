import re


class Logger:
    BRACKET_PREFIX = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

    def __init__(self, view_queue, slot_index) -> None:
        self.__view_queue = view_queue
        self.__slot_index = slot_index

    def debug(self, message):
        provider, provider_message = self.__parse(message)
        if provider is not None:
            self.__temp_writer(
                f"DBG {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__temp_writer(f"DBG {self.__slot_index} --> {message}")

    def info(self, message):
        self.__temp_writer(f"INF {self.__slot_index} --> {message}")

    def warning(self, message):
        self.__temp_writer(f"WRN {self.__slot_index} --> {message}")

    def error(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        message = message.removeprefix("ERROR: ")
        if provider is not None:
            self.__temp_writer(
                f"ERR {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__temp_writer(f"ERR {self.__slot_index} --> {message}")

    def __parse(self, message):
        match = Logger.BRACKET_PREFIX.match(message)
        if match is None:
            return None, message
        return match.group(1), match.group(2)

    def __temp_writer(self, message):
        print(message)
        # with open(f"temp_debug_{self.__slot_index}.log", "a") as f:
        #    f.write(f"{message}\n")
