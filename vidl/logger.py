import re
from collections.abc import Callable

from .debug import Debug


class Logger:
    BRACKET_PREFIX: re.Pattern[str] = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

    def __init__(self, slot_index: int, report_error: Callable[[str], None]) -> None:
        self.__slot_index = slot_index
        self.__report_error = report_error

    def debug(self, message: str):
        provider, provider_message = self.__parse(message)
        if provider is not None:
            Debug.print(
                self.__slot_index, f"DBG --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(self.__slot_index, f"DBG --> {message}")

    def info(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            Debug.print(
                self.__slot_index, f"INF --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(self.__slot_index, f"INF --> {message}")

    def warning(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            Debug.print(
                self.__slot_index, f"WRN --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(self.__slot_index, f"WRN --> {message}")

    def error(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        message = message.removeprefix("ERROR: ")
        self.__report_error(message)
        if provider is not None:
            Debug.print(
                self.__slot_index, f"ERR --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(self.__slot_index, f"ERR --> {message}")

    def __parse(self, message: str) -> tuple[str | None, str]:
        match = Logger.BRACKET_PREFIX.match(message)
        if match is None:
            return None, message
        return match.group(1), match.group(2)
