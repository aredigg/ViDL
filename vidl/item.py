from dataclasses import dataclass
from time import time

from .config import Config
from .hook import Hook


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
        time_current: float
        time_total: float
        size_current: float
        size_total: float
        fragment_current: int
        fragment_total: int
        bitrate: float
        eta: int
        percent: float
        process: str
        status: str
        extension: str

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
        if info.get("is_live", False):
            stream = "LIVE"
        elif info.get("live_status", "") in ("is_upcoming", "was_live", "post_live"):
            stream = "STREAM"
        return Item.Entity(
            id=info.get("id", ""),
            index=index,
            playlist_index=info.get("playlist_index", 0),
            date=info.get("timestamp") or info.get("epoch", 0),
            title=info.get("title", ""),
            availability=info.get("availability", ""),
            age_limit=info.get("age_limit", 0),
            stream=f"{stream:<6.6}",
            top_name=name,
        )

    @staticmethod
    def get_progress(data) -> Progress:
        elapsed = data.get("elapsed", 0.0)
        downloaded_bytes = data.get("downloaded_bytes", 0)
        total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate", 0)
        bitrate = 0
        if elapsed and downloaded_bytes:
            bitrate = int((downloaded_bytes << 3) / elapsed) >> 10
        remaining_bits = int(total_bytes - downloaded_bytes) >> 7
        remaining = int(remaining_bits / bitrate) if bitrate > 0 else 0
        extension = "----"
        parts = data.get("filename", "").upper().split(".")
        if len(parts) > 0:
            extension = parts[-1]

        return Item.Progress(
            time_current=elapsed,
            time_total=remaining,
            size_current=downloaded_bytes,
            size_total=total_bytes,
            fragment_current=data.get("fragment_index", 0),
            fragment_total=data.get("fragment_count", 0),
            bitrate=bitrate,
            eta=data.get("eta", 0),
            percent=data.get("_percent", 0.0),
            process=data.get("postprocessor", Hook.State.PROGRESS),
            status=data.get("status", ""),
            extension=f"{extension:<4.4}",
        )

    @staticmethod
    def get_media(info) -> Media:
        width = info.get("width", 0)
        height = info.get("height", 0)
        video_stat = audio_stat = subtitle_stat = ""
        if width and height:
            video_stat = f"{str(width):>4.4}x{str(height):<4.4}@{str(int(info.get('fps', 0))):<3.3} {info.get('dynamic_range', 'SDR'):<6.6} {(info.get('vcodec', '----'))[:4].upper()}"
        asr = info.get("asr", 0)
        audio_channels = info.get("audio_channels", 0)
        if asr and audio_channels:
            audio_stat = f"{str(asr):>6.6}x{str(audio_channels):<2.2} {(info.get('acodec', '----'))[:4].upper()}"
        requested_subtitles = info.get("requested_subtitles") or {}
        subtitles = [lang for lang in requested_subtitles.keys()]
        subtitle_stat = "/".join(subtitles)
        return Item.Media(
            length=info.get("duration", 0),
            extension=info.get("ext", "---").upper(),
            video_stat=video_stat,
            audio_stat=audio_stat,
            subtitle_stat=subtitle_stat,
        )

    @staticmethod
    def no_vertical(info):
        width = info.get("width", 0)
        height = info.get("height", 0)
        return not Config.settings["Download"]["allow_vertical"] and height > width

    @staticmethod
    def high_resolution(info):
        height = info.get("height", 0)
        if minimum_resolution := Config.settings["Download"]["minimum_resolution"]:
            return height == 0 or height >= minimum_resolution
        return True

    @staticmethod
    def within_cutoff(info, channel):
        timestamp = info.get("timestamp", 0)
        if cutoff := Config.settings["Download"]["playlist_cutoff"]:
            return not timestamp or (
                timestamp >= channel.set_epoch_cutoff(cutoff * 86_400)
            )
        return True

    @staticmethod
    def outside_deferred(info, format):
        height = format.get("height", 0)
        timestamp = info.get("timestamp", 0)
        if defer := Config.settings["Download"]["resolution_defer"]:
            return timestamp < (int(time()) - defer * 86_400) or height > 2000
        return True

    @staticmethod
    def enumerate_best_format(info, extension="mp4"):
        formats = info.get("formats")

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
