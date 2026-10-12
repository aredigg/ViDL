"""Background monitor for the header status bar: network state, yt-dlp version
and update availability, and free disk space. Slow checks run in their own
thread and immutable SystemStatus snapshots are published through a callback."""

import importlib.metadata
import json
import os
import re
import shutil
import time
import traceback
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, field, replace
from enum import Enum
from http.client import HTTPException
from threading import Event, Lock, Thread
from typing import Protocol, cast

from yt_dlp import version as yt_dlp_version

from .debug import Debug


class NetworkState(Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


@dataclass(frozen=True)
class DiskStatus:
    label: str
    path: str
    free: int = 0
    total: int = 0
    error: str | None = None

    @property
    def free_ratio(self) -> float | None:
        if self.error is not None or self.total <= 0:
            return None
        return self.free / self.total


@dataclass(frozen=True)
class VersionStatus:
    running: str
    installed: str | None = None
    latest: str | None = None

    @property
    def outdated(self) -> bool:
        return _newer(self.latest, self.running)

    @property
    def restart_required(self) -> bool:
        return _newer(self.installed, self.running)


def _running_version() -> str:
    # Read at call time, the version of the loaded yt-dlp module
    return str(yt_dlp_version.__version__)


def _running_status() -> VersionStatus:
    return VersionStatus(running=_running_version())


@dataclass(frozen=True)
class SystemStatus:
    disks: tuple[DiskStatus, ...] = ()
    network: NetworkState = NetworkState.UNKNOWN
    network_errors: int = 0
    version: VersionStatus = field(default_factory=_running_status)


_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*")


# Only plain dot separated ASCII digits are accepted (as used by yt-dlp
# releases and nightlies), anything else, e.g. "2026.08.19.dev0" or "v2026.08.19",
# is rejected rather than guessed
def parse_version(version: str) -> tuple[int, ...] | None:
    version = version.strip()
    if not _VERSION_PATTERN.fullmatch(version):
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:  # e.g. exceeding the integer string conversion limit
        return None


def _newer(candidate: str | None, reference: str) -> bool:
    if candidate is None:
        return False
    parsed_candidate, parsed_reference = (
        parse_version(candidate),
        parse_version(reference),
    )
    if parsed_candidate is None or parsed_reference is None:
        return False
    return parsed_candidate > parsed_reference


_BYTE_UNITS = "BKMGTPE"


# Compact, 1024 based: one decimal below 10, none from 10 up
def format_bytes(size: int) -> str:
    value = float(max(0, size))
    index = 0
    while index < len(_BYTE_UNITS) - 1 and value >= 1024:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)}B"
    if round(value, 1) < 10:
        return f"{value:.1f}{_BYTE_UNITS[index]}"
    if round(value) < 1024 or index == len(_BYTE_UNITS) - 1:
        return f"{value:.0f}{_BYTE_UNITS[index]}"
    # Rounds up to the next unit, e.g. 1023.6K
    return f"{value / 1024:.1f}{_BYTE_UNITS[index + 1]}"


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:  # noqa: BLE001 - unknown is a valid answer
        return None


class _Response(Protocol):
    def read(self, amt: int, /) -> bytes: ...


class _DiskUsage(Protocol):
    @property
    def total(self) -> int: ...
    @property
    def free(self) -> int: ...


class Monitor:
    DISK_INTERVAL: float = 10.0
    NETWORK_INTERVAL: float = 60.0
    VERSION_INTERVAL: float = 6 * 3600.0
    # A failed update check is retried sooner than VERSION_INTERVAL
    VERSION_RETRY_INTERVAL: float = 600.0
    # The installed version is local and cheap to read, so an upgrade while
    # running is noticed long before the next update check
    INSTALLED_INTERVAL: float = 60.0
    POLL_INTERVAL: float = 0.5
    NETWORK_TIMEOUT: float = 3.0
    NETWORK_ERROR_WINDOW: float = 600.0
    NETWORK_ERROR_DEGRADED: int = 3
    MAX_NETWORK_ERRORS: int = 1000  # bounds the passive error history
    PROBE_URL: str = "https://www.youtube.com/generate_204"
    PROBE_READ_BYTES: int = 64
    PYPI_URL: str = "https://pypi.org/pypi/yt-dlp/json"
    MAX_PYPI_BYTES: int = 32 * 2**20
    READ_CHUNK: int = 64 * 2**10

    def __init__(
        self,
        publish: Callable[[SystemStatus], None],
        paths: Sequence[tuple[str, str]],
        update_check: bool = True,
        network_check: bool = True,
        # The wall clock, as the monotonic clock stops while the computer
        # sleeps (macOS), which would delay checks by that time
        clock: Callable[[], float] = time.time,
        urlopen: Callable[
            ..., AbstractContextManager[_Response]
        ] = urllib.request.urlopen,
        disk_usage: Callable[[str], _DiskUsage] = shutil.disk_usage,
        stat: Callable[[str], os.stat_result] = os.stat,
        installed_version: Callable[[], str | None] = _installed_version,
    ) -> None:
        self.__publish = publish
        self.__paths: tuple[tuple[str, str], ...] = tuple(paths)
        self.__update_check = update_check
        self.__network_check = network_check
        self.__clock = clock
        self.__urlopen = urlopen
        self.__disk_usage = disk_usage
        self.__stat = stat
        self.__installed_version = installed_version
        self.__lock = Lock()
        self.__disks: tuple[DiskStatus, ...] = ()
        self.__probe_state = NetworkState.UNKNOWN
        self.__errors: deque[float] = deque(maxlen=self.MAX_NETWORK_ERRORS)
        self.__version = VersionStatus(running=_running_version())
        self.__snapshot = SystemStatus(version=self.__version)
        self.__version_failed = False
        # Only used by the monitor thread
        self.__published: SystemStatus | None = None
        self.__halt_event = Event()
        # Set by halt() and by passive network reports, wakes the thread early
        self.__wake_event = Event()
        self.__thread: Thread | None = None

    def start(self) -> None:
        if self.__thread is not None:
            return
        self.__thread = Thread(target=self.__run, name="Monitor")
        self.__thread.start()

    def halt(self) -> None:
        self.__halt_event.set()
        self.__wake_event.set()

    def join(self) -> None:
        if self.__thread is not None:
            self.__thread.join()

    def snapshot(self) -> SystemStatus:
        with self.__lock:
            self.__update_locked()
            return self.__snapshot

    # Called by download slots. The snapshot is updated immediately, and the
    # monitor thread is woken up to publish it, so publish is only ever called
    # from the monitor thread and never blocks or re-enters the caller
    def report_network_error(self) -> None:
        with self.__lock:
            self.__errors.append(self.__clock())
            self.__update_locked()
        self.__wake_event.set()

    def report_network_success(self) -> None:
        with self.__lock:
            self.__errors.clear()
            self.__update_locked()
        self.__wake_event.set()

    def check_disks(self) -> tuple[DiskStatus, ...]:
        disks: list[DiskStatus] = []
        devices: dict[int, int] = {}  # device -> index in disks
        for label, path in self.__paths:
            if not path:
                continue
            try:
                existing, device = self.__existing(path)
                usage = self.__disk_usage(existing)
                free, total = int(usage.free), int(usage.total)
            except (OSError, ValueError):  # ValueError: e.g. embedded null byte
                disks.append(DiskStatus(label, path, error="unavailable"))
                continue
            if device in devices:
                index = devices[device]
                disks[index] = replace(
                    disks[index], label=f"{disks[index].label}/{label}"
                )
            else:
                devices[device] = len(disks)
                disks.append(DiskStatus(label, path, free=free, total=total))
        result = tuple(disks)
        with self.__lock:
            self.__disks = result
            self.__update_locked()
        return result

    def check_network(self) -> NetworkState:
        probe = self.__probe() if self.__network_check else NetworkState.UNKNOWN
        with self.__lock:
            self.__probe_state = probe
            self.__update_locked()
            return self.__snapshot.network

    # Never raises. When the update check fails, the previously known latest
    # version is kept, as a failed check says nothing about the latest release
    def check_version(self) -> VersionStatus:
        installed = self.__read_installed()
        latest: str | None = None
        failed = False
        if self.__update_check:
            try:
                latest = self.__fetch_latest()
            except Exception as e:  # noqa: BLE001 - never raise
                Debug.print(
                    -1, f"Monitor: update check failed: {type(e).__name__}: {e}"
                )
            failed = latest is None
        with self.__lock:
            if failed:
                latest = self.__version.latest
            self.__version = VersionStatus(
                running=_running_version(), installed=installed, latest=latest
            )
            self.__version_failed = failed
            self.__update_locked()
            return self.__version

    # The nearest existing path (the output dir may be created later) and its
    # device
    def __existing(self, path: str) -> tuple[str, int]:
        current = os.path.abspath(os.path.expanduser(path))
        while True:
            try:
                return current, self.__stat(current).st_dev
            except (FileNotFoundError, NotADirectoryError):
                parent = os.path.dirname(current)
                if parent == current:
                    raise
                current = parent

    def __probe(self) -> NetworkState:
        request = urllib.request.Request(
            self.PROBE_URL, headers={"User-Agent": self.__user_agent()}, method="GET"
        )
        try:
            with self.__urlopen(request, timeout=self.NETWORK_TIMEOUT) as response:
                _ = response.read(self.PROBE_READ_BYTES)
        except urllib.error.HTTPError as e:
            # The server answered, so the network works
            with suppress(Exception):
                e.close()
            return NetworkState.ONLINE
        except (OSError, HTTPException) as e:  # URLError and timeouts are OSErrors
            Debug.print(-1, f"Monitor: network probe failed: {type(e).__name__}: {e}")
            return NetworkState.OFFLINE
        return NetworkState.ONLINE

    def __read_installed(self) -> str | None:
        try:
            installed = self.__installed_version()
        except Exception:  # noqa: BLE001 - unknown is a valid answer
            return None
        return installed if isinstance(installed, str) else None

    def __check_installed(self) -> None:
        installed = self.__read_installed()
        with self.__lock:
            self.__version = replace(self.__version, installed=installed)
            self.__update_locked()

    def __fetch_latest(self) -> str | None:
        request = urllib.request.Request(
            self.PYPI_URL,
            headers={"Accept": "application/json", "User-Agent": self.__user_agent()},
            method="GET",
        )
        try:
            with self.__urlopen(request, timeout=self.NETWORK_TIMEOUT) as response:
                body = self.__read_limited(response, self.MAX_PYPI_BYTES)
        except urllib.error.HTTPError as e:
            with suppress(Exception):
                e.close()
            raise
        if body is None:
            return None
        data = cast(object, json.loads(body))
        version = _item(_item(data, "info"), "version")
        if isinstance(version, str) and parse_version(version) is not None:
            return version.strip()
        return None

    # None when the body exceeds limit bytes, or the monitor is halted
    def __read_limited(self, response: _Response, limit: int) -> bytes | None:
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            if self.__halt_event.is_set():
                return None
            chunk = response.read(min(self.READ_CHUNK, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > limit:
            Debug.print(-1, f"Monitor: response exceeds {limit} bytes")
            return None
        return b"".join(chunks)

    @staticmethod
    def __user_agent() -> str:
        return f"ViDL (yt-dlp {_running_version()})"

    # Builds the snapshot from the current state, it is only replaced when it
    # has changed, so identity tells whether there is something to publish
    def __update_locked(self) -> None:
        now = self.__clock()
        if self.__errors and self.__errors[-1] > now:
            # The clock was set back, the errors count from now
            self.__errors = deque(
                (min(t, now) for t in self.__errors), maxlen=self.__errors.maxlen
            )
        while self.__errors and now - self.__errors[0] >= self.NETWORK_ERROR_WINDOW:
            _ = self.__errors.popleft()
        network = self.__probe_state
        if (
            network == NetworkState.ONLINE
            and len(self.__errors) >= self.NETWORK_ERROR_DEGRADED
        ):
            network = NetworkState.DEGRADED
        snapshot = SystemStatus(
            disks=self.__disks,
            network=network,
            network_errors=len(self.__errors),
            version=self.__version,
        )
        if snapshot != self.__snapshot:
            self.__snapshot = snapshot

    def __publish_changes(self) -> None:
        if self.__halt_event.is_set():
            return
        snapshot = self.snapshot()
        if snapshot is self.__published:
            return
        self.__published = snapshot
        try:
            self.__publish(snapshot)
        except Exception:  # noqa: BLE001 - keep the monitor alive
            Debug.print(-1, "Monitor: publish failed:\n" + traceback.format_exc())

    def __run_check(self, name: str, check: Callable[[], object]) -> None:
        try:
            _ = check()
        except Exception:  # noqa: BLE001 - keep the monitor alive
            Debug.print(-1, f"Monitor: {name} failed:\n" + traceback.format_exc())

    def __run(self) -> None:
        checks: list[tuple[str, Callable[[], object]]] = [
            ("check_disks", self.check_disks),
            ("check_network", self.check_network),
            ("check_version", self.check_version),
            ("check_installed", self.__check_installed),
        ]
        start = self.__clock()
        # Everything runs at start, except the installed version which the
        # version check has just read
        due = [start, start, start, start + self.INSTALLED_INTERVAL]
        while not self.__halt_event.is_set():
            self.__wake_event.clear()
            for index, (name, check) in enumerate(checks):
                if self.__halt_event.is_set():
                    return
                now = self.__clock()
                # When the clock was set back, a check is never delayed by more
                # than its interval
                due[index] = min(due[index], now + self.__interval(name))
                if now < due[index]:
                    continue
                self.__run_check(name, check)
                due[index] = self.__clock() + self.__interval(name)
                self.__publish_changes()
            # Passive errors expire, and reports may have arrived meanwhile
            self.__publish_changes()
            remaining = min(due) - self.__clock()
            _ = self.__wake_event.wait(min(self.POLL_INTERVAL, max(0.01, remaining)))

    def __interval(self, name: str) -> float:
        match name:
            case "check_disks":
                return self.DISK_INTERVAL
            case "check_network":
                return self.NETWORK_INTERVAL
            case "check_version":
                with self.__lock:
                    failed = self.__version_failed
                return self.VERSION_RETRY_INTERVAL if failed else self.VERSION_INTERVAL
            case _:
                return self.INSTALLED_INTERVAL


def _item(data: object, key: str) -> object:
    if isinstance(data, dict):
        return cast(dict[object, object], data).get(key)
    return None
