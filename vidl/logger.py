import re

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

    def __init__(self, view_queue, slot_index) -> None:
        self.__view_queue = view_queue
        self.__slot_index = slot_index

    def debug(self, message):
        provider, provider_message = self.__parse(message)
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            self.__temp_writer(
                f"DBG {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__temp_writer(f"DBG {self.__slot_index} --> {message}")

    def info(self, message):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            self.__temp_writer(
                f"INF {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__temp_writer(f"INF {self.__slot_index} --> {message}")

    def warning(self, message):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            self.__parse_to_view(provider, provider_message)
            self.__temp_writer(
                f"WRN {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            self.__temp_writer(f"WRN {self.__slot_index} --> {message}")

    def error(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        message = message.removeprefix("ERROR: ")
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
            self.__temp_writer(
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
            self.__temp_writer(f"ERR {self.__slot_index} --> {message}")

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

    def __temp_writer(self, message):
        pass
        # print(message)
        # with open(f"temp_debug_{self.__slot_index}.log", "a", encoding="utf-8") as f:
        #    f.write(f"{message}\n")
