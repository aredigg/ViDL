import re

from vidl.debug import Debug

from .message import (
    ErrorMessage,
    FilePathMessage,
    InfoMessage,
    Message,
    Msg,
    SleepMessage,
    URLMessage,
    WarnMessage,
)


class Logger:
    BRACKET_PREFIX = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
    MESSAGE_SLEEP = re.compile(r"Sleeping\s+(\d+(?:\.\d+)?)\s+seconds\s+\.\.\.")
    MESSAGE_DOWNLOAD_PAGE = re.compile(
        r"([A-Za-z0-9_-]+)\s+page\s+(\d+): Downloading API JSON"
    )
    MESSAGE_RETRY_ERROR = re.compile(
        r"Got error: (\d+) bytes read, (\d+) more expected\. Retrying \((\d+)/(\d+)\)\.\.\."
    )

    def __init__(self, view_queue, slot_index, report_error) -> None:
        self.__view_queue = view_queue
        self.__slot_index = slot_index
        self.__report_error = report_error

    def debug(self, message):
        provider, provider_message = self.__parse(message)
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            Debug.print(
                f"DBG {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"DBG {self.__slot_index} --> {message}")

    def info(self, message):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            Debug.print(
                f"INF {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"INF {self.__slot_index} --> {message}")

    def warning(self, message):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            Debug.print(
                f"WRN {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"WRN {self.__slot_index} --> {message}")

    def error(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        message = message.removeprefix("ERROR: ")
        self.__report_error(message)
        if provider is not None:
            target, provider_message = self.__parse(provider_message)
            self.__view_queue.put(
                Message(
                    kind=Msg.ERROR,
                    body=ErrorMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=target,
                        message=provider_message,
                    ),
                )
            )
            Debug.print(
                f"ERR {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__view_queue.put(
                Message(
                    kind=Msg.ERROR,
                    body=ErrorMessage(
                        index=self.__slot_index,
                        provider="logger",
                        target=None,
                        message=message,
                    ),
                )
            )
            Debug.print(f"ERR {self.__slot_index} --> {message}")

    def __parse(self, message):
        match = Logger.BRACKET_PREFIX.match(message)
        if match is None:
            return None, message
        return match.group(1), match.group(2)

    def __parse_to_view(self, provider, message):
        if match := self.__parse_prefix("Extracting URL: ", message):
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=URLMessage(
                        index=self.__slot_index,
                        provider=provider,
                        url=match,
                    ),
                )
            )
            return

        if match := self.__parse_prefix("Destination: ", message):
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=FilePathMessage(
                        index=self.__slot_index,
                        provider=provider,
                        path=match,
                    ),
                )
            )
            return

        if match := self.__parse_suffix(
            ": has already been recorded in the archive", message
        ):
            self.__view_queue.put(
                Message(
                    kind=Msg.WARN,
                    body=WarnMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=match,
                        message="Already recorded",
                    ),
                )
            )
            return

        if match := self.__parse_suffix(": Downloading webpage", message):
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=InfoMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=match,
                        message="Downloading",
                    ),
                )
            )
            return

        if match := self.__parse_suffix(": Downloading JSON metadata", message):
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=InfoMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=match,
                        message="Metadata",
                    ),
                )
            )
            return

        match = Logger.MESSAGE_DOWNLOAD_PAGE.fullmatch(message)
        if match is not None:
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=InfoMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=match.group(1),
                        message="Metadata",
                    ),
                )
            )
            return

        match = Logger.MESSAGE_RETRY_ERROR.fullmatch(message)
        if match is not None:
            self.__view_queue.put(
                Message(
                    kind=Msg.INFO,
                    body=ErrorMessage(
                        index=self.__slot_index,
                        provider=provider,
                        target=f"Retry {int(match.group(3))}/{int(match.group(4))}",
                        message="Metadata",
                    ),
                )
            )
            return

        match = Logger.MESSAGE_SLEEP.fullmatch(message)
        if match is not None:
            try:
                self.__view_queue.put(
                    Message(
                        kind=Msg.SLEEP,
                        body=SleepMessage(
                            index=self.__slot_index,
                            provider=provider,
                            time_offset=int(float(match.group(1))),
                        ),
                    )
                )
            except ValueError:
                ...
            return

    def __parse_prefix(self, prefix, message):
        if message.startswith(prefix):
            return message.removeprefix(prefix).strip()
        return None

    def __parse_suffix(self, suffix, message: str):
        if message.endswith(suffix):
            return message.removesuffix(suffix).strip()
        return None
