from datetime import datetime


class Channel:
    __header_columns = [
        "#",
        "URL",
        "Last Download",
        "Last Attempt",
        "Last Error Message"
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

    def get_last_date(self):
        try:
            return datetime.strptime(self.__last_download_date, Channel.__date_fmt)
        except ValueError:
            ...
        return datetime.fromtimestamp(0)
