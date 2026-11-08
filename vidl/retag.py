"""Rewrites the tags of existing mp4/m4v files of YouTube videos to the format
of new downloads, see vidl/metadata.py.

    python -m vidl.retag FOLDER [--dry-run] [...]

For each file the video id is taken from the "tven" atom, or from the file name
when that is no id (older versions of ViDL wrote the title there, and the file
name is the id), the video is looked up on YouTube, and the tags are written
with the same rules as for a new download (metadata_updates, atom_values).

Safety, as the files are the only copy:

- Nothing is written to the file itself. The tags are written with mutagen to
  a copy that is checked and then replaces the file, so the audio and video
  streams are never touched, and a failure leaves the file as it was. The
  copy keeps the modification time and permissions, and on macOS the extended
  attributes (Finder tags), as "cp -p" does. Its creation date is new.
- A file is only changed when YouTube's answer is for the same video, that is
  its id matches and its duration is that of the file (within a tolerance).
- Before the copy replaces the file, it is read back: every tag must be as
  planned, all other tags (such as the cover) unchanged, and the streams,
  checked with ffprobe, the same. The file must also not have changed
  meanwhile.
- Only the tags of new downloads are written. The episode number is that of the
  video in the uploads of its channel (the first video is 1, see
  vidl/episodes.py), counted from the list of the channel, which is fetched
  once for each channel. The season is 1 unless YouTube gives the video one,
  as for a download, so the one in the file is replaced. The grouping cannot
  be derived from a single video, so the one in the file is kept, as is the
  resolution class (hdvd) if the height cannot be read. Tags are only removed when new
  downloads do not have them either: the comment, the encoder, and the
  explicit rating of a video that is not.
- Files that are already in the new format are left alone (without a request),
  so an interrupted run can be repeated.
- YouTube is asked slowly, and the run stops at once when YouTube restricts
  the requests (bot check, rate limit), or when the network is down.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Protocol, cast

from mutagen import MutagenError
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from yt_dlp import YoutubeDL

from .config import Config
from .episodes import (
    PLAYLIST,
    UPLOADS,
    EpisodeIndex,
    complete_ids,
    uploads_url,
)
from .error import Error
from .metadata import (
    ATOMS_REMOVED,
    FREEFORM_PREFIX,
    MetadataContext,
    atom_values,
    metadata_updates,
)

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}", re.ASCII)
EXTENSIONS: tuple[str, ...] = (".mp4", ".m4v")
WATCH_URL = "https://www.youtube.com/watch?v="

# ffmpeg metadata key -> (atom, numeric), what ffmpeg's mp4 muxer writes
ATOMS: dict[str, tuple[str, bool]] = {
    "title": ("©nam", False),
    "artist": ("©ART", False),
    "album_artist": ("aART", False),
    "album": ("©alb", False),
    "show": ("tvsh", False),
    "media_type": ("stik", True),
    "hd_video": ("hdvd", True),
    "date": ("©day", False),
    "genre": ("©gen", False),
    "episode_sort": ("tves", True),
    "episode_id": ("tven", False),
    "season_number": ("tvsn", True),
    "network": ("tvnn", False),
    "description": ("desc", False),
    "synopsis": ("ldes", False),
    "keywords": ("keyw", False),
    "comment": ("©cmt", False),
    "grouping": ("©grp", False),
}
# An empty value removes these, as it does in ffmpeg. For the others it means
# there is nothing to write, which does not remove what the file has
REMOVED_WHEN_EMPTY: frozenset[str] = frozenset({"©cmt"})
# The largest value of an atom of this width
INTEGER_LIMITS: dict[str, int] = {
    "stik": 255,
    "hdvd": 255,
    "tves": 2**31,
    "tvsn": 2**31,
}
# The order tags are shown in
ORDER: tuple[str, ...] = (
    "©nam",
    "©ART",
    "aART",
    "©alb",
    "tvsh",
    "stik",
    "hdvd",
    "©day",
    "©gen",
    "tves",
    "tven",
    "tvsn",
    "tvnn",
    "desc",
    "ldes",
    "sdes",
    "keyw",
    "rtng",
    "©grp",
    "©cmt",
    "©too",
    "©enc",
)

# How yt-dlp's FFmpegMetadataPP fills the metadata from the info of a video
# (_get_metadata_opts): the first of the keys that is set, joined by ", "
TAG_SOURCES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("title",), ("track", "title")),
    (("date",), ("upload_date",)),
    (("description", "synopsis"), ("description",)),
    (("comment",), ("webpage_url",)),
    (
        ("artist",),
        ("artist", "artists", "creator", "creators", "uploader", "uploader_id"),
    ),
    (("genre",), ("genre", "genres", "categories", "tags")),
    (("album",), ("album", "series")),
    (("album_artist",), ("album_artist", "album_artists")),
    (("show",), ("series",)),
    (("season_number",), ("season_number",)),
    (("episode_id",), ("episode", "episode_id")),
    (("episode_sort",), ("episode_number",)),
)
META_KEY = re.compile(r"meta(?P<index>\d+)?_(?P<key>.+)", re.DOTALL)


def _variadic(value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        return (cast(object, value),)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(cast("Sequence[object]", value))
    return (value,)


def ffmpeg_tags(info: Mapping[str, object]) -> dict[str, str]:
    """The metadata yt-dlp's FFmpegMetadataPP gives ffmpeg for the info of a
    video (only the keys of ATOMS are of use): its own mapping of the info
    fields, overridden by the meta_* fields. An empty text removes the tag."""
    tags: dict[str, str] = {}
    for keys, sources in TAG_SOURCES:
        value = next((info[key] for key in sources if info.get(key) is not None), None)
        if value is None or (isinstance(value, str) and value == ""):
            continue
        text = ", ".join(map(str, _variadic(value))).replace("\0", "")
        if text == "" and not isinstance(value, (list, tuple)):
            continue
        tags.update(dict.fromkeys(keys, text))
    for name, value in info.items():
        found = META_KEY.fullmatch(name)
        # Without an index they are for the file, otherwise for a stream
        if found and found.group("index") is None and isinstance(value, str):
            tags[found.group("key")] = value.replace("\0", "")
    return tags


@dataclass(frozen=True)
class Target:
    """The tags of a file as they are to be: the atoms to set, with the values
    as mutagen takes them, and the atoms to remove."""

    values: dict[str, list[object]]
    remove: frozenset[str]


def target_atoms(info: Mapping[str, object]) -> Target:
    """The tags new downloads get for the info of a video, which has the
    fields of metadata_updates: FFmpegMetadataPP's (ffmpeg_tags) and then those
    of AtomPostProcessor (atom_values)."""
    tags = ffmpeg_tags(info)
    values: dict[str, list[object]] = {}
    remove: set[str] = set(ATOMS_REMOVED)
    for key, text in tags.items():
        if key not in ATOMS:
            continue
        atom, numeric = ATOMS[key]
        if text == "":
            if atom in REMOVED_WHEN_EMPTY:
                remove.add(atom)
        elif not numeric:
            values[atom] = [text]
        elif (number := _integer(text)) is not None and number < INTEGER_LIMITS[atom]:
            values[atom] = [number]
    atoms = atom_values(info)
    values.update(atoms)
    if "rtng" not in atoms:
        remove.add("rtng")
    return Target(values, frozenset(remove))


def _integer(text: str) -> int | None:
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def normal(values: object) -> tuple[str, ...]:
    """The values of an atom as texts, to compare them: freeform data decoded,
    numbers as text, a cover as a marker."""
    if values is None:
        return ()
    items = cast("list[object]", values) if isinstance(values, list) else [values]
    result: list[str] = []
    for item in items:
        if isinstance(item, MP4FreeForm):
            result.append(bytes(item).decode("utf-8", "replace"))
        elif isinstance(item, MP4Cover):
            result.append(f"<cover {len(bytes(item))} bytes>")
        elif isinstance(item, (bytes, bytearray)):
            result.append(bytes(item).decode("utf-8", "replace"))
        else:
            result.append(str(item))
    return tuple(result)


@dataclass(frozen=True)
class Change:
    """A tag that is different, as texts. new is None when it is removed, old
    is None when it is new."""

    atom: str
    old: str | None
    new: str | None

    def __str__(self) -> str:  # pyright: ignore[reportImplicitOverride]
        name = self.atom.removeprefix(FREEFORM_PREFIX)
        return f"{name}: {_shown(self.old)} -> {_shown(self.new)}"


def _shown(text: str | None, limit: int = 60) -> str:
    if text is None:
        return "(none)"
    text = " ".join(text.split())
    return repr(text if len(text) <= limit else text[: limit - 1] + "…")


def plan_changes(tags: Mapping[str, object], target: Target) -> list[Change]:
    """The tags that have to change to give the file the target."""
    changes: list[Change] = []
    for atom, values in target.values.items():
        old = normal(tags.get(atom))
        new = normal(values)
        if old != new:
            changes.append(
                Change(atom, ", ".join(old) if old else None, ", ".join(new))
            )
    for atom in target.remove:
        if atom in tags:
            changes.append(Change(atom, ", ".join(normal(tags[atom])), None))
    position = {atom: index for index, atom in enumerate(ORDER)}
    return sorted(changes, key=lambda c: (position.get(c.atom, len(ORDER)), c.atom))


def text_of(values: object) -> str | None:
    """The first value of an atom as text, if it is one."""
    texts = normal(values)
    return texts[0].strip() or None if texts else None


def candidate_ids(tags: Mapping[str, object], path: str) -> list[str]:
    """The video ids the file may have: the "tven" atom, then the file name,
    each only if it looks like a YouTube id (11 letters, digits, "-" and "_")."""
    stem = os.path.splitext(os.path.basename(path))[0]
    ids: list[str] = []
    for text in (text_of(tags.get("tven")), stem):
        if text and YOUTUBE_ID.fullmatch(text) and text not in ids:
            ids.append(text)
    return ids


def is_current(tags: Mapping[str, object]) -> bool:
    """Whether the file has the tags of a new download: a video id in "tven",
    the type TV show, the freeform tags (also EPISODE_ORDER, which says that
    the episode number is counted as it is now), season 1 (the season of
    older files was kept, which may be wrong; a video with a season of its own
    is checked again, which is harmless), and no encoder or comment."""
    video = text_of(tags.get("tven"))
    if not video or not YOUTUBE_ID.fullmatch(video):
        return False
    if normal(tags.get("stik")) != ("10",):
        return False
    if normal(tags.get("tvsn")) != ("1",):
        return False
    for name in ("VIDEO_URL", "CHANNEL_ID", "UPLOAD_TIMESTAMP", "EPISODE_ORDER"):
        if FREEFORM_PREFIX + name not in tags:
            return False
    return not any(atom in tags for atom in (*ATOMS_REMOVED, "©cmt"))


@dataclass(frozen=True)
class Probe:
    """What ffprobe says of a file: the height of the video (not the cover)
    and a signature of the streams."""

    height: int | None
    streams: tuple[tuple[str, str, int, float | None], ...]


def probe_file(path: str) -> Probe | None:
    """Reads the streams of a file with ffprobe, None when that is not
    possible."""
    try:
        done = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                os.path.abspath(path),
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
        if done.returncode != 0:
            return None
        data = cast("dict[str, object]", json.loads(done.stdout))
        streams: list[tuple[str, str, int, float | None]] = []
        height: int | None = None
        for item in cast("list[object]", data.get("streams", [])):
            stream = cast("dict[str, object]", item)
            kind = str(stream.get("codec_type", ""))
            disposition = cast("dict[str, object]", stream.get("disposition") or {})
            cover = int(str(disposition.get("attached_pic", 0)))
            duration = _seconds(stream.get("duration"))
            streams.append((kind, str(stream.get("codec_name", "")), cover, duration))
            found = stream.get("height")
            if (
                kind == "video"
                and not cover
                and height is None
                and isinstance(found, int)
                and found > 0
            ):
                height = found
        return Probe(height, tuple(streams))
    except (OSError, ValueError, subprocess.SubprocessError, TypeError, AttributeError):
        return None


def _seconds(value: object) -> float | None:
    try:
        return round(float(cast(str, value)), 1)
    except (TypeError, ValueError):
        return None


def clone(source: str, target: str) -> None:
    """Copies a file with its modification time, permissions and, with cp,
    its extended attributes and flags. On APFS a clone is made, which takes
    no space until the copy is changed."""
    commands: list[list[str]] = []
    if sys.platform == "darwin":
        commands.append(["cp", "-pc", "--", source, target])
    commands.append(["cp", "-p", "--", source, target])
    for command in commands:
        try:
            done = subprocess.run(command, capture_output=True, check=False)
        except OSError:
            continue
        if done.returncode == 0:
            return
        with suppress(OSError):
            os.remove(target)
    import shutil

    _ = shutil.copy2(source, target)


class SourceError(Exception):
    """YouTube had no usable answer, error says why."""

    def __init__(self, error: Error) -> None:
        super().__init__(error.summary)
        self.error: Error = error


class Source(Protocol):
    """Where the information of the videos comes from."""

    def video(self, video_id: str) -> Mapping[str, object]: ...

    def channel_description(self, info: Mapping[str, object]) -> str | None: ...

    def episodes(self, info: Mapping[str, object]) -> EpisodeIndex | None:
        """The numbering of the videos of the channel of a video, None when
        there is none (a loader of it may raise SourceError)."""
        ...


class _Quiet:
    """yt-dlp's output is not wanted, the errors are returned as exceptions."""

    def debug(self, _message: str) -> None: ...

    def info(self, _message: str) -> None: ...

    def warning(self, _message: str) -> None: ...

    def error(self, _message: str) -> None: ...


class YoutubeSource:
    """The information of YouTube videos from yt-dlp, as ViDL gets it before
    a download (extracted without processing the formats)."""

    def __init__(
        self,
        cookie_browser: str | None = None,
        factory: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        settings: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "color": "no_color",
            "skip_download": True,
            "ignoreerrors": False,
            "logger": _Quiet(),
            "retries": 3,
            "sleep_interval_requests": 1,
        }
        if cookie_browser:
            settings["cookiesfrombrowser"] = (cookie_browser, None, None, None)
        make = factory or cast("Callable[[dict[str, object]], object]", YoutubeDL)
        self.__ydl = make(settings)

    def close(self) -> None:
        with suppress(Exception):
            _ = cast("Callable[[], object]", getattr(self.__ydl, "close"))()  # noqa: B009

    def video(self, video_id: str) -> Mapping[str, object]:
        return self.__extract(WATCH_URL + video_id, [video_id])

    def channel_description(self, info: Mapping[str, object]) -> str | None:
        url = info.get("channel_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            return None
        description = self.__extract(url, []).get("description")
        if isinstance(description, str):
            return description.strip() or None
        return None

    def episodes(self, info: Mapping[str, object]) -> EpisodeIndex | None:
        channel = info.get("channel_id")
        url = uploads_url(channel) if isinstance(channel, str) else None
        if url is None:
            return None
        return EpisodeIndex(lambda: self.__uploads(url), True, UPLOADS)

    # The ids of the uploads, newest first
    def __uploads(self, url: str) -> list[str]:
        info = self.__extract(url, [])
        try:
            # The entries are loaded page by page, while they are read
            return complete_ids(info)
        except Exception as e:
            raise SourceError(Error.from_exception(e)) from e

    def __extract(self, url: str, known: list[str]) -> Mapping[str, object]:
        extract = cast("Callable[..., object]", getattr(self.__ydl, "extract_info"))  # noqa: B009
        try:
            info = extract(url, download=False, process=False)
        except Exception as e:
            raise SourceError(Error.from_exception(e, known)) from e
        if not isinstance(info, dict):
            raise SourceError(Error("No information was returned"))
        return cast("dict[str, object]", info)


class Status(Enum):
    UPDATED = "updated"
    WOULD_UPDATE = "would update"
    UNCHANGED = "unchanged"
    CURRENT = "current"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class Outcome:
    path: str
    status: Status
    message: str = ""
    changes: tuple[Change, ...] = ()


@dataclass
class Summary:
    counts: Counter[Status] = field(default_factory=lambda: Counter[Status]())
    aborted: str | None = None
    limited: bool = False


class Aborted(Exception):
    """The run is stopped, YouTube is restricting the requests or the network
    is down, going on would only make it worse."""


class _Skip(Exception):
    """The file is left as it is, for a reason that is no error."""


def find_videos(folder: str, recursive: bool = True) -> list[str]:
    """The mp4 and m4v files in the folder, sorted. Hidden files and folders
    (also the temporary files of ViDL and macOS) are left out, and links to
    folders are not followed."""
    found: list[str] = []
    for root, directories, files in os.walk(folder):
        directories[:] = sorted(d for d in directories if not d.startswith("."))
        for name in sorted(files):
            if not name.startswith(".") and name.lower().endswith(EXTENSIONS):
                found.append(os.path.join(root, name))
        if not recursive:
            break
    return sorted(found)


class Retagger:
    """Gives existing files the tags of new downloads, see the module text."""

    # Consecutive answers of "restricted" (e.g. Video unavailable), or errors
    # of the network, after which the run stops
    MAX_RESTRICTED: ClassVar[int] = 5
    MAX_NETWORK: ClassVar[int] = 3

    def __init__(
        self,
        source: Source,
        *,
        dry_run: bool = False,
        force: bool = False,
        sleep: float = 3.0,
        tolerance: float = 5.0,
        channel_descriptions: bool = True,
        limit: int | None = None,
        verbose: bool = False,
        probe: Callable[[str], Probe | None] = probe_file,
        pause: Callable[[float], None] = time.sleep,
        write: Callable[[str], None] = print,
        copy: Callable[[str, str], None] = clone,
    ) -> None:
        self.__source = source
        self.__dry_run = dry_run
        self.__force = force
        self.__sleep = sleep
        self.__tolerance = tolerance
        self.__channel_descriptions = channel_descriptions
        self.__limit = limit
        self.__verbose = verbose
        self.__probe = probe
        self.__pause = pause
        self.__write = write
        self.__copy = copy
        self.__requests = 0  # the requests made so far
        self.__fetched = 0  # the files YouTube was asked about
        self.__restricted = 0
        self.__network = 0
        self.__channels: dict[str, str | None] = {}
        self.__indexes: dict[str, EpisodeIndex | None] = {}
        self.__listed: set[str] = set()

    def run(self, folder: str, recursive: bool = True) -> Summary:
        paths = find_videos(folder, recursive)
        summary = Summary()
        for number, path in enumerate(paths, start=1):
            try:
                outcome = self.process(path)
            except Aborted as e:
                summary.aborted = str(e)
                break
            summary.counts[outcome.status] += 1
            self.__report(number, len(paths), folder, outcome)
            if self.__limit is not None and self.__fetched >= self.__limit:
                summary.limited = number < len(paths)
                break
        return summary

    def process(self, path: str) -> Outcome:
        """Retags one file. Never raises, except Aborted."""
        try:
            return self.__process(path)
        except _Skip as e:
            return Outcome(path, Status.SKIPPED, str(e))
        except Aborted:
            raise
        except Exception as e:  # noqa: BLE001 - a file must not stop the run
            return Outcome(path, Status.FAILED, f"{type(e).__name__}: {e}")

    def __process(self, path: str) -> Outcome:
        if os.path.islink(path):
            raise _Skip("a symbolic link")
        before = os.stat(path)
        try:
            mp4 = MP4(path)
        except (MutagenError, OSError) as e:
            raise _Skip(f"not a readable mp4 ({type(e).__name__})") from e
        tags = _tags(mp4)
        ids = candidate_ids(tags, path)
        if not ids:
            raise _Skip("no video id in tven or in the file name")
        if not self.__force and is_current(tags):
            return Outcome(path, Status.CURRENT, "already in the current format")
        length = float(mp4.info.length)
        probe = self.__probe(path)
        info = self.__fetch(ids, length)
        episode, order = self.__episode(info, tags)
        context = MetadataContext(
            episode_number=episode,
            episode_order=order,
            # The season is 1 unless the video has one, as for a download
            group=text_of(tags.get("©grp")),
            channel_description=self.__channel_description(
                info, text_of(tags.get("sdes"))
            ),
        )
        video = dict(info)
        # The height of the file, not of the best format YouTube offers
        for key in ("height", "width", "requested_formats"):
            _ = video.pop(key, None)
        if probe is not None and probe.height is not None:
            video["height"] = probe.height
        video.update(metadata_updates(video, context))
        target = target_atoms(video)
        changes = tuple(plan_changes(tags, target))
        if not changes:
            return Outcome(path, Status.UNCHANGED, "the tags are as they should be")
        text = f"{len(changes)} tag{'s' if len(changes) != 1 else ''}"
        if self.__dry_run:
            return Outcome(path, Status.WOULD_UPDATE, text, changes)
        self.__replace(path, before, tags, target, probe, length)
        return Outcome(path, Status.UPDATED, text, changes)

    def __fetch(self, ids: list[str], length: float) -> Mapping[str, object]:
        """The info of the first id that is the video of the file."""
        problems: list[str] = []
        self.__fetched += 1
        for video_id in ids:
            self.__throttle()
            try:
                info = self.__source.video(video_id)
            except SourceError as e:
                self.__note(e.error)
                problems.append(f"{video_id}: {e.error.summary}")
                continue
            self.__restricted = self.__network = 0
            if info.get("id") != video_id:
                problems.append(f"{video_id}: YouTube answered with another video")
                continue
            duration = info.get("duration")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                allowed = max(self.__tolerance, 0.02 * float(duration))
                if abs(float(duration) - length) > allowed:
                    problems.append(
                        f"{video_id}: the video is {_clock(float(duration))} long, "
                        + f"the file {_clock(length)}"
                    )
                    continue
            return info
        raise _Skip("; ".join(problems))

    # YouTube is asked slowly: a pause before each request, but the first
    def __throttle(self) -> None:
        if self.__requests:
            self.__pause(self.__sleep)
        self.__requests += 1

    def __note(self, error: Error) -> None:
        if error.category in (Error.Category.BOT_CHECK, Error.Category.RATE_LIMITED):
            raise Aborted(
                f"YouTube restricts the requests ({error.summary}), try again later, "
                + "or with --cookies-from-browser"
            )
        if error.restriction:
            self.__restricted += 1
            self.__network = 0
            if self.__restricted >= Retagger.MAX_RESTRICTED:
                raise Aborted(
                    f"{self.__restricted} requests in a row were restricted "
                    + f"({error.summary}), YouTube may be blocking, try again later"
                )
        elif error.category is Error.Category.NETWORK:
            self.__network += 1
            self.__restricted = 0
            if self.__network >= Retagger.MAX_NETWORK:
                raise Aborted(f"the network seems to be down ({error.summary})")
        else:
            self.__restricted = self.__network = 0

    def __episode(
        self, info: Mapping[str, object], tags: Mapping[str, object]
    ) -> tuple[int | None, str | None]:
        """The episode number of the video and how it was counted: the first
        video of the channel is 1. The number in the file is kept when there is
        no list (or the video is not in it, e.g. it is for members only), and
        when it was counted over a playlist, which cannot be known here."""
        existing = _number(tags.get("tves"), positive=True)
        kept_order = text_of(tags.get(FREEFORM_PREFIX + "EPISODE_ORDER"))
        if kept_order == PLAYLIST and existing is not None:
            return existing, PLAYLIST
        keep = (existing, kept_order if existing is not None else None)
        video_id = info.get("id")
        channel = info.get("channel_id")
        if not isinstance(video_id, str) or not isinstance(channel, str):
            return keep
        if channel not in self.__indexes:
            self.__indexes[channel] = self.__source.episodes(info)
        index = self.__indexes[channel]
        if index is None:
            return keep
        first = channel not in self.__listed
        if first:
            self.__listed.add(channel)
            self.__throttle()
            self.__write(f"      listing the uploads of the channel {channel} ...")
        try:
            number = index.number(video_id)
        except SourceError as e:
            self.__note(e.error)
            self.__write(f"      the uploads could not be listed ({e.error.summary})")
            return keep
        if first and index.size is not None:
            self.__write(f"      {index.size} videos")
        return (number, index.order) if number is not None else keep

    def __channel_description(
        self, info: Mapping[str, object], existing: str | None
    ) -> str | None:
        """The description of the channel of the video, asked once for each
        channel. The one in the file is kept when there is none."""
        if not self.__channel_descriptions:
            return existing
        key = info.get("channel_id") or info.get("channel_url")
        if not isinstance(key, str) or not key:
            return existing
        if key not in self.__channels:
            self.__throttle()
            try:
                self.__channels[key] = self.__source.channel_description(info)
            except SourceError as e:
                self.__channels[key] = None
                self.__note(e.error)
        return self.__channels[key] or existing

    def __replace(
        self,
        path: str,
        before: os.stat_result,
        tags: Mapping[str, object],
        target: Target,
        probe: Probe | None,
        length: float,
    ) -> None:
        directory = os.path.dirname(path) or "."
        temporary = os.path.join(directory, f".vidl-retag-{uuid.uuid4().hex}.tmp")
        try:
            self.__copy(path, temporary)
            mp4 = MP4(temporary)
            if mp4.tags is None:
                mp4.add_tags()
            written = mp4.tags
            assert written is not None
            for atom in target.remove:
                if atom in written:
                    del written[atom]
            for atom, values in target.values.items():
                written[atom] = values
            mp4.save()  # pyright: ignore[reportUnknownMemberType]
            self.__check(temporary, tags, target, probe, length)
            with open(temporary, "rb+") as f:
                os.fsync(f.fileno())
            os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
            now = os.stat(path)
            if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise RuntimeError("the file changed while it was being processed")
            os.replace(temporary, path)
        finally:
            with suppress(OSError):
                os.remove(temporary)

    def __check(
        self,
        path: str,
        old: Mapping[str, object],
        target: Target,
        probe: Probe | None,
        length: float,
    ) -> None:
        """Reads the new file back, before it replaces the file."""
        try:
            new = MP4(path)
        except (MutagenError, OSError) as e:
            raise RuntimeError(
                f"the new file cannot be read ({type(e).__name__})"
            ) from e
        tags = _tags(new)
        for atom, values in target.values.items():
            if normal(tags.get(atom)) != normal(values):
                raise RuntimeError(f"the tag {atom} was not written")
        for atom in target.remove:
            if atom in tags:
                raise RuntimeError(f"the tag {atom} was not removed")
        for atom, values in old.items():
            untouched = atom not in target.values and atom not in target.remove
            if untouched and normal(tags.get(atom)) != normal(values):
                raise RuntimeError(f"the tag {atom} was changed")
        for atom in tags:
            if atom not in old and atom not in target.values:
                raise RuntimeError(f"the tag {atom} appeared")
        if abs(float(new.info.length) - length) > 0.001:
            raise RuntimeError("the duration changed")
        if probe is not None:
            after = self.__probe(path)
            if after is None or after.streams != probe.streams:
                raise RuntimeError("the streams of the new file differ")

    def __report(self, number: int, total: int, folder: str, outcome: Outcome) -> None:
        name = os.path.relpath(outcome.path, folder)
        width = len(str(total))
        line = f"[{number:>{width}}/{total}] {outcome.status.value.upper():<12} {name}"
        if outcome.message:
            line += f"  ({outcome.message})"
        self.__write(line)
        if self.__verbose or self.__dry_run:
            for change in outcome.changes:
                self.__write(f"      {change}")


def _tags(mp4: MP4) -> dict[str, object]:
    return dict(mp4.tags) if mp4.tags is not None else {}


def _number(values: object, positive: bool = False) -> int | None:
    texts = normal(values)
    if not texts:
        return None
    number = _integer(texts[0])
    if number is None or (positive and number <= 0):
        return None
    return number


def _clock(seconds: float) -> str:
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


class Arguments(argparse.Namespace):
    """The command line, typed."""

    folder: str = ""
    dry_run: bool = False
    force: bool = False
    no_recursive: bool = False
    sleep: float = 3.0
    tolerance: float = 5.0
    limit: int | None = None
    no_channel_description: bool = False
    config: str | None = None
    cookies_from_browser: str | None = None
    verbose: bool = False


def cookie_browser(arguments: Arguments) -> str | None:
    """The browser to take cookies from: the option, else General.cookie_browser
    of the configuration file when there is one. The configuration is only read."""
    option = arguments.cookies_from_browser
    if option is not None:
        return None if option.strip().lower() in ("", "none") else option.strip()
    path = arguments.config
    if path is None:
        path = "config.ini" if os.path.exists("config.ini") else None
    if path is None:
        return None
    Config.initialize(path)
    Config.load()  # the file exists, a missing one would be written
    browser = Config.settings["General"].get("cookie_browser")
    return str(browser) if browser else None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m vidl.retag",
        description="Rewrites the tags of the mp4/m4v files of YouTube videos in a "
        + "folder to the format of new ViDL downloads. The video id is taken from "
        + "the tven tag, or from the file name.",
    )
    _ = result.add_argument(
        "folder", help="the folder with the videos (searched recursively)"
    )
    _ = result.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="only show the tags that would change, nothing is written",
    )
    _ = result.add_argument(
        "--force",
        action="store_true",
        help="also retag files that already have the current format",
    )
    _ = result.add_argument(
        "--no-recursive", action="store_true", help="leave out subfolders"
    )
    _ = result.add_argument(
        "--sleep",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="pause between requests to YouTube (default: %(default)s)",
    )
    _ = result.add_argument(
        "--tolerance",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="the largest difference between the duration of the file and of the "
        + "video (or 2%%, whichever is more, default: %(default)s)",
    )
    _ = result.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="stop after N files YouTube was asked about, to try it on a few",
    )
    _ = result.add_argument(
        "--no-channel-description",
        action="store_true",
        help="do not ask for the channel description (sdes), one request per channel",
    )
    _ = result.add_argument(
        "--config",
        metavar="FILE",
        help="the ViDL configuration for the cookie browser (default: config.ini "
        + "if there is one)",
    )
    _ = result.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="the browser to take cookies from, or none (default: as in the "
        + "configuration)",
    )
    _ = result.add_argument(
        "-v", "--verbose", action="store_true", help="show the tags that were changed"
    )
    return result


def main(
    argv: Sequence[str] | None = None,
    source: Callable[[str | None], Source] | None = None,
) -> int:
    """The command line, returns the exit status: 0 when all went well, 1 when
    a file failed or the run was stopped, 2 for wrong arguments, 130 when it was
    interrupted."""
    arguments = parser().parse_args(argv, namespace=Arguments())
    if not os.path.isdir(arguments.folder):
        print(f"ERROR: {arguments.folder} is not a folder", file=sys.stderr)
        return 2
    if arguments.config is not None and not os.path.isfile(arguments.config):
        print(f"ERROR: {arguments.config} does not exist", file=sys.stderr)
        return 2
    if arguments.sleep < 0 or arguments.tolerance < 0:
        print("ERROR: --sleep and --tolerance must not be negative", file=sys.stderr)
        return 2
    if arguments.limit is not None and arguments.limit < 1:
        print("ERROR: --limit must be at least 1", file=sys.stderr)
        return 2
    browser = cookie_browser(arguments)
    youtube = source(browser) if source is not None else YoutubeSource(browser)
    print(
        f"Retagging {arguments.folder}"
        + (" (dry run, nothing is written)" if arguments.dry_run else "")
        + (f", cookies from {browser}" if browser else "")
    )
    retagger = Retagger(
        youtube,
        dry_run=arguments.dry_run,
        force=arguments.force,
        sleep=arguments.sleep,
        tolerance=arguments.tolerance,
        channel_descriptions=not arguments.no_channel_description,
        limit=arguments.limit,
        verbose=arguments.verbose,
    )
    try:
        summary = retagger.run(arguments.folder, recursive=not arguments.no_recursive)
    except KeyboardInterrupt:
        print("Interrupted, no file is half written", file=sys.stderr)
        return 130
    finally:
        close = getattr(youtube, "close", None)
        if callable(close):
            _ = cast("Callable[[], object]", close)()
    parts = [f"{summary.counts[s]} {s.value}" for s in Status if summary.counts[s]]
    print("Done: " + (", ".join(parts) if parts else "no mp4 or m4v files found"))
    if summary.limited:
        print("Stopped at the limit, more files were left")
    if summary.aborted is not None:
        print(f"STOPPED: {summary.aborted}", file=sys.stderr)
        return 1
    return 1 if summary.counts[Status.FAILED] else 0


if __name__ == "__main__":
    sys.exit(main())
