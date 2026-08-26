from datetime import datetime, timezone


class Util:
    date_fmt: str = "%Y-%m-%d"
    time_fmt: str = "%H:%M"

    @staticmethod
    def format_seconds(
        seconds: int, include_seconds: bool = True, two_parts: bool = True
    ):
        if seconds < 0:
            seconds = -seconds
        hr = int(seconds / 3600)
        mn = int((seconds / 60) % 60)
        sc = int(seconds % 60)
        if include_seconds:
            if two_parts and hr == 0:
                return f"{mn:02}′{sc:02}″"
            elif not two_parts:
                return f"{hr:02}:{mn:02}:{sc:02}"
        if sc > 0:
            if mn < 59:
                mn += 1
            else:
                hr += 1
                mn = 0
        return f"{hr:02}:{mn:02}′"

    @staticmethod
    def get_date(epoch: int | None = None):
        if epoch == 0:
            return ""
        if epoch is None:
            return datetime.strftime(
                datetime.now(tz=timezone.utc).astimezone(), Util.date_fmt
            )
        return datetime.strftime(
            datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(), Util.date_fmt
        )

    @staticmethod
    def get_time(epoch: int | None = None):
        if epoch == 0:
            return ""
        if epoch is None:
            return datetime.strftime(
                datetime.now(tz=timezone.utc).astimezone(), Util.time_fmt
            )
        return datetime.strftime(
            datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(), Util.time_fmt
        )
