from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from time import time
from typing import cast

from .config import Config


class Item:
    @dataclass
    class Entity:
        id: str
        index: int
        playlist_index: int
        date: int
        title: str
        availability: str
        age_limit: int
        stream: str
        top_name: str

    @dataclass
    class Progress:
        time_current: float = 0.0
        time_total: float = 0.0
        size_current: float = 0
        size_total: float = 0
        fragment_current: int = 0
        fragment_total: int = 0
        bitrate: float = 0
        eta: int = 0
        percent: float = 0
        process: str = ""
        status: str = ""
        extension: str = "---"

    @dataclass
    class Media:
        length: int
        extension: str
        video_stat: str
        audio_stat: str
        subtitle_stat: str

    @staticmethod
    def get_str(info: Mapping[str, object], key: str, ret: str = "") -> str:
        if info:
            value = info.get(key)
            if value and isinstance(value, str):
                return value
        return ret

    @staticmethod
    def get_int(info: Mapping[str, object], key: str, ret: int = 0) -> int:
        if info:
            value = info.get(key)
            if value and isinstance(value, int):
                return value
        return ret

    @staticmethod
    def get_flt(info: Mapping[str, object], key: str, ret: float = 0.0) -> float:
        if info:
            value = info.get(key)
            if value and isinstance(value, float):
                return value
        return ret

    @staticmethod
    def get_lst(info: Mapping[str, object], key: str) -> list[object]:
        ret: list[object] = []
        if info:
            value = info.get(key)
            if isinstance(value, list):
                return cast(list[object], value)
            elif isinstance(value, Iterable) and not isinstance(
                value, (str, bytes, Mapping)
            ):
                return list(value)
        return ret

    @staticmethod
    def get_dct(info: Mapping[str, object], key: str) -> dict[str, object]:
        res: dict[str, object] = {}
        if info:
            value = info.get(key)
            if value and isinstance(value, Mapping):
                value = cast(Mapping[object, object], value)
                for res_key, res_value in value.items():
                    if isinstance(res_key, str):
                        res[res_key] = res_value
        return res

    @staticmethod
    def get_entity(info: dict[str, object], name: str, index: int) -> Entity:
        stream = "VIDEO"
        if info.get("is_live") or False:
            stream = "LIVE"
        elif info.get("live_status") in ("is_upcoming", "was_live", "post_live"):
            stream = "STREAM"
        return Item.Entity(
            id=Item.get_str(info, "id"),
            index=index,
            playlist_index=Item.get_int(info, "playlist_index"),
            date=Item.get_int(info, "timestamp") or Item.get_int(info, "epoch"),
            title=Item.get_str(info, "title"),
            availability=Item.get_str(info, "availability"),
            age_limit=Item.get_int(info, "age_limit"),
            stream=stream,
            top_name=name,
        )

    @staticmethod
    def get_progress(data: dict[str, object]) -> Progress:
        elapsed = Item.get_flt(data, "elapsed")
        downloaded_bytes = Item.get_int(data, "downloaded_bytes")
        total_bytes = Item.get_int(data, "total_bytes") or Item.get_int(
            data, "total_bytes_estimate"
        )
        bitrate = 0
        if elapsed and downloaded_bytes:
            bitrate = int((downloaded_bytes << 3) / elapsed) >> 10
        remaining_bits = int(total_bytes - downloaded_bytes) >> 7
        remaining = int(remaining_bits / bitrate) if bitrate > 0 else 0
        extension = "----"
        parts = Item.get_str(data, "filename").upper().split(".")
        if len(parts) > 0:
            extension = parts[-1]

        return Item.Progress(
            time_current=elapsed,
            time_total=elapsed + remaining,
            size_current=downloaded_bytes,
            size_total=total_bytes,
            fragment_current=Item.get_int(data, "fragment_index"),
            fragment_total=Item.get_int(data, "fragment_count"),
            bitrate=bitrate,
            eta=Item.get_int(data, "eta"),
            percent=Item.get_flt(data, "_percent"),
            process=Item.get_str(data, "postprocessor", "Progress"),
            status=Item.get_str(data, "status"),
            extension=extension,
        )

    @staticmethod
    def get_media(info: dict[str, object]) -> Media:
        width = Item.get_int(info, "width")
        height = Item.get_int(info, "height")
        video_stat = audio_stat = subtitle_stat = ""
        if width and height:
            video_stat = f"{width}x{height}@{int(Item.get_flt(info, 'fps'))} {Item.get_str(info, 'dynamic_range', 'SDR')} {Item.get_str(info, 'vcodec', '----')[:4].upper()}"
        asr = info.get("asr") or 0
        audio_channels = info.get("audio_channels") or 0
        if asr and audio_channels:
            audio_stat = f"{asr}x{audio_channels} {Item.get_str(info, 'acodec', '----')[:4].upper()}"
        requested_subtitles: dict[str, object] = Item.get_dct(
            info, "requested_subtitles"
        )
        subtitles = [lang.split("-")[0] for lang in requested_subtitles]
        subtitle_stat = "/".join(subtitles)
        return Item.Media(
            length=Item.get_int(info, "duration"),
            extension=Item.get_str(info, "ext", "---"),
            video_stat=video_stat,
            audio_stat=audio_stat,
            subtitle_stat=subtitle_stat,
        )

    @staticmethod
    def no_vertical(info: dict[str, object]) -> bool:
        width = Item.get_int(info, "width")
        height = Item.get_int(info, "height")
        allow_vertical = bool(Config.settings["Download"]["allow_vertical"])
        return not allow_vertical and height > width

    @staticmethod
    def high_resolution(info: dict[str, object]) -> bool:
        height = Item.get_int(info, "height")
        if minimum_resolution := cast(
            int, Config.settings["Download"]["minimum_resolution"] or 0
        ):
            return height == 0 or height >= minimum_resolution
        return True

    @staticmethod
    def outside_deferred(info: dict[str, object], format: dict[str, object]):
        height = Item.get_int(format, "height")
        timestamp = Item.get_int(info, "timestamp")
        if defer := cast(int, Config.settings["Download"]["resolution_defer"] or 0):
            return timestamp < (int(time()) - defer * 86_400) or height > 2000
        return True

    @staticmethod
    def requested_format(info: dict[str, object]) -> dict[str, object]:
        requested = Item.get_lst(info, "requested_formats") or Item.get_lst(
            info, "requested_downloads"
        )
        if requested:
            video = next(
                (
                    f
                    for f in requested
                    if Item.get_str(cast(dict[str, object], f), "vcodec", "none")
                    != "none"
                ),
                requested[0],
            )
            audio = next(
                (
                    f
                    for f in requested
                    if Item.get_str(cast(dict[str, object], f), "acodec", "none")
                    != "none"
                ),
                cast(dict[str, object], {}),
            )
            merged = cast(dict[str, object], video)
            for key in ("asr", "audio_channels", "acodec"):
                if not merged.get(key) or (merged.get(key) or "none") == "none":
                    merged[key] = cast(dict[str, object], audio).get(key)
            return merged
        return Item.enumerate_best_format(info)

    @staticmethod
    def enumerate_best_format(
        info: dict[str, object], extension: str = "mp4"
    ) -> dict[str, object]:
        formats_pre: list[object] = Item.get_lst(info, "formats")
        formats: list[dict[str, object]] = []
        for format in formats_pre:
            formats.append(cast(dict[str, object], format))

        def key(format: dict[str, object]) -> tuple[int, int, bool, int, int]:
            dynamic_range = Item.get_str(format, "dynamic_range", "SDR").upper()
            return (
                Item.get_int(format, "height"),
                Item.get_int(format, "fps"),
                dynamic_range != "SDR",
                Item.get_int(format, "audio_channels"),
                Item.get_int(format, "asr"),
            )

        matching = [
            format
            for format in formats
            if Item.get_str(format, "ext").lower() == extension.lower()
        ]
        return max(matching, key=key, default=cast(dict[str, object], {}))
