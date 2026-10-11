# pyright: standard
import importlib.metadata
import io
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from email.message import Message
from typing import NamedTuple, cast, final
from unittest import mock

from tests.helpers import IsolatedTestCase, wait_until
from vidl.monitor import (
    DiskStatus,
    Monitor,
    NetworkState,
    SystemStatus,
    VersionStatus,
    format_bytes,
    parse_version,
)

RUNNING = "2026.08.19"


class Usage(NamedTuple):
    total: int
    used: int
    free: int


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@final
class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self.body = body
        self.status = status
        self.position = 0
        self.reads: list[int] = []
        self.closed = False
        self.on_read: Callable[[], None] | None = None

    def getcode(self) -> int:
        return self.status

    def read(self, amt: int = -1) -> bytes:
        self.reads.append(amt)
        if self.on_read is not None:
            self.on_read()
        end = len(self.body) if amt < 0 else self.position + amt
        chunk = self.body[self.position : end]
        self.position += len(chunk)
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True


class FakeUrlopen:
    """Maps an URL to a response, an exception to raise, or a callable taking
    the timeout and returning one of these."""

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses: dict[str, object] = dict(responses or {})
        self.calls: list[tuple[urllib.request.Request, float]] = []
        self.lock = threading.Lock()

    def __call__(self, request: urllib.request.Request, timeout: float) -> object:
        with self.lock:
            self.calls.append((request, timeout))
        value = self.responses[request.full_url]
        if callable(value):
            value = cast(Callable[[float], object], value)(timeout)
        if isinstance(value, BaseException):
            raise value
        return value

    def urls(self) -> list[str]:
        with self.lock:
            return [request.full_url for request, _ in self.calls]


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Error", Message(), io.BytesIO(b"body"))


def pypi(version: object) -> FakeResponse:
    return FakeResponse(json.dumps({"info": {"version": version}}).encode())


def stat_result(device: int) -> os.stat_result:
    return os.stat_result((0o40755, 1, device, 1, 0, 0, 0, 0, 0, 0))


class FakeFileSystem:
    """Fake stat and disk_usage over a mapping of path to (device, free, total)."""

    def __init__(self, entries: dict[str, tuple[int, int, int]]) -> None:
        self.entries = entries
        self.errors: dict[str, OSError] = {}
        self.usage_calls: list[str] = []

    def stat(self, path: str) -> os.stat_result:
        if path in self.errors:
            raise self.errors[path]
        if path not in self.entries:
            raise FileNotFoundError(path)
        return stat_result(self.entries[path][0])

    def disk_usage(self, path: str) -> Usage:
        self.usage_calls.append(path)
        _, free, total = self.entries[path]
        return Usage(total, total - free, free)


class MonitorTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("yt_dlp.version.__version__", RUNNING)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = FakeClock()
        self.published: list[SystemStatus] = []
        self.urlopen = FakeUrlopen(
            {
                Monitor.PROBE_URL: FakeResponse(status=204),
                Monitor.PYPI_URL: pypi(RUNNING),
            }
        )
        self.fs = FakeFileSystem({"/data": (1, 50, 100), "/tmp": (2, 10, 40)})

    def monitor(
        self,
        paths: list[tuple[str, str]] | None = None,
        publish: Callable[[SystemStatus], None] | None = None,
        **kwargs,
    ) -> Monitor:
        options = {
            "clock": self.clock,
            "urlopen": self.urlopen,
            "disk_usage": self.fs.disk_usage,
            "stat": self.fs.stat,
            "installed_version": lambda: RUNNING,
        }
        options.update(kwargs)
        if paths is None:
            paths = [("dl", "/data"), ("tmp", "/tmp")]
        return Monitor(publish or self.published.append, paths, **options)


class TestParseVersion(unittest.TestCase):
    def test_table(self):
        cases: list[tuple[str, tuple[int, ...] | None]] = [
            ("2026.08.19", (2026, 8, 19)),
            ("2026.08.19.232045", (2026, 8, 19, 232045)),
            ("  2026.08.19\n", (2026, 8, 19)),
            ("7", (7,)),
            ("", None),
            ("   ", None),
            ("2026.08.19.dev0", None),
            ("v2026.08.19", None),
            ("2026..19", None),
            ("2026.08.", None),
            (".2026", None),
            ("2026-08-19", None),
            ("2026.08.19 1", None),
            ("٢٠٢٦.08.19", None),  # non ASCII digits
            ("1" * 5000, None),  # exceeds the integer conversion limit
        ]
        for version, expected in cases:
            with self.subTest(version=version[:20]):
                self.assertEqual(parse_version(version), expected)


class TestFormatBytes(unittest.TestCase):
    def test_table(self):
        cases: list[tuple[int, str]] = [
            (0, "0B"),
            (1, "1B"),
            (1023, "1023B"),
            (1024, "1.0K"),
            (1536, "1.5K"),
            (10 * 1024 - 1, "10K"),  # 9.999K rounds to no decimals
            (10 * 1024, "10K"),
            (1023 * 1024, "1023K"),
            (1024 * 1024 - 1, "1.0M"),  # 1023.999K rounds up to the next unit
            (412 * 2**30, "412G"),
            (int(1.2 * 2**40), "1.2T"),
            (2**50, "1.0P"),
            (2**60, "1.0E"),
            (2**70, "1024E"),
            (-5, "0B"),
        ]
        for size, expected in cases:
            with self.subTest(size=size):
                self.assertEqual(format_bytes(size), expected)


class TestStatusTypes(unittest.TestCase):
    def test_version_status(self):
        cases: list[tuple[str, str | None, str | None, bool, bool]] = [
            # running, installed, latest, outdated, restart_required
            (RUNNING, None, None, False, False),
            (RUNNING, RUNNING, RUNNING, False, False),
            (RUNNING, RUNNING, "2026.09.01", True, False),
            (RUNNING, "2026.09.01", "2026.09.01", True, True),
            (RUNNING, "2026.07.01", "2026.07.01", False, False),
            (RUNNING, RUNNING, "2026.08.19.000001", True, False),
            ("2026.08.19.232045", None, RUNNING, False, False),
            (RUNNING, "garbage", "garbage", False, False),
            ("garbage", "2026.09.01", "2026.09.01", False, False),
        ]
        for running, installed, latest, outdated, restart in cases:
            with self.subTest(running=running, installed=installed, latest=latest):
                status = VersionStatus(running, installed, latest)
                self.assertEqual(status.outdated, outdated)
                self.assertEqual(status.restart_required, restart)

    def test_free_ratio(self):
        self.assertEqual(DiskStatus("dl", "/d", free=25, total=100).free_ratio, 0.25)
        self.assertIsNone(DiskStatus("dl", "/d").free_ratio)
        self.assertIsNone(
            DiskStatus("dl", "/d", free=1, total=2, error="unavailable").free_ratio
        )

    def test_snapshots_are_immutable_with_running_version(self):
        with mock.patch("yt_dlp.version.__version__", RUNNING):
            status = SystemStatus()
        self.assertEqual(status.version, VersionStatus(running=RUNNING))
        self.assertEqual(status.network, NetworkState.UNKNOWN)
        for name in ("disks", "network", "network_errors", "version"):
            with self.subTest(name=name), self.assertRaises(FrozenInstanceError):
                setattr(status, name, None)
        _ = hash(status)


class TestCheckDisks(MonitorTestCase):
    def test_distinct_devices(self):
        disks = self.monitor().check_disks()
        self.assertEqual(
            disks,
            (
                DiskStatus("dl", "/data", free=50, total=100),
                DiskStatus("tmp", "/tmp", free=10, total=40),
            ),
        )

    def test_same_device_is_merged_in_order(self):
        self.fs.entries.update({"/x": (1, 50, 100), "/y": (3, 1, 2)})
        monitor = self.monitor(
            [("dl", "/data"), ("tmp", "/tmp"), ("x", "/x"), ("y", "/y")]
        )
        self.assertEqual(
            monitor.check_disks(),
            (
                DiskStatus("dl/x", "/data", free=50, total=100),
                DiskStatus("tmp", "/tmp", free=10, total=40),
                DiskStatus("y", "/y", free=1, total=2),
            ),
        )
        self.assertEqual(monitor.snapshot().disks, monitor.check_disks())

    def real(self, path: str) -> Monitor:
        calls: list[str] = []

        def disk_usage(path: str) -> Usage:
            calls.append(path)
            return Usage(100, 75, 25)

        self.usage_calls = calls
        return self.monitor([("dl", path)], stat=os.stat, disk_usage=disk_usage)

    def test_missing_path_walks_up_to_existing_parent(self):
        directory = self.temp_dir()
        missing = os.path.join(directory, "not", "yet")
        (disk,) = self.real(missing).check_disks()
        self.assertEqual(self.usage_calls, [directory])
        self.assertEqual(disk, DiskStatus("dl", missing, free=25, total=100))

    def test_missing_path_with_a_file_parent_walks_up(self):
        directory = self.temp_dir()
        with open(os.path.join(directory, "file"), "w"):
            pass
        missing = os.path.join(directory, "file", "child")
        _ = self.real(missing).check_disks()
        self.assertEqual(self.usage_calls, [os.path.join(directory, "file")])

    def test_errors_make_the_disk_unavailable(self):
        self.fs.errors["/tmp"] = PermissionError("denied")
        self.fs.entries["/broken"] = (5, 0, 0)

        def disk_usage(path: str) -> Usage:
            if path == "/broken":
                raise OSError("io")
            return self.fs.disk_usage(path)

        monitor = self.monitor(
            [("dl", "/data"), ("tmp", "/tmp"), ("b", "/broken"), ("n", "/a\0b")],
            disk_usage=disk_usage,
        )
        self.assertEqual(
            monitor.check_disks(),
            (
                DiskStatus("dl", "/data", free=50, total=100),
                DiskStatus("tmp", "/tmp", error="unavailable"),
                DiskStatus("b", "/broken", error="unavailable"),
                DiskStatus("n", "/a\0b", error="unavailable"),
            ),
        )

    def test_null_byte_with_real_stat(self):
        monitor = self.monitor([("n", "/a\0b")], stat=os.stat)
        self.assertEqual(
            monitor.check_disks(), (DiskStatus("n", "/a\0b", error="unavailable"),)
        )

    def test_empty_paths_are_skipped(self):
        paths = cast(list[tuple[str, str]], [("dl", ""), ("tmp", None), ("x", "/data")])
        self.assertEqual(
            self.monitor(paths).check_disks(),
            (DiskStatus("x", "/data", free=50, total=100),),
        )

    def test_home_is_expanded(self):
        home = self.temp_dir()
        self.fs.entries[os.path.join(home, "videos")] = (7, 3, 9)
        with mock.patch.dict(os.environ, {"HOME": home}):
            disks = self.monitor([("dl", "~/videos")]).check_disks()
        self.assertEqual(disks, (DiskStatus("dl", "~/videos", free=3, total=9),))


class TestCheckNetwork(MonitorTestCase):
    def test_online(self):
        response = FakeResponse(status=204)
        self.urlopen.responses[Monitor.PROBE_URL] = response
        self.assertEqual(self.monitor().check_network(), NetworkState.ONLINE)
        ((request, timeout),) = self.urlopen.calls
        self.assertEqual(request.full_url, Monitor.PROBE_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("User-agent", request.headers)
        self.assertEqual(timeout, Monitor.NETWORK_TIMEOUT)
        self.assertTrue(response.closed)
        self.assertTrue(all(0 < amount <= 64 for amount in response.reads))

    def test_http_error_means_online(self):
        self.urlopen.responses[Monitor.PROBE_URL] = http_error(Monitor.PROBE_URL, 403)
        self.assertEqual(self.monitor().check_network(), NetworkState.ONLINE)

    def test_failures_mean_offline(self):
        for error in (
            urllib.error.URLError("no route"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            OSError("other"),
        ):
            with self.subTest(error=type(error).__name__):
                self.urlopen.responses[Monitor.PROBE_URL] = error
                monitor = self.monitor()
                self.assertEqual(monitor.check_network(), NetworkState.OFFLINE)
                self.assertEqual(monitor.snapshot().network, NetworkState.OFFLINE)

    def test_disabled_never_touches_the_network(self):
        monitor = self.monitor(network_check=False)
        self.assertEqual(monitor.check_network(), NetworkState.UNKNOWN)
        self.assertEqual(self.urlopen.calls, [])


class TestPassiveErrors(MonitorTestCase):
    def test_degraded_within_window(self):
        monitor = self.monitor()
        self.assertEqual(monitor.check_network(), NetworkState.ONLINE)
        monitor.report_network_error()
        self.clock.now += 100
        monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network, NetworkState.ONLINE)
        self.assertEqual(monitor.snapshot().network_errors, 2)
        self.clock.now += 100
        monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network, NetworkState.DEGRADED)
        self.assertEqual(monitor.snapshot().network_errors, 3)
        self.assertEqual(monitor.check_network(), NetworkState.DEGRADED)
        # The first error expires
        self.clock.now += Monitor.NETWORK_ERROR_WINDOW - 200
        self.assertEqual(monitor.snapshot().network, NetworkState.ONLINE)
        self.assertEqual(monitor.snapshot().network_errors, 2)
        self.assertEqual(monitor.check_network(), NetworkState.ONLINE)
        self.clock.now += 200
        self.assertEqual(monitor.snapshot().network_errors, 0)

    def test_success_clears(self):
        monitor = self.monitor()
        _ = monitor.check_network()
        for _ in range(5):
            monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network, NetworkState.DEGRADED)
        monitor.report_network_success()
        self.assertEqual(monitor.snapshot().network, NetworkState.ONLINE)
        self.assertEqual(monitor.snapshot().network_errors, 0)

    def test_only_online_becomes_degraded(self):
        monitor = self.monitor(network_check=False)
        for _ in range(3):
            monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network, NetworkState.UNKNOWN)
        self.assertEqual(monitor.snapshot().network_errors, 3)
        self.urlopen.responses[Monitor.PROBE_URL] = urllib.error.URLError("down")
        monitor = self.monitor()
        _ = monitor.check_network()
        for _ in range(3):
            monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network, NetworkState.OFFLINE)

    def test_history_is_bounded(self):
        monitor = self.monitor()
        for _ in range(Monitor.MAX_NETWORK_ERRORS + 10):
            monitor.report_network_error()
        self.assertEqual(monitor.snapshot().network_errors, Monitor.MAX_NETWORK_ERRORS)


class TestCheckVersion(MonitorTestCase):
    def test_success(self):
        self.urlopen.responses[Monitor.PYPI_URL] = pypi("2026.09.01")
        monitor = self.monitor(installed_version=lambda: "2026.09.01")
        status = monitor.check_version()
        self.assertEqual(status, VersionStatus(RUNNING, "2026.09.01", "2026.09.01"))
        self.assertTrue(status.outdated)
        self.assertTrue(status.restart_required)
        self.assertEqual(monitor.snapshot().version, status)
        ((request, timeout),) = self.urlopen.calls
        self.assertEqual(request.full_url, Monitor.PYPI_URL)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(timeout, Monitor.NETWORK_TIMEOUT)

    def test_disabled_never_touches_the_network(self):
        status = self.monitor(update_check=False).check_version()
        self.assertEqual(status, VersionStatus(RUNNING, RUNNING, None))
        self.assertEqual(self.urlopen.calls, [])

    def test_invalid_responses(self):
        bodies: dict[str, bytes] = {
            "bad json": b"{not json",
            "not utf": b"\xff\xfe\xfa",
            "not an object": b"[1, 2]",
            "no info": json.dumps({"releases": {}}).encode(),
            "info not an object": json.dumps({"info": "2026.09.01"}).encode(),
            "no version": json.dumps({"info": {}}).encode(),
            "deeply nested": b"[" * 100_000,
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                self.urlopen.responses[Monitor.PYPI_URL] = FakeResponse(body)
                self.assertIsNone(self.monitor().check_version().latest)
        for version in (20260901, None, ["2026.09.01"], "2026.09.01.dev0", ""):
            with self.subTest(version=version):
                self.urlopen.responses[Monitor.PYPI_URL] = pypi(version)
                self.assertIsNone(self.monitor().check_version().latest)

    def test_oversized_body(self):
        body = json.dumps({"info": {"version": "2026.09.01"}}).encode()
        with mock.patch.object(Monitor, "READ_CHUNK", 7):
            with mock.patch.object(Monitor, "MAX_PYPI_BYTES", len(body)):
                self.urlopen.responses[Monitor.PYPI_URL] = FakeResponse(body)
                self.assertEqual(self.monitor().check_version().latest, "2026.09.01")
            with mock.patch.object(Monitor, "MAX_PYPI_BYTES", len(body) - 1):
                response = FakeResponse(body)
                self.urlopen.responses[Monitor.PYPI_URL] = response
                self.assertIsNone(self.monitor().check_version().latest)
                self.assertEqual(sum(response.reads), len(body))  # limit + 1
                self.assertTrue(response.closed)

    def test_exceptions_mean_unknown(self):
        for error in (
            urllib.error.URLError("down"),
            http_error(Monitor.PYPI_URL, 503),
            TimeoutError("slow"),
            RuntimeError("unexpected"),
        ):
            with self.subTest(error=type(error).__name__):
                self.urlopen.responses[Monitor.PYPI_URL] = error
                self.assertIsNone(self.monitor().check_version().latest)

    def test_previous_latest_is_kept_on_failure(self):
        self.urlopen.responses[Monitor.PYPI_URL] = pypi("2026.09.01")
        monitor = self.monitor()
        self.assertEqual(monitor.check_version().latest, "2026.09.01")
        self.urlopen.responses[Monitor.PYPI_URL] = urllib.error.URLError("down")
        self.assertEqual(monitor.check_version().latest, "2026.09.01")
        self.urlopen.responses[Monitor.PYPI_URL] = pypi("2026.10.01")
        self.assertEqual(monitor.check_version().latest, "2026.10.01")

    def test_installed_version_failures(self):
        def broken() -> str | None:
            raise RuntimeError("metadata")

        for installed in (broken, lambda: None, lambda: 5):
            with self.subTest(installed=installed):
                status = self.monitor(installed_version=installed).check_version()
                self.assertIsNone(status.installed)
                self.assertFalse(status.restart_required)

    def test_default_installed_version_reads_metadata(self):
        with mock.patch("importlib.metadata.version", return_value="2026.09.01"):
            monitor = Monitor(self.published.append, [], update_check=False)
            self.assertEqual(monitor.check_version().installed, "2026.09.01")
        error = importlib.metadata.PackageNotFoundError("yt-dlp")
        with mock.patch("importlib.metadata.version", side_effect=error):
            monitor = Monitor(self.published.append, [], update_check=False)
            self.assertIsNone(monitor.check_version().installed)

    def test_halt_stops_reading(self):
        monitor = self.monitor()
        response = pypi("2026.09.01")
        response.on_read = monitor.halt
        self.urlopen.responses[Monitor.PYPI_URL] = response
        with mock.patch.object(Monitor, "READ_CHUNK", 4):
            self.assertIsNone(monitor.check_version().latest)
        self.assertEqual(len(response.reads), 1)


class TestThread(MonitorTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Fake clock advances are noticed on the next poll, keep the tests fast
        patcher = mock.patch.object(Monitor, "POLL_INTERVAL", 0.02)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, monitor: Monitor) -> Monitor:
        def stop() -> None:
            monitor.halt()
            monitor.join()

        self.addCleanup(stop)
        monitor.start()
        return monitor

    def published_where(self, condition: Callable[[SystemStatus], bool]) -> bool:
        """Waits until the last published snapshot meets the condition."""
        return wait_until(
            lambda: bool(self.published) and condition(self.published[-1])
        )

    def test_start_publishes_initial_snapshot(self):
        self.urlopen.responses[Monitor.PYPI_URL] = pypi("2026.09.01")
        monitor = self.start(self.monitor())
        expected = SystemStatus(
            disks=(
                DiskStatus("dl", "/data", free=50, total=100),
                DiskStatus("tmp", "/tmp", free=10, total=40),
            ),
            network=NetworkState.ONLINE,
            version=VersionStatus(RUNNING, RUNNING, "2026.09.01"),
        )
        self.assertTrue(self.published_where(lambda status: status == expected))
        self.assertIn("Monitor", [t.name for t in threading.enumerate()])
        self.assertEqual(monitor.snapshot(), expected)
        # Every publish is a change
        for previous, current in zip(self.published, self.published[1:]):
            self.assertNotEqual(previous, current)
        self.assertEqual(self.published[0].disks, expected.disks)

    def test_checks_run_on_their_interval(self):
        self.start(self.monitor())
        self.assertTrue(wait_until(lambda: len(self.urlopen.calls) == 2))
        self.fs.entries["/data"] = (1, 49, 100)
        self.clock.now += Monitor.DISK_INTERVAL
        self.assertTrue(self.published_where(lambda status: status.disks[0].free == 49))
        self.assertEqual(len(self.urlopen.calls), 2)
        self.clock.now += Monitor.NETWORK_INTERVAL
        self.assertTrue(wait_until(lambda: len(self.urlopen.calls) == 3))
        self.assertEqual(self.urlopen.urls()[-1], Monitor.PROBE_URL)

    def test_failed_update_check_is_retried_sooner(self):
        self.urlopen.responses[Monitor.PYPI_URL] = urllib.error.URLError("down")
        self.start(self.monitor(network_check=False))
        self.assertTrue(wait_until(lambda: self.urlopen.urls() == [Monitor.PYPI_URL]))
        self.urlopen.responses[Monitor.PYPI_URL] = pypi("2026.09.01")
        self.clock.now += Monitor.VERSION_RETRY_INTERVAL
        self.assertTrue(
            self.published_where(lambda status: status.version.latest == "2026.09.01")
        )

    def test_installed_version_is_refreshed(self):
        installed = [RUNNING]
        self.start(self.monitor(installed_version=lambda: installed[0]))
        self.assertTrue(wait_until(lambda: len(self.urlopen.calls) == 2))
        installed[0] = "2026.09.01"
        self.clock.now += Monitor.INSTALLED_INTERVAL
        self.assertTrue(
            self.published_where(lambda status: status.version.restart_required)
        )
        self.assertEqual(self.urlopen.urls().count(Monitor.PYPI_URL), 1)

    def test_passive_errors_are_published_without_a_probe(self):
        monitor = self.start(self.monitor())
        self.assertTrue(
            self.published_where(
                lambda status: (
                    status.network == NetworkState.ONLINE
                    and status.version.latest is not None
                )
            )
        )
        for _ in range(Monitor.NETWORK_ERROR_DEGRADED):
            monitor.report_network_error()
        self.assertTrue(
            self.published_where(lambda status: status.network == NetworkState.DEGRADED)
        )
        self.assertEqual(
            self.published[-1].network_errors, Monitor.NETWORK_ERROR_DEGRADED
        )
        monitor.report_network_success()
        self.assertTrue(
            self.published_where(
                lambda status: (
                    status.network == NetworkState.ONLINE and status.network_errors == 0
                )
            )
        )
        self.assertEqual(self.urlopen.urls().count(Monitor.PROBE_URL), 1)

    def test_halt_is_prompt_while_the_network_blocks(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocking(timeout: float) -> object:
            entered.set()
            if release.wait(timeout):
                return FakeResponse()
            return TimeoutError("timed out")

        self.urlopen.responses[Monitor.PROBE_URL] = blocking
        with mock.patch.object(Monitor, "NETWORK_TIMEOUT", 0.3):
            monitor = self.start(self.monitor())
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            monitor.halt()
            monitor.join()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertNotIn("Monitor", [t.name for t in threading.enumerate()])
        self.assertEqual(self.urlopen.urls(), [Monitor.PROBE_URL])  # no update check

    def test_halt_is_prompt_while_idle(self):
        monitor = self.start(self.monitor())
        self.assertTrue(wait_until(lambda: len(self.urlopen.calls) == 2))
        started = time.monotonic()
        monitor.halt()
        monitor.join()
        self.assertLess(time.monotonic() - started, 1.0)

    def test_check_exception_does_not_kill_the_thread(self):
        def broken(path: str) -> Usage:
            raise RuntimeError("unexpected")

        usage: list[Callable[[str], Usage]] = [broken]
        with mock.patch("vidl.monitor.Debug.print") as debug:
            monitor = self.start(self.monitor(disk_usage=lambda path: usage[0](path)))
            self.assertTrue(
                self.published_where(
                    lambda status: status.network == NetworkState.ONLINE
                )
            )
            self.assertTrue(
                any(
                    "check_disks failed" in call.args[1]
                    for call in debug.call_args_list
                )
            )
        usage[0] = self.fs.disk_usage
        self.clock.now += Monitor.DISK_INTERVAL
        self.assertTrue(self.published_where(lambda status: bool(status.disks)))
        self.assertEqual(len(monitor.snapshot().disks), 2)

    def test_publish_exception_does_not_kill_the_thread(self):
        received: list[SystemStatus] = []

        def publish(status: SystemStatus) -> None:
            received.append(status)
            if len(received) == 1:
                raise RuntimeError("view gone")

        with mock.patch("vidl.monitor.Debug.print") as debug:
            self.start(self.monitor(publish=publish))
            self.assertTrue(
                wait_until(
                    lambda: bool(received) and received[-1].version.latest == RUNNING
                )
            )
            self.assertTrue(
                any("publish failed" in call.args[1] for call in debug.call_args_list)
            )
        self.assertGreater(len(received), 1)

    def test_join_without_start_is_safe(self):
        monitor = self.monitor()
        monitor.join()
        monitor.halt()
        monitor.join()

    def test_start_twice_is_harmless(self):
        monitor = self.start(self.monitor())
        monitor.start()
        self.assertTrue(wait_until(lambda: len(self.urlopen.calls) == 2))
        names = [t.name for t in threading.enumerate()]
        self.assertEqual(names.count("Monitor"), 1)
        self.assertEqual(len(self.urlopen.calls), 2)


if __name__ == "__main__":
    unittest.main()
