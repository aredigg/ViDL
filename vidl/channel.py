import contextlib
import csv
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from queue import Queue
from threading import Event, RLock
from time import monotonic, time
from typing import ClassVar, cast
from urllib.parse import parse_qs, urlsplit

from yt_dlp.utils import DownloadError
from yt_dlp.YoutubeDL import YoutubeDL

from .config import Config
from .cookies import CookieWatch, Refresh
from .debug import Debug
from .episodes import EpisodeIndex, complete_ids, ids_of, index_for
from .error import Error
from .item import Item
from .message import (
    CountMessage,
    CutoffMessage,
    EntityMessage,
    ErrorMessage,
    InfoMessage,
    MediaMessage,
    Message,
    PathMessage,
    SleepMessage,
    WarningMessage,
)
from .metadata import (
    MetadataContext,
    MetadataPostProcessor,
    channel_thumbnail,
)
from .preflight import Preflight, PreflightError, PreflightInterrupted
from .shared import Shared
from .util import Util


class Channel:
    # Pause after a restriction error that did not start a global suspension
    RESTRICTION_PAUSE: int = 5
    ALREADY_ARCHIVED: str = "Already recorded in archive"
    # Longest time between checks of the halt and drain events while sleeping
    SLEEP_STEP: float = 0.5
    # Longest unexpected failure text kept in the channels file
    FAILURE_MAX: int = 200
    # The tabs of a YouTube channel: the group (grouping tag) of their videos,
    # None when the tab is the plain list of videos or has no videos itself
    TAB_GROUPS: ClassVar[dict[str, str | None]] = {
        "videos": None,
        "featured": None,
        "playlists": None,
        "community": None,
        "search": None,
        "shorts": "Shorts",
        "streams": "Live",
        "live": "Live",
        "releases": "Releases",
        "podcasts": "Podcasts",
        "courses": "Courses",
    }

    __lock = RLock()
    __write_archive_lock = RLock()

    def __init__(
        self,
        name: str | None,
        url: str | None,
        last_dl_dte: str | int | None,
        last_at_dte: str | int | None,
        last_error: str | None,
        sub_level: int = 0,
    ) -> None:
        self.__name = name
        self.__url = url
        self.__id: str | None = None
        try:
            self.__last_download_date = int(last_dl_dte or 0)
            self.__last_attempt_date = int(last_at_dte or 0)
        except (ValueError, TypeError):
            self.__last_download_date = 0
            self.__last_attempt_date = 0
        self.__last_error = last_error
        self.__sub_level = sub_level
        self.__active = False
        self.__slot_index: int | None = None
        self.__epoch_cutoff: int = 0
        self.__epoch_cutoff_passed: bool = False
        self.__playlist: bool = False
        # Replaced by the slot's event; the default is never set, it only avoids None checks
        self.__halt_event: Event = Event()
        # The suspension and drain state of all slots, given by the slot
        self.__shared: Shared | None = None
        # Set when a halt or drain stopped the download before it completed
        self.__interrupted: bool = False
        self.__metadata_pp: MetadataPostProcessor | None = None
        # Series and cover of the top level channel, given to sub channels
        self.__metadata: MetadataContext = MetadataContext()
        self.__preflight: Preflight | None = None
        self.__cookies: CookieWatch | None = None
        # Set when the download was refused before it started, see __download
        self.__refused: bool = False
        # The numbering of the videos of the channel or playlist, see episodes.py,
        # given by the playlist to its entries
        self.__episodes: EpisodeIndex | None = None

    def row(self):
        with Channel.__lock:
            return [
                self.__name or "",
                self.__url or "",
                self.__last_download_date or 0,
                self.__last_attempt_date or 0,
                self.__last_error or "",
            ]

    # The order channels are processed in, the smallest first: the channel
    # tried longest ago, never tried ones first. A failing channel is thereby
    # tried again only after all others, whereas ordering by the last download
    # would keep it first for ever, as it never gets a new download date. The
    # download date only separates channels attempted at the same time.
    def queue_key(self) -> tuple[int, int]:
        with Channel.__lock:
            attempt = self.__last_attempt_date or self.__last_download_date or 0
            return attempt, self.__last_download_date or 0

    def get_last_error(self) -> str | None:
        return self.__last_error

    def get_name(self) -> str | None:
        return self.__name

    def get_epoch_cutoff(self) -> tuple[int, bool]:
        return self.__epoch_cutoff, self.__epoch_cutoff_passed

    def is_playlist(self) -> bool:
        return self.__playlist

    def assign_epoch_cutoff(self, cutoff: tuple[int, bool]):
        with Channel.__lock:
            self.__epoch_cutoff = cutoff[0]
            self.__epoch_cutoff_passed = cutoff[1]

    def set_halt_event(self, halt_event: Event):
        self.__halt_event = halt_event

    def set_shared(self, shared: Shared | None):
        self.__shared = shared

    def set_metadata_processor(self, processor: MetadataPostProcessor | None):
        self.__metadata_pp = processor

    def set_preflight(self, preflight: Preflight | None):
        self.__preflight = preflight

    def set_episodes(self, episodes: EpisodeIndex | None):
        self.__episodes = episodes

    def set_cookies(self, cookies: CookieWatch | None):
        self.__cookies = cookies

    def assign_metadata(self, context: MetadataContext):
        self.__metadata = context

    def get_metadata(self) -> MetadataContext:
        return self.__metadata

    # True when the last download was stopped by a halt or drain, it is then
    # neither a success nor an error
    def interrupted(self) -> bool:
        return self.__interrupted

    def set_active(self):
        self.__active = True

    def set_inactive(self):
        self.__active = False

    def active(self):
        return self.__active

    # Errors logged by yt-dlp, stored in the short form
    def report_error(self, message: str):
        self.__set_error(Error(message, self.__known_ids()).display)

    # An unexpected failure, stored as is (bounded) since there is no short form
    def report_failure(self, message: str):
        message = " ".join(message.split())
        if len(message) > Channel.FAILURE_MAX:
            message = message[: Channel.FAILURE_MAX - 1] + "…"
        self.__set_error(message)

    # ----- Inside slot thread

    def download(
        self,
        slot: int,
        processor: YoutubeDL,
        queue: Queue[Message],
        playlist_index: int = 1,
    ):
        if self.__slot_index is not None:
            return False

        self.__slot_index = slot
        self.__interrupted = False
        self.__refused = False
        if self.__sub_level == 0:
            self.__reset_epoch_cutoff()
            # The list of the videos is that of this run, not an earlier one
            self.__episodes = None
        previous_attempt = self.__last_attempt_date
        try:
            self.__set_attempt_date()
            result = self.__extract(processor, queue, playlist_index)
            if result:
                self.__set_download_date()
                self.__set_error(None)
            elif self.__interrupted:
                # It did not complete, so it keeps its place in the queue
                self.__set_attempt_date(previous_attempt)
            elif self.__get_error() is None:
                self.__set_error("Unknown error")
            return result
        finally:
            self.__slot_index = None
            self.__active = False

    def __extract(
        self,
        processor: YoutubeDL,
        queue: Queue[Message],
        playlist_index: int,
    ) -> bool:
        if self.__slot_index is None:
            return False
        if (
            self.__halt_event.is_set()
            or self.__draining()
            or self.__await_suspension(queue)
        ):
            self.__interrupted = True
            return False
        self.__refresh_cookies(processor, queue)
        if self.__url is None or self.__url == "None":
            self.__url = self.__name
        self.__playlist = False
        info: Mapping[str, object] | None = {}
        try:
            if self.__url is not None:
                # Typed as never None, but it is when the id is in the download archive
                info = cast(
                    Mapping[str, object] | None,
                    processor.extract_info(self.__url, download=False, process=False),
                )
        except DownloadError as e:
            self.__handle_error_exception(queue, e)
            return False
        if info is None:
            self.__set_error(Channel.ALREADY_ARCHIVED)
            return False
        if info:
            info = cast(dict[str, object], info)
            self.__name = (
                Item.get_str(info, "title")
                or Item.get_str(info, "channel")
                or Item.get_str(info, "uploader")
                or f"{Item.get_str(info, 'extractor')} ({Item.get_str(info, 'id')})"
            )
            self.__id = Item.get_str(info, "id")
            top_name = Item.get_str(info, "channel") or self.__name
            if self.__sub_level == 0:
                self.__metadata = Channel.__top_metadata(info, top_name)

            queue.put(
                EntityMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    entity=Item.get_entity(
                        info=info,
                        name=top_name if self.__sub_level == 0 else "",
                        index=playlist_index,
                    ),
                )
            )
            queue.put(
                MediaMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    media=Item.get_media(
                        info=Item.requested_format(info),
                    ),
                )
            )
            if Item.get_str(info, "_type") == "playlist":
                # Each playlist has its own cutoff, e.g. the tabs of a channel
                self.__playlist = True
                self.__reset_epoch_cutoff()
                queue.put(
                    InfoMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.CHANNEL,
                        target=self.__name,
                        message=Item.get_str(info, "extractor_key")
                        or Item.get_str(info, "extractor")
                        or "Playlist",
                    )
                )

                group = self.__group(info)
                sub_channels: list[tuple[int, Channel]] = []
                entries = Item.get_lst(info, "entries")
                if self.__episodes is None and Config.settings["Download"].get(
                    "metadata"
                ):
                    # The first playlist numbers the videos of all below it
                    self.__episodes = index_for(
                        info,
                        lambda url: self.__list_ids(processor, url),
                        ids_of(entries),
                    )
                if entries:
                    queue.put(
                        CountMessage(
                            index=self.__slot_index,
                            provider=Message.Provider.CHANNEL,
                            value=len(entries),
                        )
                    )
                    for position, entry in enumerate(
                        cast(list[dict[str, object]], entries), start=1
                    ):
                        if url := Item.get_str(entry, "webpage_url") or Item.get_str(
                            entry, "url"
                        ):
                            sub_channels.append(
                                (
                                    position,
                                    Channel(
                                        url,
                                        None,
                                        None,
                                        None,
                                        None,
                                        sub_level=self.__sub_level + 1,
                                    ),
                                )
                            )
                        if self.__stop_requested():
                            self.__interrupted = True
                            return False

                for position, channel in sub_channels:
                    if self.__epoch_cutoff_passed:
                        continue
                    if self.__stop_requested():
                        self.__interrupted = True
                        break
                    channel.set_active()
                    channel.assign_epoch_cutoff(self.get_epoch_cutoff())
                    channel.set_halt_event(self.__halt_event)
                    channel.set_shared(self.__shared)
                    channel.set_metadata_processor(self.__metadata_pp)
                    channel.set_preflight(self.__preflight)
                    channel.set_cookies(self.__cookies)
                    channel.set_episodes(self.__episodes)
                    channel.assign_metadata(replace(self.__metadata, group=group))
                    _ = channel.download(self.__slot_index, processor, queue, position)
                    # A nested playlist's cutoff does not apply to its siblings
                    if not channel.is_playlist():
                        self.assign_epoch_cutoff(channel.get_epoch_cutoff())
                    if channel.interrupted():
                        self.__interrupted = True
                        break
                if self.__interrupted:
                    # Not completed, so it is neither a download nor an error
                    return False
            else:
                self.__debug(str(info))
                format = Item.enumerate_best_format(info)
                self.__debug(str(format))
                # The video could be extracted, so this slot is not restricted
                self.__report_success()
                if Item.no_vertical(format):
                    self.__record_archive(processor, info)
                    self.__report_status(queue, "Vertical video")
                    return False
                if not Item.high_resolution(format):
                    self.__record_archive(processor, info)
                    self.__report_status(queue, "Low resolution")
                    return False
                if not Item.outside_deferred(info, format):
                    self.__report_status(queue, "Defer low resolution")
                    return False
                if not self.__within_cutoff(info):
                    self.__epoch_cutoff_passed = True
                    self.__report_status(queue, "Outside cutoff")
                    return False
                else:
                    if self.__epoch_cutoff != 0:
                        queue.put(
                            CutoffMessage(
                                index=self.__slot_index,
                                provider=Message.Provider.CHANNEL,
                                value=self.__epoch_cutoff,
                            )
                        )
                path = cast(
                    Callable[[dict[str, object], str], str], processor.prepare_filename
                )
                queue.put(
                    PathMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.DOWNLOAD,
                        path=path(info, "temp"),
                    )
                )
                available_at = Item.get_int(format, "available_at")
                sleep_time = (available_at or time()) - time()
                sleep_time = max(Config.SLEEP_INTERVAL, sleep_time + 1)
                required = sleep_time > Config.SLEEP_INTERVAL
                queue.put(
                    SleepMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.DOWNLOAD,
                        sleep_time=sleep_time,
                        required=required,
                    )
                )
                # yt-dlp waits for available_at in time.sleep, which neither
                # a halt nor a drain can end, so the wait is done here
                if available_at and required and self.__sleep(available_at - time()):
                    self.__interrupted = True
                    return False
                process_time = int(time())
                self.__set_metadata_context(
                    True, self.__prepare_episode(queue, info, playlist_index)
                )
                try:
                    ret = self.__download(processor, queue, info)
                finally:
                    self.__set_metadata_context(False)
                self.__refresh_cookies(processor, queue)
                if self.__refused or self.__interrupted:
                    # Nothing was downloaded, so neither is there a pause
                    return False
                if ret:
                    self.__report_success()
                if self.__stop_requested():
                    # The download is complete, the pause before the next is not needed
                    return ret
                min_sleep = cast(int, Config.settings["Download"]["sleep_interval"])
                cutoff = (
                    cast(int, (Config.settings["Download"]["post_sleep_cutoff"]))
                    * Util.SECONDS_PER_MINUTE
                )
                sleep_time = max(min_sleep, min(cutoff, int(time()) - process_time))
                queue.put(
                    SleepMessage(
                        index=self.__slot_index,
                        provider=Message.Provider.CHANNEL,
                        sleep_time=sleep_time,
                    )
                )
                _ = self.__sleep(sleep_time)
                return ret
        else:
            return False
        return True

    @staticmethod
    def __top_metadata(info: dict[str, object], top_name: str) -> MetadataContext:
        download = Config.settings["Download"]
        thumbnail = None
        if download.get("thumbnail") == "channel":
            thumbnail = channel_thumbnail(info)
        # The description of a playlist or channel, not that of a single video
        description = None
        if Item.get_str(info, "_type") == "playlist":
            description = Item.get_str(info, "description").strip() or None
        return MetadataContext(
            series=(top_name or None) if download.get("metadata") else None,
            thumbnail_url=thumbnail,
            channel_description=description,
        )

    # The grouping of the videos of this playlist: the title of a real
    # playlist, or the name of a channel tab other than the plain videos tab.
    # None for the channel itself, whose entries are the tabs. Only YouTube
    # urls are recognised, other sites have no grouping.
    def __group(self, info: Mapping[str, object]) -> str | None:
        try:
            parts = urlsplit(self.__url or "")
            segment = parts.path.rstrip("/").rsplit("/", 1)[-1].lower()
            if segment in Channel.TAB_GROUPS:
                return Channel.TAB_GROUPS[segment]
            if segment == "playlist" or "list" in parse_qs(parts.query):
                title = " ".join(Item.get_str(info, "title").split())
                return title or None
        except ValueError:
            ...
        return None

    def __set_metadata_context(
        self, active: bool, episode: tuple[int, str] | None = None
    ):
        if self.__metadata_pp is None:
            return
        if not active:
            self.__metadata_pp.set_context(None)
            return
        number, order = episode if episode is not None else (None, None)
        self.__metadata_pp.set_context(
            replace(self.__metadata, episode_number=number, episode_order=order)
        )

    # The number of the video in its channel or playlist (see episodes.py), asked
    # for when the video is about to be downloaded, which is when the list of
    # the uploads of a channel is fetched, once. The panel shows it as well.
    def __prepare_episode(
        self, queue: Queue[Message], info: Mapping[str, object], playlist_index: int
    ) -> tuple[int, str] | None:
        episode = self.__episode_of(Item.get_str(info, "id"))
        if episode is not None and self.__slot_index is not None:
            queue.put(
                EntityMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    entity=Item.get_entity(
                        info=cast(dict[str, object], info),
                        name="",
                        index=playlist_index,
                        episode=episode[0],
                    ),
                )
            )
        return episode

    def __episode_of(self, video_id: str) -> tuple[int, str] | None:
        if self.__episodes is None or not video_id:
            return None
        try:
            number = self.__episodes.number(video_id)
        except Exception as e:  # noqa: BLE001 - no number is better than a wrong one
            self.__debug(f"Episodes: no list ({type(e).__name__}: {e})")
            return None
        if number is None:
            self.__debug(f"Episodes: {video_id} is not in the list")
            return None
        return number, self.__episodes.order

    # The ids of a playlist, newest first as YouTube lists them
    def __list_ids(self, processor: YoutubeDL, url: str) -> list[str]:
        self.__debug(f"Episodes: listing {url}")
        info = cast(
            Mapping[str, object] | None,
            processor.extract_info(url, download=False, process=False),
        )
        ids = complete_ids(info) if info is not None else []
        self.__debug(f"Episodes: {len(ids)} videos")
        return ids

    # When yt-dlp noticed that the cookies were rotated in the browser, the
    # current ones are loaded into the downloader, see vidl/cookies.py
    def __refresh_cookies(self, processor: YoutubeDL, queue: Queue[Message]):
        if self.__cookies is None or self.__slot_index is None:
            return
        text = {
            Refresh.REFRESHED: "Rotated, reloaded from the browser",
            Refresh.UNCHANGED: "Expired, log in again in the browser",
            Refresh.FAILED: "Rotated, reading the browser failed",
        }.get(self.__cookies.refresh(processor))
        if text is not None:
            queue.put(
                WarningMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    target="Cookies",
                    message=text,
                )
            )

    def __draining(self) -> bool:
        return self.__shared is not None and self.__shared.drain_event.is_set()

    def __stop_requested(self) -> bool:
        return self.__halt_event.is_set() or self.__draining()

    # Sleeps, returns True when a halt or drain ended the sleep early.
    # The end is observed on the wall clock: the monotonic clock stops while
    # the computer sleeps (macOS), and would oversleep by that time. The
    # monotonic clock only ends the sleep when the wall clock was set back,
    # as it never runs ahead of real time.
    def __sleep(self, seconds: float) -> bool:
        end = time() + seconds
        monotonic_end = monotonic() + seconds
        while (remaining := min(end - time(), monotonic_end - monotonic())) > 0:
            if self.__halt_event.wait(min(Channel.SLEEP_STEP, remaining)):
                return True
            if self.__draining():
                return True
        return self.__stop_requested()

    # Waits while a global suspension is active, returns True when a halt or
    # drain ended the wait
    def __await_suspension(self, queue: Queue[Message]) -> bool:
        if self.__shared is None:
            return False
        suspension = self.__shared.suspension
        if not suspension.active():
            return False
        if self.__slot_index is not None:
            queue.put(
                SleepMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    sleep_time=suspension.remaining(),
                    required=True,
                )
            )
        return suspension.wait(self.__halt_event, self.__shared.drain_event)

    def __report_success(self):
        if self.__shared is not None:
            if self.__slot_index is not None:
                self.__shared.suspension.report_success(self.__slot_index)
            self.__shared.network_success()

    def __known_ids(self) -> tuple[str, ...]:
        return (self.__id,) if self.__id else ()

    def __debug(self, message: str):
        Debug.print(-1 if self.__slot_index is None else self.__slot_index, message)

    def __download(
        self, processor: YoutubeDL, queue: Queue[Message], info: Mapping[str, object]
    ) -> bool:
        try:
            return (
                processor.download(
                    [
                        Item.get_str(info, "original_url")
                        or Item.get_str(info, "webpage_url")
                    ]
                )
                == 0
            )
        except DownloadError as e:
            self.__handle_error_exception(queue, e)
            return False
        except PreflightInterrupted:
            # A halt or drain came before the download started
            self.__interrupted = True
            self.__discard_temporary(processor, info)
            return False
        except PreflightError as e:
            self.__refuse(processor, queue, info, e)
            return False
        finally:
            if self.__preflight is not None:
                self.__preflight.finish()

    # The download was refused before it started, see vidl/preflight.py. Like
    # a vertical video, a video that is too large is recorded in the archive,
    # so it is not looked at again. The others are tried again later.
    def __refuse(
        self,
        processor: YoutubeDL,
        queue: Queue[Message],
        info: Mapping[str, object],
        error: PreflightError,
    ):
        self.__refused = True
        if error.archive:
            self.__record_archive(processor, cast(dict[str, object], info))
        self.__debug(f"Refused ({error.reason.name}): {error.message}")
        self.__report_status(queue, error.message)
        self.__discard_temporary(processor, info)

    # yt-dlp has written subtitles and thumbnails to the temporary directory by
    # the time the download is refused, they are of no use anymore. Only files
    # of this video (named after its id) inside the temporary directory.
    def __discard_temporary(self, processor: YoutubeDL, info: Mapping[str, object]):
        try:
            prepare = cast(
                Callable[[Mapping[str, object], str], str], processor.prepare_filename
            )
            # The same directory the processor was given as "temp", see Slot
            temp = os.path.realpath(str(Config.settings["Paths"]["temporary"]))
            path = prepare(info, "temp")
            directory = os.path.realpath(os.path.dirname(path))
            stem = os.path.splitext(os.path.basename(path))[0]
            if (
                not stem
                or directory == temp
                or os.path.commonpath((temp, directory)) != temp
            ):
                return
            for name in os.listdir(directory):
                file = os.path.join(directory, name)
                if name.startswith(stem + ".") and os.path.isfile(file):
                    with contextlib.suppress(OSError):
                        os.remove(file)
        except (OSError, KeyError, TypeError, ValueError):
            ...

    def __handle_error_exception(self, queue: Queue[Message], e: DownloadError):
        error = Error.from_exception(e, self.__known_ids())
        self.__debug(f"{error.category.name} ({error.kind.value}): {error.raw}")
        self.__report(queue, error)

    # A yt-dlp error, shown and stored in its short form
    def __report(self, queue: Queue[Message], error: Error):
        if self.__slot_index is not None:
            queue.put(
                ErrorMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    target=error.identity or self.__id or self.__name or "",
                    message=error.summary,
                )
            )
        self.__set_error(error.display)
        if self.__shared is not None and error.category is Error.Category.NETWORK:
            self.__shared.network_error()
        if error.restriction:
            self.__restricted(error)

    # A restriction may be a temporary ban, or just a deleted video. Only when
    # enough slots are restricted all of them suspend, which is waited for
    # before the next extraction. Otherwise this slot pauses briefly.
    def __restricted(self, error: Error):
        if (
            self.__shared is not None
            and self.__slot_index is not None
            and self.__shared.suspension.report_restriction(
                self.__slot_index, error.display
            )
        ):
            return
        _ = self.__sleep(Channel.RESTRICTION_PAUSE)

    # A status decided by ViDL itself, e.g. a skipped video
    def __report_status(self, queue: Queue[Message], message: str):
        if self.__slot_index is not None:
            queue.put(
                ErrorMessage(
                    index=self.__slot_index,
                    provider=Message.Provider.CHANNEL,
                    target=self.__id or self.__name or "",
                    message=message,
                )
            )
        self.__set_error(message)

    def __record_archive(self, processor: YoutubeDL, info: dict[str, object]):
        if processor.params.get("download_archive"):
            with Channel.__write_archive_lock:
                in_download_archive = cast(
                    Callable[[dict[str, object]], bool], processor.in_download_archive
                )
                if not in_download_archive(info):
                    record_download_archive = cast(
                        Callable[[dict[str, object]], None],
                        processor.record_download_archive,
                    )
                    record_download_archive(info)

    def __set_attempt_date(self, value: int | None = None):
        with Channel.__lock:
            self.__last_attempt_date = int(time()) if value is None else value

    def __set_download_date(self):
        with Channel.__lock:
            self.__last_download_date = int(time())

    def __reset_epoch_cutoff(self):
        with Channel.__lock:
            self.__epoch_cutoff = 0
            self.__epoch_cutoff_passed = False

    def __set_error(self, message: str | None):
        with Channel.__lock:
            self.__last_error = message

    def __get_error(self) -> str | None:
        return self.__last_error

    def __set_epoch_cutoff(self, epoch: int, timestamp: int):
        with Channel.__lock:
            if self.__epoch_cutoff == 0:
                self.__epoch_cutoff = timestamp - epoch
            return self.__epoch_cutoff

    def __within_cutoff(self, info: dict[str, object]) -> bool:
        timestamp: int = Item.get_int(info, "timestamp")
        if cutoff := cast(int, Config.settings["Download"]["playlist_cutoff"]):
            return not timestamp or (
                timestamp
                >= self.__set_epoch_cutoff(cutoff * Util.SECONDS_PER_DAY, timestamp)
            )
        return True


class ChannelHelper:
    __header_columns: ClassVar = [
        "#",
        "URL",
        "Last Download",
        "Last Attempt",
        "Last Error Message",
    ]
    header_len: int = len(__header_columns)
    header: str = ";".join(__header_columns) + "\n"

    @staticmethod
    def load_channels(file_name: str) -> list[Channel]:
        channels: list[Channel] = []
        with open(file_name, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter=";"):
                if row and not row[0].lstrip().startswith("#"):
                    # A row may hold only the name, missing columns are padded
                    # and surplus columns (e.g. a trailing ";") are ignored
                    cells = [cell.strip() or None for cell in row]
                    cells = cells[: ChannelHelper.header_len]
                    if not any(cells):
                        continue
                    cells += [None] * (ChannelHelper.header_len - len(cells))
                    channels.append(Channel(*cells, sub_level=0))
        return channels

    @staticmethod
    def save_channels(channels: list[Channel], file_name: str):
        # Write through symlinks, and keep the permissions of the existing file
        target = os.path.realpath(file_name)
        directory = os.path.dirname(target) or "."
        rows = [channel.row() for channel in channels]
        temp: str | None = None
        try:
            fd, temp = tempfile.mkstemp(dir=directory, prefix=".channels-")
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
                writer.writerow(ChannelHelper.__header_columns)
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(target):
                shutil.copymode(target, temp)
            os.replace(temp, target)
        except OSError as e:
            return e
        finally:
            if temp is not None and os.path.exists(temp):
                with contextlib.suppress(OSError):
                    os.remove(temp)
        return None
