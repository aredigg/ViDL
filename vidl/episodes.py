"""Episode numbers: the first video of a channel is episode 1, the next 2, and
so on, in the order the videos were published.

For a YouTube channel that order is its uploads playlist, which YouTube keeps
for every channel (the id of the channel with "UU" for "UC") with all of its
videos, shorts and streams, newest first. The tabs of a channel (videos,
shorts, live) are separate lists, each numbered from 1 they would give the one
show several episodes with the same number, and a number counted from the
position in a tab is not the one the same video has when it is looked at
again. The uploads list is the same wherever a video is found from.

The videos of any other playlist are numbered in the order of the playlist,
the first is 1: that is the order its maker gave it. Playlists that YouTube
makes of the uploads of a channel (ids starting with "UU") are newest first,
as the uploads. Other sites are not numbered, their order is not known.

A number only changes when a video is removed from the list (the videos after
it get one less), never when a new video is added.

The list is only fetched when a number is asked for, once for each EpisodeIndex.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import Lock
from typing import cast

UPLOADS = "uploads"  # all the uploads of a channel, the numbering above
PLAYLIST = "playlist"  # the order of a playlist
NONE = "none"  # no number, e.g. a video on its own

CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}", re.ASCII)
UPLOADS_ID = re.compile(r"UU[A-Za-z0-9_-]{22}", re.ASCII)
UPLOADS_URL = "https://www.youtube.com/playlist?list="

Lister = Callable[[str], Sequence[str]]


def uploads_url(channel_id: str) -> str | None:
    """The address of the uploads playlist of a channel, from the id of the
    channel (UC...), or None when that is no channel id."""
    if not CHANNEL_ID.fullmatch(channel_id):
        return None
    return UPLOADS_URL + "UU" + channel_id[2:]


def rank(ids: Sequence[str], video_id: str, newest_first: bool) -> int | None:
    """The number of the video in a list of ids, 1 for the first to be
    published, or None when it is not in the list."""
    try:
        position = ids.index(video_id)
    except ValueError:
        return None
    return len(ids) - position if newest_first else position + 1


def ids_of(entries: Iterable[object]) -> list[str]:
    """The ids of playlist entries (dicts of yt-dlp), in order. Entries without
    an id are left out."""
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            video_id = cast("Mapping[str, object]", entry).get("id")
            if isinstance(video_id, str) and video_id:
                ids.append(video_id)
    return ids


def entry_ids(info: Mapping[str, object]) -> list[str]:
    """The ids of the entries of a playlist that yt-dlp extracted without
    processing it, in order. A generator of entries can only be read once."""
    entries = info.get("entries")
    if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes, Mapping)):
        return []
    return ids_of(entries)


class IncompleteList(Exception):
    """The list of a playlist has fewer videos than the playlist says it has,
    so the numbers counted from it would be wrong."""


def complete_ids(info: Mapping[str, object]) -> list[str]:
    """The ids of the entries of a playlist, only when all of them were
    listed: yt-dlp states the number of videos of the playlist
    (playlist_count), and a list with fewer is an interrupted one. Raises
    IncompleteList, or the error that interrupted the listing."""
    ids = entry_ids(info)
    expected = info.get("playlist_count")
    if (
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and expected > len(ids)
    ):
        raise IncompleteList(f"{len(ids)} of {expected} videos were listed")
    return ids


class EpisodeIndex:
    """The episode numbers of the videos of one channel or playlist."""

    def __init__(
        self, loader: Callable[[], Sequence[str]], newest_first: bool, order: str
    ) -> None:
        self.__loader = loader
        self.__newest_first = newest_first
        self.__order = order
        self.__lock = Lock()
        self.__ids: list[str] | None = None
        self.__failed = False

    @property
    def order(self) -> str:
        """UPLOADS or PLAYLIST, written to the file next to the number."""
        return self.__order

    @property
    def size(self) -> int | None:
        """The number of videos, once the list has been loaded."""
        with self.__lock:
            return None if self.__ids is None else len(self.__ids)

    def number(self, video_id: str) -> int | None:
        """The episode number of the video, None when it is not in the list.

        The list is loaded by the first call. When that fails, the error is
        raised by that call only, later calls give None: a list that cannot be
        loaded is not asked for again."""
        with self.__lock:
            if self.__failed:
                return None
            if self.__ids is None:
                try:
                    self.__ids = list(self.__loader())
                except BaseException:
                    self.__failed = True
                    raise
            return rank(self.__ids, video_id, self.__newest_first)


def index_for(
    info: Mapping[str, object], lister: Lister, ids: Sequence[str] | None = None
) -> EpisodeIndex | None:
    """The numbering of the videos of the playlist info that yt-dlp extracted
    without processing it, see the module text. The lister gives the ids of a
    playlist address (only called for the uploads of a channel), ids those of
    the entries of the info itself, by default read from it. None for what is
    not numbered: other sites and anything that is not a playlist."""
    if info.get("_type") != "playlist":
        return None
    key = info.get("extractor_key")
    if not isinstance(key, str) or not key.lower().startswith("youtube"):
        return None
    playlist = info.get("id")
    if not isinstance(playlist, str) or not playlist:
        return None
    if playlist.startswith(("@", "UC")):
        # A channel, or one of its tabs
        channel = info.get("channel_id")
        url = uploads_url(channel) if isinstance(channel, str) else None
        if url is None:
            return None
        return EpisodeIndex(lambda: lister(url), True, UPLOADS)
    own = list(ids) if ids is not None else entry_ids(info)
    if playlist.startswith("UU"):
        order = UPLOADS if UPLOADS_ID.fullmatch(playlist) else PLAYLIST
        return EpisodeIndex(lambda: own, True, order)
    return EpisodeIndex(lambda: own, False, PLAYLIST)
