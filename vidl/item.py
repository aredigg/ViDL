from dataclasses import dataclass

from .config import Config


@dataclass
class Item:
    id: str
    title: str
    channel: str
    uploader: str
    thumbnail: str
    is_live: bool
    availability: str
    age_limit: int
    webpage_url: str
    original_url: str
    webpage_url_basename: str
    webpage_url_domain: str
    extractor: str
    extractor_key: str
    playlist: str
    playlist_index: int
    display_id: str
    fulltitle: str
    release_year: str
    live_status: str
    timestamp: int
    duration: int
    epoch: int
    _type: str
    _filename: str
    _real_download: bool
    _finaldir: str
    filepath: str
    _files_to_move: str
    width: int
    height: int
    fps: int
    asr: int
    audio_channels: int
    dynamic_range: str
    vcodec: str
    acodec: str
    ext: str
    format_id: str
    protocol: str
    tbr: int
    status: str
    processor: str
    filename: str
    elapsed: float
    downloaded_bytes: int
    total_bytes: int
    fragment_index: int
    fragment_count: int
    speed: float
    eta: float
    _percent: float

    def valid_format(self):
        if (
            not Config.settings["Download"]["allow_vertical"]
            and self.height > self.width
        ):
            return False
        if minimum_resolution := Config.settings["Download"]["minimum_resolution"]:
            return self.height == 0 or self.height >= minimum_resolution
        return True

    def within_cutoff(self, channel):
        if cutoff := Config.settings["Download"]["playlist_cutoff"]:
            return not self.timestamp or self.timestamp >= channel.set_epoch_cutoff(
                cutoff * 86_400
            )
        return True

    @staticmethod
    def get_status(data):
        return (
            data.get("status") or "",
            data.get("postprocessor") or "progress",
            data.get("filename") or "",
            data.get("elapsed") or 0.0,
            data.get("downloaded_bytes") or 0,
            data.get("total_bytes") or data.get("total_bytes_estimate") or 0,
            data.get("fragment_index") or 0,
            data.get("fragment_count") or 0,
            data.get("speed") or 0.0,
            data.get("eta") or 0,
            data.get("_percent") or 0.0,
        )

    @staticmethod
    def get_details(info):
        return (
            info.get("id") or "",
            info.get("title") or "",
            info.get("channel") or "",
            info.get("uploader") or "",
            info.get("thumbnail") or "",
            info.get("is_live") or False,
            info.get("availability") or "",
            info.get("age_limit") or 0,
            info.get("webpage_url") or "",
            info.get("original_url") or "",
            info.get("webpage_url_basename") or "",
            info.get("webpage_url_domain") or "",
            info.get("extractor") or "",
            info.get("extractor_key") or "",
            info.get("playlist") or "",
            info.get("playlist_index") or 0,
            info.get("display_id") or "",
            info.get("fulltitle") or "",
            info.get("release_year") or "",
            info.get("live_status") or "",
            info.get("timestamp") or 0,
            info.get("duration") or 0,
            info.get("epoch") or 0,
            info.get("_type") or "",
            info.get("_filename") or "",
            info.get("__real_download") or False,
            info.get("__finaldir") or "",
            info.get("filepath") or "",
            info.get("__files_to_move") or "",
        )

    @staticmethod
    def get_format(format):
        return (
            format.get("width") or 0,
            format.get("height") or 0,
            format.get("fps") or 0,
            format.get("asr") or 0,
            format.get("audio_channels") or 0,
            format.get("dynamic_range") or "SDR",
            (format.get("vcodec") or "----")[:4],
            (format.get("acodec") or "----")[:4],
            format.get("ext") or "---",
            format.get("format_id") or "",
            format.get("protocol") or "",
            format.get("tbr") or format.get("vbr") or 0,
        )

    @staticmethod
    def enumerate_best_format(formats, extension="mp4"):
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
        return Item.get_format(max(matching, key=key, default={}))

    @staticmethod
    def get_item(info):
        return Item(
            *(
                Item.get_details(info or {})
                + Item.enumerate_best_format(info.get("formats") or [])
                + Item.get_status({})
            )
        )

    @staticmethod
    def get_item_hooks(data):
        return Item(
            *(
                Item.get_details(data.get("info_dict") or {})
                + Item.get_format(data.get("info_dict") or {})
                + Item.get_status(data or {})
            )
        )
