from datetime import datetime
from random import randint
from time import sleep


class Channel:
    __header_columns = [
        "#",
        "URL",
        "Last Download",
        "Last Attempt",
        "Last Error Message",
    ]
    header_len = len(__header_columns)
    header = ";".join(__header_columns) + "\n"
    __date_fmt = "%Y-%m-%d"

    def __init__(self, name, url, last_dl_dte, last_at_dte, last_error) -> None:
        self.__name = name
        self.__url = url
        self.__last_download_date = last_dl_dte
        self.__last_attempt_date = last_at_dte
        self.__last_error = last_error
        self.__media_items = []
        self.__sub_channels = []
        self.__sub_level = 0
        self.__active = False
        self.__slot_index = None

    def get_last_date(self):
        ret = datetime.fromtimestamp(0)
        try:
            ret = datetime.strptime(self.__last_download_date, Channel.__date_fmt)
        except ValueError, TypeError:
            ...
        return ret

    def write(self):
        ret = (
            f"{self.__name};"
            + f"{self.__url};"
            + f"{self.__last_download_date};"
            + f"{self.__last_attempt_date};"
            + f"{self.__last_error}"
            + "\n"
        )
        if len(ret.split(";")) != Channel.header_len:
            return ""
        return ret

    def set_active(self):
        self.__active = True

    def active(self):
        return self.__active

    # ----- Inside slot thread

    def download(self, slot):
        if self.__slot_index is None:
            self.__slot_index = slot
            self.set_attempt_date()
            print(
                f"\033[3{slot + 1}mDownloading",
                self.__name,
                self.__last_attempt_date,
                self.__last_download_date,
                "\033[0m",
            )
            sleep(randint(1, 20) / 4)
            print(f"\033[3{slot + 1}mCompleted", self.__name, "\033[0m")
            self.set_download_date()
        else:
            print(
                f"\033[3{slot + 1}mActive\033[3{self.__slot_index + 1}m",
                self.__name,
                "\033[0m",
            )
            return
        self.__slot_index = None
        self.__active = False

    def set_attempt_date(self):
        self.__last_attempt_date = datetime.strftime(datetime.now(), Channel.__date_fmt)

    def set_download_date(self):
        self.__last_download_date = datetime.strftime(
            datetime.now(), Channel.__date_fmt
        )

    def set_error(self, message):
        self.__last_error = message
