"""Checks run just before a download starts: poor sources, huge files and free
disk space, and the coordination of large downloads between slots.

A Preflight post-processor is registered with
processor.add_post_processor(pp, when="before_dl"). Facts relied upon
(yt-dlp 2026.08.19):

- The "before_dl" stage runs in YoutubeDL.process_info (pre_process(info,
  "before_dl")) when the formats are chosen and nothing has been downloaded.
  info["requested_formats"] holds the formats to merge (video + audio) with
  their "filesize"/"filesize_approx"; a single format is the info dict itself.
- pre_process only catches PostProcessingError, so any other exception from
  run() leaves process_info (process_ie_result itself catches nothing). The
  extraction wrapper of extract_info and of playlist entries
  (_handle_extraction_exceptions) re-raises a plain Exception only when
  "ignoreerrors" is false; with it set, the exception is swallowed and
  reported as an error (str(e)). DownloadCancelled is re-raised in every case
  (and download() prints "[info] <message>" for it, then re-raises), so
  PreflightError and PreflightInterrupted are DownloadCancelled subclasses:
  the caller of processor.download() gets exactly these exceptions, never
  wrapped in DownloadError, whatever "ignoreerrors" is.
- The PostProcessor metaclass wraps run() to send "started"/"finished" to the
  postprocessor hooks, which the UI would interpret (the view would stop
  waiting for the download), so, as MetadataPostProcessor, no hooks are
  registered. Construct the PP without a downloader: add_post_processor calls
  set_downloader, which adds the YoutubeDL hooks again.
- pp_key() would be "Prefligh" (the last two characters are stripped), so the
  key is set explicitly.

Order of the gate and the space check: the gate comes first, then the space.
A download waiting for the gate (e.g. while a large download runs alone) then
holds no space reservation, which would only keep other downloads from
starting for nothing, and the space is measured when the download really can
start. A slot waiting for disk space holds the gate in shared mode, which at
most delays a large download; it waits for reservations of downloads that
already run, and those never wait for the gate again, so there is no cycle. An
exclusive download runs alone, so its space check never has to wait.

The slot of a Preflight must call finish() after each download (in a finally),
which releases both the gate and the reservation. run() itself leaves nothing
held when it raises.
"""

import math
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from threading import Event, Lock
from typing import Any, ClassVar, Protocol, cast

from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled

from .monitor import format_bytes

GIB: int = 2**30
MIB: int = 2**20

InfoDict = dict[str, Any]  # pyright: ignore[reportExplicitAny]


def margin(size: int) -> int:
    """5% of the size plus 256 MiB, for container overhead and metadata."""
    return max(0, size) // 20 + 256 * MIB


def temp_space_needed(size: int) -> int:
    """The parts and the merged file exist together while merging, then the
    merged file and yt-dlp's full copy for the metadata step."""
    return 2 * max(0, size) + margin(size)


def output_space_needed(size: int) -> int:
    return max(0, size) + margin(size)


def download_size(info: Mapping[str, object]) -> int | None:
    """The size of a download: the sum over the requested formats (the info
    itself without them) of "filesize", else "filesize_approx". Entries
    without a usable size add 0, None when no entry has one. Zero is not a
    size (extractors use it for unknown), floats are rounded up."""
    try:
        total, found = 0, False
        for entry in _entries(info):
            size = _entry_size(entry)
            if size is not None:
                total += size
                found = True
        return total if found else None
    except Exception:  # noqa: BLE001 - odd data must not break a download
        return None


def _entries(info: Mapping[str, object]) -> list[Mapping[str, object]]:
    requested = info.get("requested_formats")
    if isinstance(requested, (list, tuple)) and requested:
        return [
            cast(Mapping[str, object], entry)
            for entry in cast(list[object], requested)
            if isinstance(entry, Mapping)
        ]
    return [info]


def _entry_size(entry: Mapping[str, object]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        size = _usable_size(entry.get(key))
        if size is not None:
            return size
    return None


def _usable_size(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and math.isfinite(value) and value > 0:
        return math.ceil(value)
    return None


def _is_hls_only(info: Mapping[str, object]) -> bool:
    extractor = info.get("extractor_key")
    if not isinstance(extractor, str):
        return False
    if extractor.split(":")[0].strip().lower() != "youtube":
        return False
    for entry in _entries(info):
        protocol = entry.get("protocol")
        if isinstance(protocol, str) and any(
            part.strip().lower().startswith("m3u8") for part in protocol.split("+")
        ):
            return True
    return False


class PreflightReason(Enum):
    HLS_ONLY = "hls_only"
    TOO_LARGE = "too_large"
    NO_SPACE = "no_space"


# The exceptions are DownloadCancelled subclasses so that yt-dlp lets them
# through unwrapped, see the module documentation
class PreflightError(DownloadCancelled):
    """The download must not start."""

    def __init__(
        self,
        reason: PreflightReason,
        message: str,
        archive: bool = False,
        retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason: PreflightReason = reason
        self.message: str = message  # short, for the panel and the channels file
        self.archive: bool = archive  # record the video in the download archive
        self.retry: bool = retry  # transient, trying again later may succeed


class PreflightInterrupted(DownloadCancelled):
    """A halt or a drain ended a wait or prevented the start, not an error."""

    def __init__(self, message: str = "Interrupted") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class GateStatus:
    exclusive: int | None = None  # slot running alone
    waiting_exclusive: int | None = None  # slot next in line to run alone
    shared: tuple[int, ...] = ()


class Gate:
    """A reader-writer gate for the slots: any number of shared holders, or one
    exclusive holder. An exclusive request waits until no other slot holds the
    gate; while one holds or waits, new shared requests wait (so a large
    download is not starved) and exclusive requests are served one at a time
    in arrival order. A slot asking for exclusive while it holds shared gives
    up the shared hold first (two such slots would otherwise wait for each
    other forever)."""

    # The wait step is Preflight.WAIT_STEP
    def __init__(self) -> None:
        self.__lock = Lock()
        self.__exclusive: int | None = None
        self.__shared: set[int] = set()
        self.__queue: list[int] = []  # exclusive requests in arrival order
        self.__waiting: set[int] = set()  # slots inside acquire, waiting
        self.__cancelled: set[int] = set()  # waiting slots released meanwhile

    def acquire_shared(
        self,
        slot: int,
        halt: Event,
        drain: Event,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.__acquire(slot, False, halt, drain, notify)

    def acquire_exclusive(
        self,
        slot: int,
        halt: Event,
        drain: Event,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.__acquire(slot, True, halt, drain, notify)

    def release(self, slot: int) -> None:
        """Releases whatever the slot holds and ends its wait, if it waits
        in another thread. Does nothing for a slot that holds nothing."""
        with self.__lock:
            if slot in self.__waiting:
                self.__cancelled.add(slot)
            self.__forget(slot)

    def status(self) -> GateStatus:
        with self.__lock:
            return GateStatus(
                exclusive=self.__exclusive,
                waiting_exclusive=self.__queue[0] if self.__queue else None,
                shared=tuple(sorted(self.__shared)),
            )

    def __acquire(
        self,
        slot: int,
        exclusive: bool,
        halt: Event,
        drain: Event,
        notify: Callable[[str], None] | None,
    ) -> None:
        notified = False
        granted = False
        try:
            if halt.is_set() or drain.is_set():
                raise PreflightInterrupted
            while True:
                with self.__lock:
                    if slot in self.__cancelled:
                        raise PreflightInterrupted
                    self.__waiting.add(slot)
                    blocker = self.__try(slot, exclusive)
                    if blocker is None:
                        granted = True
                        self.__waiting.discard(slot)
                        return
                # Outside the lock: notify may do anything, e.g. ask the gate
                if not notified:
                    notified = True
                    _notify(notify, blocker)
                if halt.wait(Preflight.WAIT_STEP) or drain.is_set():
                    raise PreflightInterrupted
        finally:
            if not granted:
                with self.__lock:
                    self.__forget(slot)
                    self.__waiting.discard(slot)
                    self.__cancelled.discard(slot)

    # Called with the lock held. Returns None when the slot holds the gate
    # now, else the text for the wait
    def __try(self, slot: int, exclusive: bool) -> str | None:
        if self.__exclusive == slot:
            return None
        if exclusive:
            self.__shared.discard(slot)
            if slot not in self.__queue:
                self.__queue.append(slot)
            if (
                self.__exclusive is None
                and self.__queue[0] == slot
                and not self.__shared
            ):
                _ = self.__queue.pop(0)
                self.__exclusive = slot
                return None
            if self.__exclusive is None and self.__queue[0] == slot:
                return "Waiting for the other downloads to finish"
            ahead = (
                self.__exclusive if self.__exclusive is not None else self.__queue[0]
            )
            return f"Waiting for the large download in slot {ahead + 1}"
        if slot in self.__shared:
            return None
        if self.__exclusive is None and not self.__queue:
            self.__shared.add(slot)
            return None
        holder = self.__exclusive if self.__exclusive is not None else self.__queue[0]
        return f"Waiting for the large download in slot {holder + 1}"

    # Called with the lock held
    def __forget(self, slot: int) -> None:
        if self.__exclusive == slot:
            self.__exclusive = None
        self.__shared.discard(slot)
        if slot in self.__queue:
            self.__queue.remove(slot)


def _notify(notify: Callable[[str], None] | None, text: str) -> None:
    if notify is None:
        return
    try:
        notify(text)
    except Exception:  # noqa: BLE001, S110 - a failing UI must not break a download
        pass


@dataclass(frozen=True)
class SpaceShortage:
    output: bool  # the output directory is short, else temp
    need: int
    available: int


class SpaceLedger:
    """The disk space the running downloads still need, one reservation per
    slot, for the temp and the output directory."""

    def __init__(self) -> None:
        self.__lock = Lock()
        self.__reservations: dict[int, tuple[int, int]] = {}

    def reserve(self, slot: int, temp_bytes: int, output_bytes: int) -> None:
        with self.__lock:
            self.__reservations[slot] = (max(0, temp_bytes), max(0, output_bytes))

    def reserve_if(
        self,
        slot: int,
        temp_bytes: int,
        output_bytes: int,
        check: Callable[[int, int], SpaceShortage | None],
    ) -> tuple[SpaceShortage | None, int]:
        """Reserves atomically, unless check, given the temp and output bytes
        reserved by the other slots, reports a shortage. Returns the shortage
        (nothing is reserved then) and the number of other reservations at
        the time of the check."""
        with self.__lock:
            temp, output = self.__others(slot)
            others = sum(1 for other in self.__reservations if other != slot)
            shortage = check(temp, output)
            if shortage is None:
                self.__reservations[slot] = (max(0, temp_bytes), max(0, output_bytes))
            return shortage, others

    def release(self, slot: int) -> None:
        with self.__lock:
            _ = self.__reservations.pop(slot, None)

    def reserved_temp(self, exclude: int | None = None) -> int:
        with self.__lock:
            return self.__others(exclude)[0]

    def reserved_output(self, exclude: int | None = None) -> int:
        with self.__lock:
            return self.__others(exclude)[1]

    def active(self, exclude: int | None = None) -> int:
        """The number of reservations of other slots."""
        with self.__lock:
            return sum(1 for slot in self.__reservations if slot != exclude)

    # Called with the lock held
    def __others(self, exclude: int | None) -> tuple[int, int]:
        temp = output = 0
        for slot, (t, o) in self.__reservations.items():
            if slot != exclude:
                temp += t
                output += o
        return temp, output


class _Usage(Protocol):
    @property
    def free(self) -> int: ...


class _Stat(Protocol):
    @property
    def st_dev(self) -> int: ...


def directory_size(path: str) -> int:
    """The sum of the file sizes under a directory, without following
    symlinks. Files that vanish while walking are ignored."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


@dataclass(frozen=True)
class _Measure:
    temp_free: int | None = None  # None: unknown
    temp_written: int = 0  # bytes already in the temp directory
    output_free: int | None = None
    separate_output: bool = False  # the output directory needs its own space


class Preflight(PostProcessor):
    KEY: ClassVar[str] = "Preflight"
    MINIMUM_FREE: ClassVar[int] = GIB  # required when the size is unknown
    WAIT_STEP: ClassVar[float] = 0.5  # seconds between event checks while waiting

    def __init__(
        self,
        slot: int,
        gate: Gate,
        ledger: SpaceLedger,
        halt_event: Event,
        drain_event: Event,
        *,
        maximum_size: int = 0,
        exclusive_size: int = 0,
        temp_dir: str | None = None,
        output_dir: str | None = None,
        notify: Callable[[str], None] | None = None,
        disk_usage: Callable[[str], _Usage] = shutil.disk_usage,
        stat: Callable[[str], _Stat] = os.stat,
        dir_size: Callable[[str], int] = directory_size,
    ) -> None:
        self.__slot = slot
        self.__gate = gate
        self.__ledger = ledger
        self.__halt = halt_event
        self.__drain = drain_event
        self.__maximum_size = maximum_size
        self.__exclusive_size = exclusive_size
        self.__temp_dir = temp_dir
        self.__output_dir = output_dir
        self.__notify = notify
        self.__disk_usage = disk_usage
        self.__stat = stat
        self.__dir_size = dir_size
        self.__lock = Lock()
        # Changed by finish(), so a run in progress in another thread stops
        self.__generation = 0
        self.__warned: set[str] = set()
        super().__init__(None)

    @classmethod
    def pp_key(cls) -> str:  # pyright: ignore[reportImplicitOverride]
        return cls.KEY

    # Without hooks, _hook_progress returns early. Called by __init__ (for
    # report_progress), set_downloader and YoutubeDL.add_postprocessor_hook
    def add_progress_hook(self, ph: Callable[[str], object]) -> None:  # pyright: ignore[reportImplicitOverride]
        pass

    # The typeshed stubs declare run(information: _InfoDict), yt-dlp passes a
    # plain dict positionally
    def run(self, info: InfoDict) -> tuple[list[str], InfoDict]:  # pyright: ignore[reportImplicitOverride, reportIncompatibleMethodOverride]
        """Returns [], info unchanged when the download may start. Raises
        PreflightError to refuse it and PreflightInterrupted when a halt or
        drain prevented the start; nothing is held then. Other failures never
        refuse a download."""
        with self.__lock:
            generation = self.__generation
            self.__warned = set()
        try:
            self.__start(info, generation)
        except (PreflightError, PreflightInterrupted):
            self.__release()
            raise
        except Exception as e:  # noqa: BLE001 - only definite findings refuse
            self.__release()
            self.__warn("run", f"Preflight skipped, {type(e).__name__}: {e}")
        except BaseException:
            self.__release()
            raise
        return [], info

    def finish(self) -> None:
        """Releases the gate and the reservation of the slot, and stops a run
        in progress in another thread. The slot calls it after each download,
        successful or not. Safe to call any time, from any thread."""
        with self.__lock:
            self.__generation += 1
        self.__release()

    def __release(self) -> None:
        self.__gate.release(self.__slot)
        self.__ledger.release(self.__slot)

    def __start(self, info: InfoDict, generation: int) -> None:
        self.__ensure(generation)
        if _is_hls_only(info):
            raise PreflightError(
                PreflightReason.HLS_ONLY, "HLS only, poor quality", False, True
            )
        size = download_size(info)
        if self.__maximum_size > 0 and size is not None and size > self.__maximum_size:
            raise PreflightError(
                PreflightReason.TOO_LARGE,
                f"Too large ({format_bytes(size)})",
                True,
                False,
            )
        if (
            self.__exclusive_size > 0
            and size is not None
            and size >= self.__exclusive_size
        ):
            self.__gate.acquire_exclusive(
                self.__slot, self.__halt, self.__drain, self.__notify
            )
        else:
            self.__gate.acquire_shared(
                self.__slot, self.__halt, self.__drain, self.__notify
            )
        self.__ensure(generation)
        self.__reserve_space(size, generation)
        self.__ensure(generation)

    def __ensure(self, generation: int) -> None:
        if self.__halt.is_set() or self.__drain.is_set():
            raise PreflightInterrupted
        with self.__lock:
            if self.__generation != generation:
                raise PreflightInterrupted("Cancelled")

    # ----- Space

    def __reserve_space(self, size: int | None, generation: int) -> None:
        use_temp = self.__temp_dir is not None
        if not use_temp and self.__output_dir is None:
            return
        # What has to be free, and what is reserved: the size is unknown for
        # no reservation, but a minimum is still required
        if size is None:
            temp_check = output_check = Preflight.MINIMUM_FREE
            temp_hold = output_hold = 0
        else:
            temp_check, temp_hold = temp_space_needed(size), temp_space_needed(size)
            output_check, output_hold = (
                output_space_needed(size),
                output_space_needed(size),
            )
        notified = False
        while True:
            measure = self.__measure()
            if not use_temp:
                temp_hold = 0
            if not measure.separate_output:
                output_hold = 0

            def check(
                reserved_temp: int, reserved_output: int, measure: _Measure = measure
            ) -> SpaceShortage | None:
                if use_temp and measure.temp_free is not None:
                    # The temp directory is ours alone: what is written to it
                    # is no longer free, but part of the reservations (and
                    # leftovers of this video are reused)
                    available = measure.temp_free + measure.temp_written - reserved_temp
                    if available < temp_check:
                        return SpaceShortage(False, temp_check, max(0, available))
                if measure.separate_output and measure.output_free is not None:
                    available = measure.output_free - reserved_output
                    if available < output_check:
                        return SpaceShortage(True, output_check, max(0, available))
                return None

            shortage, others = self.__ledger.reserve_if(
                self.__slot, temp_hold, output_hold, check
            )
            if shortage is None:
                return
            if others == 0:
                where = "output" if shortage.output else "temp"
                need = format_bytes(shortage.need)
                free = format_bytes(shortage.available)
                raise PreflightError(
                    PreflightReason.NO_SPACE,
                    f"No space in {where}: need {need}, free {free}",
                    False,
                    True,
                )
            if not notified:
                notified = True
                _notify(self.__notify, "Waiting for disk space")
            self.__ensure(generation)
            if self.__halt.wait(Preflight.WAIT_STEP):
                raise PreflightInterrupted
            self.__ensure(generation)

    def __measure(self) -> _Measure:
        temp = self.__probe(self.__temp_dir)
        output = self.__probe(self.__output_dir)
        same_device = (
            self.__temp_dir is not None
            and self.__output_dir is not None
            and temp is not None
            and output is not None
            and temp[1] == output[1]
        )
        separate_output = self.__output_dir is not None and not same_device
        temp_free = self.__free(temp)
        written = 0
        if self.__temp_dir is not None and temp_free is not None:
            written = self.__written(self.__temp_dir)
        return _Measure(
            temp_free=temp_free,
            temp_written=written,
            output_free=self.__free(output) if separate_output else None,
            separate_output=separate_output,
        )

    # The nearest existing parent of the path and its device, None when it
    # cannot be told
    def __probe(self, path: str | None) -> tuple[str, int] | None:
        if path is None:
            return None
        try:
            current = os.path.abspath(path)
            while True:
                try:
                    return current, int(self.__stat(current).st_dev)
                except OSError:
                    parent = os.path.dirname(current)
                    if parent == current:
                        return None
                    current = parent
        except Exception:  # noqa: BLE001 - unknown is a valid answer
            return None

    def __free(self, probe: tuple[str, int] | None) -> int | None:
        if probe is None:
            return None
        try:
            free = self.__disk_usage(probe[0]).free
        except Exception as e:  # noqa: BLE001 - unknown free space never refuses
            self.__warn(f"free {probe[0]}", f"Free space of {probe[0]} unknown, {e}")
            return None
        if isinstance(free, int) and not isinstance(free, bool) and free >= 0:  # pyright: ignore[reportUnnecessaryIsInstance]
            return free
        return None

    def __written(self, path: str) -> int:
        try:
            size = self.__dir_size(os.path.abspath(path))
        except Exception as e:  # noqa: BLE001 - the credit is only a bonus
            self.__warn(f"size {path}", f"Size of {path} unknown, {e}")
            return 0
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:  # pyright: ignore[reportUnnecessaryIsInstance]
            return size
        return 0

    # Each message once per run, waits measure again and again
    def __warn(self, key: str, message: str) -> None:
        with self.__lock:
            if key in self.__warned:
                return
            self.__warned.add(key)
        # A failing logger must not break a download either
        try:
            self.report_warning(f"[{self.pp_key()}] {message}")
        except Exception:  # noqa: BLE001, S110
            pass
