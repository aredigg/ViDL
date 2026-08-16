from dataclasses import dataclass
from time import time

from .config import Config


class Item:
    @dataclass
    class Entity:
        id: str
        index: int = 0
        playlist_index: int = 0
        date: int = 0
        title: str = ""
        availability: str = ""
        age_limit: int = 0
        stream: str = ""
        top_name: str = ""

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
    def get_entity(info, name, index) -> Entity:
        stream = "VIDEO"
        if info.get("is_live") or False:
            stream = "LIVE"
        elif info.get("live_status") in ("is_upcoming", "was_live", "post_live"):
            stream = "STREAM"
        return Item.Entity(
            id=info.get("id") or "",
            index=index,
            playlist_index=info.get("playlist_index") or 0,
            date=info.get("timestamp") or info.get("epoch") or 0,
            title=info.get("title") or "",
            availability=info.get("availability") or "",
            age_limit=info.get("age_limit") or 0,
            stream=stream,
            top_name=name,
        )

    @staticmethod
    def get_progress(data) -> Progress:
        elapsed = data.get("elapsed") or 0.0
        downloaded_bytes = data.get("downloaded_bytes") or 0
        total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        bitrate = 0
        if elapsed and downloaded_bytes:
            bitrate = int((downloaded_bytes << 3) / elapsed) >> 10
        remaining_bits = int(total_bytes - downloaded_bytes) >> 7
        remaining = int(remaining_bits / bitrate) if bitrate > 0 else 0
        extension = "----"
        parts = (data.get("filename") or "").upper().split(".")
        if len(parts) > 0:
            extension = parts[-1]

        return Item.Progress(
            time_current=elapsed,
            time_total=elapsed + remaining,
            size_current=downloaded_bytes,
            size_total=total_bytes,
            fragment_current=data.get("fragment_index") or 0,
            fragment_total=data.get("fragment_count") or 0,
            bitrate=bitrate,
            eta=data.get("eta") or 0,
            percent=data.get("_percent") or 0.0,
            process=data.get("postprocessor") or "Progress",
            status=data.get("status") or "",
            extension=extension,
        )

    @staticmethod
    def get_media(info) -> Media:
        width = info.get("width") or 0
        height = info.get("height") or 0
        video_stat = audio_stat = subtitle_stat = ""
        if width and height:
            video_stat = f"{width}x{height}@{int(info.get('fps') or 0)} {info.get('dynamic_range') or 'SDR'} {(info.get('vcodec') or '----')[:4].upper()}"
        asr = info.get("asr") or 0
        audio_channels = info.get("audio_channels") or 0
        if asr and audio_channels:
            audio_stat = (
                f"{asr}x{audio_channels} {(info.get('acodec') or '----')[:4].upper()}"
            )
        requested_subtitles = info.get("requested_subtitles") or {}
        subtitles = [lang.split("-")[0] for lang in requested_subtitles.keys()]
        subtitle_stat = "/".join(subtitles)
        return Item.Media(
            length=info.get("duration") or 0,
            extension=(info.get("ext") or "---"),
            video_stat=video_stat,
            audio_stat=audio_stat,
            subtitle_stat=subtitle_stat,
        )

    @staticmethod
    def no_vertical(info):
        width = info.get("width") or 0
        height = info.get("height") or 0
        return not Config.settings["Download"]["allow_vertical"] and height > width

    @staticmethod
    def high_resolution(info):
        height = info.get("height") or 0
        if minimum_resolution := Config.settings["Download"]["minimum_resolution"]:
            return height == 0 or height >= minimum_resolution
        return True

    @staticmethod
    def within_cutoff(info, channel):
        timestamp = info.get("timestamp") or 0
        if cutoff := Config.settings["Download"]["playlist_cutoff"]:
            return not timestamp or (
                timestamp >= channel.set_epoch_cutoff(cutoff * 86_400, timestamp)
            )
        return True

    @staticmethod
    def outside_deferred(info, format):
        height = format.get("height") or 0
        timestamp = info.get("timestamp") or 0
        if defer := Config.settings["Download"]["resolution_defer"]:
            return timestamp < (int(time()) - defer * 86_400) or height > 2000
        return True

    @staticmethod
    def requested_format(info):
        requested = info.get("requested_formats") or info.get("requested_downloads")
        if requested:
            video = next(
                (f for f in requested if f.get("vcodec") or "none" != "none"),
                requested[0],
            )
            audio = next(
                (f for f in requested if f.get("acodec") or "none" != "none"), {}
            )
            merged = dict(video)
            for key in ("asr", "audio_channels", "acodec"):
                if not merged.get(key) or (merged.get(key) or "none") == "none":
                    merged[key] = audio.get(key)
            return merged
        return Item.enumerate_best_format(info)

    @staticmethod
    def enumerate_best_format(info, extension="mp4"):
        formats = info.get("formats") or []

        def key(format):
            dynamic_range = (format.get("dynamic_range") or "SDR").upper()
            return (
                format.get("height") or 0,
                format.get("fps") or 0,
                dynamic_range != "SDR",
                format.get("audio_channels") or 0,
                format.get("asr") or 0,
            )

        matching = [
            format
            for format in formats
            if (format.get("ext") or "").lower() == extension.lower()
        ]
        return max(matching, key=key, default={})
