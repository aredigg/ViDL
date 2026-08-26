import re

from .debug import Debug


class Logger:
    BRACKET_PREFIX: re.Pattern[str] = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
    MESSAGE_SLEEP: re.Pattern[str] = re.compile(
        r"Sleeping\s+(\d+(?:\.\d+)?)\s+seconds \.\.\."
    )
    MESSAGE_SLEEP_ADS: re.Pattern[str] = re.compile(
        r"Sleeping\s+(\d+(?:\.\d+)?)\s+seconds as required by the site\.\.\."
    )
    MESSAGE_DOWNLOAD_PAGE: re.Pattern[str] = re.compile(
        r"([A-Za-z0-9_-]+)\s+page\s+(\d+): Downloading API JSON"
    )
    MESSAGE_RETRY_ERROR: re.Pattern[str] = re.compile(
        r"Got error: (\d+) bytes read, (\d+) more expected\. Retrying \((\d+)/(\d+)\)\.\.\."
    )
    MESSAGE_MEMBER_LEVEL: re.Pattern[str] = re.compile(
        r"This video is available to this channel's members on level: (.+?) \(or any higher level\)\. Join this channel to get access to members-only content and other exclusive perks\."
    )

    def __init__(self, slot_index: int, report_error) -> None:
        # self.__view_queue = view_queue
        self.__slot_index = slot_index
        self.__report_error = report_error

    def debug(self, message: str):
        provider, provider_message = self.__parse(message)
        if provider is not None:
            Debug.print(
                f"DBG {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"DBG {self.__slot_index} --> {message}")

    def info(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
            Debug.print(
                f"INF {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"INF {self.__slot_index} --> {message}")

    def warning(self, message: str):
        provider, provider_message = self.__parse(message.removeprefix("ERROR: "))
        if provider is not None:
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
            Debug.print(
                f"ERR {self.__slot_index} --> {provider:>20.20} | {provider_message}"
            )
        else:
            Debug.print(f"ERR {self.__slot_index} --> {message}")

    def __parse(self, message):
        match = Logger.BRACKET_PREFIX.match(message)
        if match is None:
            return None, message
        return match.group(1), match.group(2)

    # def __parse_to_view_old(self, provider, message) -> bool:
    #     provider_message = provider
    #     if provider == "download":
    #         provider = Message.Provider.DOWNLOAD

    #     if match := self.__parse_prefix("Extracting URL: ", message):
    #         self.__view_queue.put(
    #             UrlMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 url=match,
    #             )
    #         )
    #         return True

    #     if match := self.__parse_prefix("Destination: ", message):
    #         self.__view_queue.put(
    #             PathMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 path=match,
    #             )
    #         )
    #         return True

    #     if match := self.__parse_suffix(
    #         ": has already been recorded in the archive", message
    #     ):
    #         self.__view_queue.put(
    #             WarningMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match,
    #                 message="Already recorded",
    #             )
    #         )
    #         return True

    #     if match := self.__parse_suffix(": Downloading webpage", message):
    #         self.__view_queue.put(
    #             InfoMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match,
    #                 message=provider_message,
    #             )
    #         )
    #         return True

    #     if match := self.__parse_suffix(": Downloading JSON metadata", message):
    #         self.__view_queue.put(
    #             InfoMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match,
    #                 message=provider_message,
    #             )
    #         )
    #         return True

    #     if match := self.__parse_suffix(
    #         ": Join this channel to get access to members-only content like this video, and other exclusive perks.",
    #         message,
    #     ):
    #         self.__view_queue.put(
    #             ErrorMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match,
    #                 message="Join channel",
    #             )
    #         )
    #         return True

    #     match = Logger.MESSAGE_DOWNLOAD_PAGE.fullmatch(message)
    #     if match is not None:
    #         self.__view_queue.put(
    #             InfoMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match.group(1),
    #                 message=provider_message,
    #             )
    #         )
    #         return True

    #     match = Logger.MESSAGE_RETRY_ERROR.fullmatch(message)
    #     if match is not None:
    #         self.__view_queue.put(
    #             InfoMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=f"Retry {int(match.group(3))}/{int(match.group(4))}",
    #                 message=provider_message,
    #             )
    #         )
    #         return True

    #     match = Logger.MESSAGE_MEMBER_LEVEL.fullmatch(message)
    #     if match is not None:
    #         self.__view_queue.put(
    #             ErrorMessage(
    #                 index=self.__slot_index,
    #                 provider=provider,
    #                 target=match.group(1),
    #                 message="Member level",
    #             )
    #         )
    #         return True

    #     match = Logger.MESSAGE_SLEEP.fullmatch(message)
    #     if match is not None:
    #         try:
    #             self.__view_queue.put(
    #                 SleepMessage(
    #                     index=self.__slot_index,
    #                     provider=provider,
    #                     sleep_time=int(float(match.group(1))),
    #                 )
    #             )
    #         except ValueError:
    #             ...
    #         return True

    #     match = Logger.MESSAGE_SLEEP_ADS.fullmatch(message)
    #     if match is not None:
    #         try:
    #             self.__view_queue.put(
    #                 SleepMessage(
    #                     index=self.__slot_index,
    #                     provider=provider,
    #                     sleep_time=int(float(match.group(1))),
    #                     required=True,
    #                 )
    #             )
    #         except ValueError:
    #             ...
    #         return True
    #     return False

    # def __parse_prefix(self, prefix, message):
    #     if message.startswith(prefix):
    #         return message.removeprefix(prefix).strip()
    #     return None

    # def __parse_suffix(self, suffix, message: str):
    #     if message.endswith(suffix):
    #         return message.removesuffix(suffix).strip()
    #     return None
