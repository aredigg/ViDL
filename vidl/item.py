from dataclasses import dataclass

from .config import Config


@dataclass
class Item:
    id: str
    title: str
    thumbnail: str
    is_live: bool
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
    epoch: int
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
    speed: float
    _percent: float

    @staticmethod
    def get_status(data):
        return (
            data.get("status") or "",
            data.get("postprocessor") or "progress",
            data.get("filename") or "",
            data.get("elapsed") or 0.0,
            data.get("downloaded_bytes") or 0,
            data.get("total_bytes") or 0,
            data.get("speed") or 0.0,
            data.get("_percent") or 0.0,
        )

    @staticmethod
    def get_details(info):
        return (
            info.get("id") or "",
            info.get("title") or "",
            info.get("thumbnail") or "",
            info.get("is_live") or False,
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
            info.get("epoch") or 0,
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
        return Item.get_format(max(matching, key=key, default=None))

    @staticmethod
    def valid_format(item):
        if minimum_resolution := Config.settings["Downloads"]["minimum_resolution"]:
            return item.height == 0 or item.height >= minimum_resolution
        return True

    @staticmethod
    def get_item(info):
        return Item(
            *(
                Item.get_details(info or {})
                + Item.enumerate_best_format(info.get("formats") or {})
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
