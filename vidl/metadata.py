"""Episode metadata and channel cover art for new downloads.

A MetadataPostProcessor is registered with
processor.add_post_processor(pp, when="video") and fed a MetadataContext before
each video is downloaded. Facts relied upon (yt-dlp 2026.08.19):

- The "video" stage runs in YoutubeDL.process_info (pre_process(info, "video"))
  after _match_entry (archived/filtered videos never get here) and before
  _write_thumbnails, so a replaced thumbnail list is what gets written to disk.
  process_video_result has already run _sanitize_thumbnails at that point, so
  the dicts set here are used as is: "id" must be present (_write_thumbnails
  formats t["id"] directly), "url" must be absolute, "preference" is not needed.
- _write_thumbnails names the file t.get("ext") or determine_ext(url, "jpg").
  Avatar URLs (https://yt3.googleusercontent.com/...=s0) have no extension, so
  that is "jpg" anyway; "ext" is set explicitly so odd URLs (e.g. ".php") never
  name the file, and EmbedThumbnailPP checks the real content: a webp labelled
  .jpg is renamed by fixup_webp and converted to png (ffmpeg) before embedding.
- FFmpegMetadataPP maps series -> show and album (unless album is set),
  season_number -> season_number, episode (or episode_id) -> episode_id and
  episode_number -> episode_sort. In mp4 those are the tvsh, ©alb, tvsn, tven
  and tves atoms.
- PostProcessor.pp_key() strips the last two characters of the class name (and
  an "FFmpeg" prefix), which would give "MetadataPostProcess", while
  FFmpegMetadataPP already reports "Metadata", so the key is set explicitly.
- The PostProcessor metaclass wraps run() to send "started"/"finished" to the
  postprocessor hooks, also for the "video" stage. That happens before the
  download starts and would end the waiting state of the view (the following
  "downloading" progress no longer switches it to downloading), so no hooks
  are registered unless REPORT_PROGRESS is set. With it, hooks report
  postprocessor="EpisodeMetadata". Construct the PP without a downloader:
  add_post_processor calls set_downloader, which adds the YoutubeDL hooks
  again, so a PP built with YoutubeDL(...) as argument reports twice.
"""

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.embedthumbnail import (  # pyright: ignore[reportMissingTypeStubs]
    EmbedThumbnailPP as UntypedEmbedThumbnailPP,
)
from yt_dlp.postprocessor.ffmpeg import (  # pyright: ignore[reportMissingTypeStubs]
    FFmpegMetadataPP as UntypedFFmpegMetadataPP,
)
from yt_dlp.utils import determine_ext
from yt_dlp.YoutubeDL import YoutubeDL

# These modules have no type stubs
EmbedThumbnailPP = cast(type[PostProcessor], UntypedEmbedThumbnailPP)
FFmpegMetadataPP = cast(type[PostProcessor], UntypedFFmpegMetadataPP)

# The thumbnail formats yt-dlp knows (MEDIA_EXTENSIONS.thumbnails)
THUMBNAIL_EXTENSIONS: tuple[str, ...] = ("jpg", "png", "webp")

InfoDict = dict[str, Any]  # pyright: ignore[reportExplicitAny]


@dataclass(frozen=True)
class MetadataContext:
    series: str | None = None  # top level channel name
    episode_number: int | None = None  # 1 = oldest
    season_number: int | None = None  # optional, not set by default
    thumbnail_url: str | None = None  # channel graphic (http/https only)


def episode_number(playlist_count: int, playlist_index: int) -> int | None:
    """The episode number counted from the oldest entry, as entries are newest
    first, or None when the index is outside the playlist."""
    if not _is_int(playlist_count) or not _is_int(playlist_index):
        return None
    if 1 <= playlist_index <= playlist_count:
        return playlist_count - playlist_index + 1
    return None


def channel_thumbnail(info: Mapping[str, object]) -> str | None:
    """The URL of the channel avatar in an (unprocessed) channel info dict.

    The youtube:tab extractor (_extract_metadata_from_tabs) produces
    playlist thumbnails + avatars + banners. Avatars and banners come from
    _extract_thumbnails, which sets only url/width/height (no "id"); the
    avatar is usually one 900x900 "=s900-c-k-c0x00ffffff-no-rj" entry. When
    there is an avatar, an {"id": "avatar_uncropped", "preference": 1} entry
    with the original image ("=s0", no size) is appended; banners get
    preference -10 and an {"id": "banner_uncropped", "preference": -5} entry.
    With process=False the ids are not filled in by _sanitize_thumbnails, so
    only entries with an id naming an avatar are considered.
    """
    try:
        thumbnails = info.get("thumbnails")
        if not isinstance(thumbnails, list):
            return None
        best: tuple[tuple[int, int, float], str] | None = None
        for thumbnail in cast(list[object], thumbnails):
            if not isinstance(thumbnail, Mapping):
                continue
            thumbnail = cast(Mapping[str, object], thumbnail)
            thumbnail_id = thumbnail.get("id")
            if not isinstance(thumbnail_id, str):
                continue
            thumbnail_id = thumbnail_id.lower()
            if "banner" in thumbnail_id or "avatar" not in thumbnail_id:
                continue
            url = _http_url(thumbnail.get("url"))
            if url is None:
                continue
            key = (
                1 if thumbnail_id == "avatar_uncropped" else 0,
                _size(thumbnail.get("width")) * _size(thumbnail.get("height")),
                _preference(thumbnail.get("preference")),
            )
            if best is None or key > best[0]:
                best = (key, url)
        return best[1] if best is not None else None
    except Exception:  # noqa: BLE001 - odd data must not break a download
        return None


class MetadataPostProcessor(PostProcessor):
    KEY: ClassVar[str] = "EpisodeMetadata"
    REPORT_PROGRESS: ClassVar[bool] = False

    def __init__(self, downloader: YoutubeDL | None = None) -> None:
        self.__lock = Lock()
        self.__context: MetadataContext | None = None
        super().__init__(downloader)

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return cls.KEY

    def set_context(self, context: MetadataContext | None) -> None:
        with self.__lock:
            self.__context = context

    # The typeshed stubs declare run(information: _InfoDict), yt-dlp passes a
    # plain dict positionally
    def run(self, info: InfoDict) -> tuple[list[str], InfoDict]:  # pyright: ignore[reportImplicitOverride, reportIncompatibleMethodOverride]
        with self.__lock:
            context = self.__context
        if context is None:
            return [], info
        try:
            # Collected first, so a failure while reading leaves info unchanged
            info.update(self.__updates(info, context))
        except Exception as e:  # noqa: BLE001 - must never break a download
            self.__warn(f"Metadata not added, {type(e).__name__}: {e}")
        return [], info

    # Without hooks, _hook_progress returns early. Called by __init__ (for
    # report_progress), set_downloader and YoutubeDL.add_postprocessor_hook
    def add_progress_hook(self, ph: Callable[[str], object]) -> None:  # pyright: ignore[reportImplicitOverride]
        if self.REPORT_PROGRESS:
            super().add_progress_hook(ph)

    def __updates(
        self, info: Mapping[str, object], context: MetadataContext
    ) -> dict[str, object]:
        updates: dict[str, object] = {}
        if _is_int(context.episode_number):
            updates["episode_number"] = context.episode_number
            title = info.get("title")
            if not info.get("episode") and isinstance(title, str) and title:
                updates["episode"] = title
        if (
            isinstance(context.series, str)
            and context.series
            and not info.get("series")
        ):
            updates["series"] = context.series
        if _is_int(context.season_number):
            updates["season_number"] = context.season_number
        if url := _http_url(context.thumbnail_url):
            updates["thumbnails"] = [
                {"id": "channel", "url": url, "ext": _thumbnail_ext(url)}
            ]
            updates["thumbnail"] = url
        return updates

    def __warn(self, message: str) -> None:
        # A failing logger must not break a download either
        try:
            self.report_warning(f"[{self.pp_key()}] {message}")
        except Exception:  # noqa: BLE001, S110
            pass


# yt-dlp runs the post_process stage also when the file was already
# downloaded (process_info sets __real_download to False then), which would
# rewrite an existing file. Existing files must never be touched, so these
# post-processors only run for files downloaded right now.
#
# The guarded run is assigned after the class is created: the PostProcessor
# metaclass wraps a run defined in the class body to send "started" and
# "finished" to the hooks, and the base run is wrapped already, so a wrapped
# guard would report twice, and a skipped file would still be reported.


def is_new_download(info: Mapping[str, object]) -> bool:
    return info.get("__real_download") is True


RunFunction = Callable[[Any, InfoDict], tuple[list[str], InfoDict]]  # pyright: ignore[reportExplicitAny]


class _SingleHooks(PostProcessor):
    """Constructed with the downloader and then added to it, a post-processor
    gets the downloader's hooks twice (set_downloader runs twice), so the
    same hook is only added once."""

    def add_progress_hook(self, ph: Callable[[str], object]) -> None:  # pyright: ignore[reportImplicitOverride]
        hooks = cast(list[object], getattr(self, "_progress_hooks", []))
        if ph not in hooks:
            super().add_progress_hook(ph)


class NewFileMetadataPP(_SingleHooks, FFmpegMetadataPP):
    """FFmpegMetadataPP for new downloads only."""

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return FFmpegMetadataPP.pp_key()


class NewFileEmbedThumbnailPP(_SingleHooks, EmbedThumbnailPP):
    """EmbedThumbnailPP for new downloads only.

    The thumbnail is written before the download, so for an existing file the
    thumbnails written to the temporary directory are deleted instead, and
    nothing is moved next to the existing file. Thumbnails outside the
    temporary directory (e.g. an existing thumbnail found by yt-dlp in the
    output directory) are never deleted."""

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return EmbedThumbnailPP.pp_key()


# Constructed with the downloader, as yt-dlp does for its "postprocessors"
# option, so they read its parameters (e.g. ffmpeg_location)
def new_file_metadata_pp(downloader: YoutubeDL) -> PostProcessor:
    factory = cast(Callable[..., PostProcessor], NewFileMetadataPP)
    return factory(downloader, add_metadata=True, add_chapters=True, add_infojson=False)


def new_file_embed_thumbnail_pp(downloader: YoutubeDL) -> PostProcessor:
    factory = cast(Callable[..., PostProcessor], NewFileEmbedThumbnailPP)
    return factory(downloader, already_have_thumbnail=False)


def _temporary_thumbnails(pp: PostProcessor, info: InfoDict) -> list[str]:
    paths = cast(object, pp.get_param("paths"))
    if not isinstance(paths, Mapping):
        return []
    temp = cast(Mapping[str, object], paths).get("temp")
    if not isinstance(temp, str) or not temp:
        return []
    temp = os.path.realpath(temp)
    files: list[str] = []
    thumbnails = info.get("thumbnails")
    if not isinstance(thumbnails, list):
        return []
    for thumbnail in cast(list[object], thumbnails):
        if not isinstance(thumbnail, Mapping):
            continue
        filepath = cast(Mapping[str, object], thumbnail).get("filepath")
        if not isinstance(filepath, str) or not filepath:
            continue
        real = os.path.realpath(filepath)
        if os.path.commonpath((temp, real)) == temp and real != temp:
            files.append(filepath)
    return files


def _guard(base: RunFunction, delete_thumbnails: bool) -> RunFunction:
    def run(self: PostProcessor, info: InfoDict) -> tuple[list[str], InfoDict]:
        if is_new_download(info):
            return base(self, info)
        _ = cast(Callable[[str], object], self.to_screen)(
            "Skipping, the file was not downloaded now"
        )
        if delete_thumbnails:
            try:
                return _temporary_thumbnails(self, info), info
            except (OSError, ValueError):
                return [], info
        return [], info

    return run


NewFileMetadataPP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, FFmpegMetadataPP.run), delete_thumbnails=False
)
NewFileEmbedThumbnailPP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, EmbedThumbnailPP.run), delete_thumbnails=True
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _size(value: object) -> int:
    return cast(int, value) if _is_int(value) and cast(int, value) > 0 else 0


def _preference(value: object) -> float:
    # Missing preferences sort as -1, like YoutubeDL._sort_thumbnails
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return -1.0


def _http_url(value: object) -> str | None:
    """The URL if it is an absolute http(s) URL, protocol relative URLs are
    taken as https (unprocessed extractor results are not sanitized)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith("//"):
        url = "https:" + url
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return None
    except ValueError:
        return None
    return url


def _thumbnail_ext(url: str) -> str:
    """The extension yt-dlp would derive, limited to image formats."""
    ext = determine_ext(url, "jpg").lower()
    if ext == "jpeg":
        ext = "jpg"
    return ext if ext in THUMBNAIL_EXTENSIONS else "jpg"
