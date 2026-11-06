from collections.abc import Callable

from .debug import Debug
from .error import Error


class Logger:
    def __init__(
        self,
        slot_index: int,
        report_error: Callable[[str], None],
        on_cookie_rotation: Callable[[], None] | None = None,
    ) -> None:
        self.__slot_index = slot_index
        self.__report_error = report_error
        self.__on_cookie_rotation = on_cookie_rotation

    def debug(self, message: str):
        self.__print("DBG", message, message)

    def info(self, message: str):
        self.__print("INF", Error.strip_level(message), message)

    def warning(self, message: str):
        if self.__on_cookie_rotation is not None and Error.is_cookie_rotation(message):
            self.__on_cookie_rotation()
        self.__print("WRN", Error.strip_level(message), message)

    def error(self, message: str):
        message = Error.strip_level(message)
        self.__report_error(message)
        self.__print("ERR", message, message)

    def __print(self, level: str, message: str, fallback: str):
        # Only the first tag is the provider, any further tags stay in the line
        provider, provider_message = Error.split_provider(message, all_tags=False)
        if provider is not None:
            Debug.print(
                self.__slot_index, f"{level} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(self.__slot_index, f"{level} --> {fallback}")
