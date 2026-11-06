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
  and tves atoms. Every info field named meta_<key> (a str, "" removes the
  tag) is passed on as -metadata <key>=<value> AFTER that mapping, so the
  meta_* fields set here override it.
- ffmpeg's mp4 muxer writes title ©nam, artist ©ART, album_artist aART, album
  ©alb, show tvsh, media_type stik, hd_video hdvd, date ©day, episode_id tven,
  season_number tvsn, network tvnn, description desc, synopsis ldes, keywords
  keyw, comment ©cmt and grouping ©grp, but neither rtng nor sdes, and it
  always adds ©too. AtomPostProcessor fixes that up with mutagen.
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
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

from mutagen.mp4 import (
    MP4,
    AtomDataType,  # pyright: ignore[reportAny]
    MP4FreeForm,
)
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.embedthumbnail import (  # pyright: ignore[reportMissingTypeStubs]
    EmbedThumbnailPP as UntypedEmbedThumbnailPP,
)
from yt_dlp.postprocessor.ffmpeg import (  # pyright: ignore[reportMissingTypeStubs]
    FFmpegEmbedSubtitlePP as UntypedFFmpegEmbedSubtitlePP,
)
from yt_dlp.postprocessor.ffmpeg import (  # pyright: ignore[reportMissingTypeStubs]
    FFmpegMetadataPP as UntypedFFmpegMetadataPP,
)
from yt_dlp.utils import determine_ext
from yt_dlp.YoutubeDL import YoutubeDL

# These modules have no type stubs
EmbedThumbnailPP = cast(type[PostProcessor], UntypedEmbedThumbnailPP)
FFmpegEmbedSubtitlePP = cast(type[PostProcessor], UntypedFFmpegEmbedSubtitlePP)
FFmpegMetadataPP = cast(type[PostProcessor], UntypedFFmpegMetadataPP)

# The thumbnail formats yt-dlp knows (MEDIA_EXTENSIONS.thumbnails)
THUMBNAIL_EXTENSIONS: tuple[str, ...] = ("jpg", "png", "webp")

# The atom "stik" (media_type) value of a TV show
MEDIA_TYPE_TV_SHOW = "10"

# The atom "hdvd" (hd_video) levels as (minimum height, level), highest first.
# 0 = SD, 1 = 720p, 2 = 1080p, 3 = 2160p (4K)
HD_VIDEO_LEVELS: tuple[tuple[int, int], ...] = (
    (2160, 3),
    (1080, 2),
    (720, 1),
    (0, 0),
)

# The tags that carry the channel name
CHANNEL_TAGS: tuple[str, ...] = (
    "meta_artist",
    "meta_album_artist",
    "meta_album",
    "meta_show",
)

# The "desc" atom is meant to be short
SHORT_DESCRIPTION_LENGTH = 255
MAX_KEYWORDS = 50

# yt-dlp extractor names (lower case, without sub extractor) -> network
NETWORK_NAMES: dict[str, str] = {
    "youtube": "YouTube",
    "youtubetab": "YouTube",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
    "twitchvod": "Twitch",
    "twitchstream": "Twitch",
    "twitchclips": "Twitch",
    "dailymotion": "Dailymotion",
    "soundcloud": "SoundCloud",
    "bilibili": "Bilibili",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "reddit": "Reddit",
    "rumble": "Rumble",
    "niconico": "Niconico",
    "peertube": "PeerTube",
    "bandcamp": "Bandcamp",
    "twitter": "Twitter",
}

# The container formats mutagen's MP4 can tag
ATOM_EXTENSIONS: tuple[str, ...] = ("mp4", "m4a", "mov")
# Written by ffmpeg whatever the metadata (the encoder), removed
ATOMS_REMOVED: tuple[str, ...] = ("©too", "©enc")
FREEFORM_PREFIX = "----:com.apple.iTunes:"
# Atom name -> info key of the text written to it
FREEFORM_ATOMS: tuple[tuple[str, str], ...] = (
    ("CHANNEL_URL", "channel_url"),
    ("CHANNEL_ID", "channel_id"),
    ("VIDEO_URL", "webpage_url"),
)

HASHTAG = re.compile(r"(?<![\w#])#(\w+)")
UPLOAD_DATE = re.compile(r"[0-9]{8}", re.ASCII)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

InfoDict = dict[str, Any]  # pyright: ignore[reportExplicitAny]


@dataclass(frozen=True)
class MetadataContext:
    series: str | None = None  # top level channel name
    episode_number: int | None = None  # 1 = oldest
    season_number: int | None = 1  # used when the video has no season
    thumbnail_url: str | None = None  # channel graphic (http/https only)
    channel_description: str | None = None  # for the "sdes" atom
    group: str | None = None  # title of the parent playlist, for "©grp"


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


def channel_name(info: Mapping[str, object], context: MetadataContext) -> str | None:
    """The first non-empty name of info["channel"], info["uploader"] and the
    context series (stripped), or None."""
    for value in (info.get("channel"), info.get("uploader"), context.series):
        if name := _name(value):
            return name
    return None


def video_height(info: Mapping[str, object]) -> int | None:
    """The height of the downloaded video: info["height"], else the largest
    height of info["requested_formats"]. Only positive ints count."""
    height = info.get("height")
    if _is_int(height) and cast(int, height) > 0:
        return cast(int, height)
    formats = info.get("requested_formats")
    if not isinstance(formats, list):
        return None
    heights: list[int] = []
    for fmt in cast(list[object], formats):
        if not isinstance(fmt, Mapping):
            continue
        value = cast(Mapping[str, object], fmt).get("height")
        if _is_int(value) and cast(int, value) > 0:
            heights.append(cast(int, value))
    return max(heights) if heights else None


def hd_video(info: Mapping[str, object]) -> str | None:
    """The "hdvd" level (HD_VIDEO_LEVELS) of the video height as a str, or
    None without a height."""
    height = video_height(info)
    if height is None:
        return None
    for minimum, level in HD_VIDEO_LEVELS:
        if height >= minimum:
            return str(level)
    return None


def episode_date(info: Mapping[str, object]) -> str | None:
    """YYYY-MM-DD from info["upload_date"] (YYYYMMDD, a real date), else from
    the UTC date of info["timestamp"], else None."""
    upload_date = info.get("upload_date")
    if isinstance(upload_date, str) and UPLOAD_DATE.fullmatch(upload_date):
        try:
            return date(
                int(upload_date[:4]), int(upload_date[4:6]), int(upload_date[6:])
            ).isoformat()
        except ValueError:
            pass
    moment = _utc(info.get("timestamp"))
    return moment.date().isoformat() if moment is not None else None


def iso_timestamp(value: object) -> str | None:
    """An epoch timestamp (int or float) as ISO 8601 UTC, e.g.
    2024-01-15T12:00:00Z, or None when it is not a usable timestamp."""
    moment = _utc(value)
    if moment is None:
        return None
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def episode_id(info: Mapping[str, object]) -> str | None:
    """The video id (for the "tven" atom), or None."""
    return _text(info.get("id"))


def season_number(info: Mapping[str, object], context: MetadataContext) -> int | None:
    """The season to set: None when the video has a positive season_number
    already (it is kept), else the context season when that is an int."""
    current = info.get("season_number")
    if _is_int(current) and cast(int, current) > 0:
        return None
    return context.season_number if _is_int(context.season_number) else None


def network_name(info: Mapping[str, object]) -> str | None:
    """The network (for the "tvnn" atom) from info["extractor_key"], else
    info["extractor"]: without the ":sub extractor" part and with a display
    name from NETWORK_NAMES, e.g. Youtube:tab -> YouTube."""
    for field in ("extractor_key", "extractor"):
        value = _name(info.get(field))
        if value is None:
            continue
        base = value.split(":", 1)[0].strip()
        if base:
            return NETWORK_NAMES.get(base.lower(), base)
    return None


def short_description(description: object) -> str | None:
    """The description for the "desc" atom: whitespace (also newlines)
    collapsed to single spaces and at most SHORT_DESCRIPTION_LENGTH
    characters. A longer text is cut at a word boundary and gets an ellipsis
    appended (a single word is cut hard). None when there is no text."""
    text = _text(description)
    if text is None:
        return None
    text = " ".join(text.split())
    if len(text) <= SHORT_DESCRIPTION_LENGTH:
        return text
    room = SHORT_DESCRIPTION_LENGTH - 1  # for the ellipsis
    head = text[:room]
    if text[room] != " ":  # the cut is inside a word
        boundary = head.rfind(" ")
        if boundary > 0:
            head = head[:boundary]
    return head.rstrip() + "…"


def synopsis(description: object) -> str | None:
    """The full description for the "ldes" atom (without NUL characters), or
    None when there is no text."""
    return _text(description)


def hashtags(*texts: object) -> list[str]:
    """The hashtags of the texts without the "#", in order of appearance,
    unique ignoring case, at most MAX_KEYWORDS."""
    tags: list[str] = []
    seen: set[str] = set()
    for text in texts:
        cleaned = _text(text)
        if cleaned is None:
            continue
        for tag in cast(list[str], HASHTAG.findall(cleaned)):
            if tag.casefold() in seen:
                continue
            seen.add(tag.casefold())
            tags.append(tag)
            if len(tags) >= MAX_KEYWORDS:
                return tags
    return tags


def keywords(info: Mapping[str, object]) -> str | None:
    """The hashtags of the title and then the description, joined by ", "
    (for the "keyw" atom), or None when there are none."""
    tags = hashtags(info.get("title"), info.get("description"))
    return ", ".join(tags) if tags else None


def atom_values(info: Mapping[str, object]) -> dict[str, list[object]]:
    """The atoms AtomPostProcessor writes, as mutagen takes them (atom ->
    list of values): rtng (age_limit >= 18), sdes (vidl_channel_description)
    and the freeform CHANNEL_URL, CHANNEL_ID, VIDEO_URL and UPLOAD_TIMESTAMP.
    Only atoms with a valid source value are included."""
    values: dict[str, list[object]] = {}
    age_limit = info.get("age_limit")
    if _is_int(age_limit) and cast(int, age_limit) >= 18:
        values["rtng"] = [1]
    if (description := _text(info.get("vidl_channel_description"))) is not None:
        values["sdes"] = [description]
    for name, key in FREEFORM_ATOMS:
        if (text := _text(info.get(key))) is not None:
            values[FREEFORM_PREFIX + name] = [_freeform(text)]
    if (stamp := iso_timestamp(info.get("timestamp"))) is not None:
        values[FREEFORM_PREFIX + "UPLOAD_TIMESTAMP"] = [_freeform(stamp)]
    return values


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
        if (name := channel_name(info, context)) is not None:
            updates.update(dict.fromkeys(CHANNEL_TAGS, name))
        if _is_int(context.episode_number):
            updates["episode_number"] = context.episode_number
        if (
            isinstance(context.series, str)
            and context.series
            and not info.get("series")
        ):
            updates["series"] = context.series
        if (season := season_number(info, context)) is not None:
            updates["season_number"] = season
        description = info.get("description")
        tags: dict[str, str | None] = {
            "meta_media_type": MEDIA_TYPE_TV_SHOW,
            "meta_hd_video": hd_video(info),
            "meta_date": episode_date(info),
            "meta_episode_id": episode_id(info),
            "meta_network": network_name(info),
            "meta_description": short_description(description),
            "meta_synopsis": synopsis(description),
            "meta_keywords": keywords(info),
            # yt-dlp fills the comment with the video URL, an empty value
            # makes ffmpeg remove the tag
            "meta_comment": "",
            "meta_grouping": _name(context.group),
            # Not for ffmpeg, read by AtomPostProcessor
            "vidl_channel_description": _text(context.channel_description),
        }
        updates.update({key: tag for key, tag in tags.items() if tag is not None})
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


class AtomPostProcessor(_SingleHooks):
    """Fixes up the atoms of an mp4/m4a/mov file with mutagen, after
    FFmpegMetadataPP (which has written the meta_* tags) and before
    EmbedThumbnailPP (which uses mutagen as well):

    - removes the encoder atoms ©too and ©enc,
    - writes rtng (age limit), sdes (channel description) and the freeform
      atoms of atom_values, none of which ffmpeg can write.

    The tags are written to a copy of the file that replaces it when it is
    saved, as mutagen saves in place and a failure would corrupt the file.
    It never raises, a failure only reports a warning and leaves the file
    as it is."""

    KEY: ClassVar[str] = "Atoms"

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return cls.KEY

    def run(self, info: InfoDict) -> tuple[list[str], InfoDict]:  # pyright: ignore[reportImplicitOverride, reportIncompatibleMethodOverride]
        try:
            self.__apply(info)
        except Exception as e:  # noqa: BLE001 - must never break a download
            self.__warn(f"Atoms not written, {type(e).__name__}: {e}")
        return [], info

    def __apply(self, info: Mapping[str, object]) -> None:
        filepath = info.get("filepath")
        ext = info.get("ext")
        if not isinstance(filepath, str) or not isinstance(ext, str):
            return self.__skip("there is no file")
        suffix = os.path.splitext(filepath)[1].lstrip(".").lower()
        if ext.lower() not in ATOM_EXTENSIONS or suffix not in ATOM_EXTENSIONS:
            return self.__skip(f"only {', '.join(ATOM_EXTENSIONS)} files have atoms")
        if not os.path.isfile(filepath):
            return self.__skip("the file is missing")
        values = atom_values(info)
        removed = ATOMS_REMOVED + (() if "rtng" in values else ("rtng",))
        _ = cast(Callable[[str], object], self.to_screen)(
            f'Writing atoms to "{filepath}"'
        )
        directory = os.path.dirname(filepath) or "."
        handle, temporary = tempfile.mkstemp(
            prefix=".vidl-atoms-", suffix=".tmp", dir=directory
        )
        os.close(handle)
        try:
            _ = shutil.copy2(filepath, temporary)
            mp4 = MP4(temporary)
            if mp4.tags is None:
                mp4.add_tags()
            tags = mp4.tags
            assert tags is not None
            for key in removed:
                if key in tags:
                    del tags[key]
            for key, value in values.items():
                tags[key] = value
            mp4.save()  # pyright: ignore[reportUnknownMemberType]
            os.replace(temporary, filepath)
        finally:
            with suppress(OSError):
                os.remove(temporary)

    def __skip(self, reason: str) -> None:
        self.__say(f"Skipping atoms, {reason}")

    def __say(self, message: str) -> None:
        try:
            _ = cast(Callable[[str], object], self.to_screen)(message)
        except Exception:  # noqa: BLE001, S110
            pass

    def __warn(self, message: str) -> None:
        # A failing logger must not break a download either
        try:
            self.report_warning(f"[{self.pp_key()}] {message}")
        except Exception:  # noqa: BLE001, S110
            pass


class NewFileAtomPP(AtomPostProcessor):
    """AtomPostProcessor for new downloads only."""


class NewFileEmbedSubtitlePP(_SingleHooks, FFmpegEmbedSubtitlePP):
    """FFmpegEmbedSubtitlePP for new downloads only.

    The subtitles are written before the download, so for an existing file
    the subtitle files written to the temporary directory are deleted
    instead, and nothing is moved next to the existing file. Subtitle files
    outside the temporary directory are never deleted."""

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return FFmpegEmbedSubtitlePP.pp_key()


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


def new_file_atom_pp(downloader: YoutubeDL) -> PostProcessor:
    factory = cast(Callable[..., PostProcessor], NewFileAtomPP)
    return factory(downloader)


def new_file_embed_subtitle_pp(downloader: YoutubeDL) -> PostProcessor:
    factory = cast(Callable[..., PostProcessor], NewFileEmbedSubtitlePP)
    return factory(downloader, already_have_subtitle=False)


def _temporary_files(pp: PostProcessor, entries: Iterable[object]) -> list[str]:
    """The "filepath" of the entries (thumbnail or subtitle dicts) that is
    inside the temporary directory (after resolving links), never the
    directory itself and never anything else."""
    paths = cast(object, pp.get_param("paths"))
    if not isinstance(paths, Mapping):
        return []
    temp = cast(Mapping[str, object], paths).get("temp")
    if not isinstance(temp, str) or not temp:
        return []
    temp = os.path.realpath(temp)
    files: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        filepath = cast(Mapping[str, object], entry).get("filepath")
        if not isinstance(filepath, str) or not filepath:
            continue
        real = os.path.realpath(filepath)
        if os.path.commonpath((temp, real)) == temp and real != temp:
            files.append(filepath)
    return files


def _temporary_thumbnails(pp: PostProcessor, info: InfoDict) -> list[str]:
    thumbnails = info.get("thumbnails")
    if not isinstance(thumbnails, list):
        return []
    return _temporary_files(pp, cast(list[object], thumbnails))


def _temporary_subtitles(pp: PostProcessor, info: InfoDict) -> list[str]:
    subtitles = info.get("requested_subtitles")
    if not isinstance(subtitles, Mapping):
        return []
    return _temporary_files(pp, cast(Mapping[str, object], subtitles).values())


Leftovers = Callable[[PostProcessor, InfoDict], list[str]]


def _guard(base: RunFunction, leftovers: Leftovers | None = None) -> RunFunction:
    """Runs base for new downloads only. For existing files, leftovers
    names the files to delete instead (those written to the temporary
    directory for the file, which would be moved next to it otherwise)."""

    def run(self: PostProcessor, info: InfoDict) -> tuple[list[str], InfoDict]:
        if is_new_download(info):
            return base(self, info)
        _ = cast(Callable[[str], object], self.to_screen)(
            "Skipping, the file was not downloaded now"
        )
        if leftovers is not None:
            try:
                return leftovers(self, info), info
            except (OSError, ValueError):
                return [], info
        return [], info

    return run


NewFileMetadataPP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, FFmpegMetadataPP.run)
)
NewFileAtomPP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, AtomPostProcessor.run)
)
NewFileEmbedSubtitlePP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, FFmpegEmbedSubtitlePP.run), _temporary_subtitles
)
NewFileEmbedThumbnailPP.run = _guard(  # pyright: ignore[reportAttributeAccessIssue]
    cast(RunFunction, EmbedThumbnailPP.run), _temporary_thumbnails
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    """The str without NUL characters (ffmpeg cannot take them on the command
    line), or None when it is not a str or only whitespace."""
    if not isinstance(value, str):
        return None
    value = value.replace("\0", "")
    return value if value.strip() else None


def _name(value: object) -> str | None:
    """Like _text, stripped."""
    text = _text(value)
    return text.strip() if text is not None else None


def _freeform(text: str) -> MP4FreeForm:
    return MP4FreeForm(
        text.encode("utf-8"),
        dataformat=AtomDataType.UTF8,  # pyright: ignore[reportAny]
    )


def _utc(value: object) -> datetime | None:
    """The UTC time of an epoch timestamp (int or float), or None."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not math.isfinite(value):
        return None
    try:
        return EPOCH + timedelta(seconds=value)
    except (OverflowError, ValueError):
        return None


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
