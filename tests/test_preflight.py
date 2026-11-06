# pyright: standard
import copy
import os
import random
import time
import unittest
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, dataclass
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from yt_dlp import YoutubeDL
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError

from tests.helpers import IsolatedTestCase, wait_until
from vidl.monitor import format_bytes
from vidl.preflight import (
    GIB,
    MIB,
    Gate,
    GateStatus,
    Preflight,
    PreflightError,
    PreflightInterrupted,
    PreflightReason,
    SpaceLedger,
    directory_size,
    download_size,
    margin,
    output_space_needed,
    temp_space_needed,
)

STEP = 0.01  # the polling step of the waits in the tests


def fmt(size: object = None, approx: object = None, **extra: object) -> dict[str, Any]:
    entry: dict[str, Any] = {"format_id": "f", "protocol": "https"}
    if size is not None:
        entry["filesize"] = size
    if approx is not None:
        entry["filesize_approx"] = approx
    entry.update(extra)
    return entry


def info_of(
    *sizes: object, extractor_key: object = "Youtube", **extra: object
) -> dict[str, Any]:
    """An info dict with one requested format per size (None: no size)."""
    info: dict[str, Any] = {
        "id": "abc",
        "extractor_key": extractor_key,
        "requested_formats": [fmt(size) for size in sizes],
    }
    info.update(extra)
    return info


class Task:
    """Runs a function in a thread and keeps its result or exception."""

    def __init__(self, function: Callable[[], object]) -> None:
        self.result: object = None
        self.error: BaseException | None = None
        self.__function = function
        self.thread = Thread(target=self.__run, daemon=True)
        self.thread.start()

    def __run(self) -> None:
        try:
            self.result = self.__function()
        except BaseException as e:  # noqa: BLE001
            self.error = e

    def done(self, timeout: float = 0.0) -> bool:
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def finished(self, timeout: float = 5.0) -> "Task":
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "the thread did not finish"
        return self


class ThreadedTestCase(IsolatedTestCase):
    """Small polling steps, and cleanup of the threads started by a test."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(Preflight, "WAIT_STEP", STEP)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.halt = Event()
        self.drain = Event()
        self.__tasks: list[Task] = []
        self.__events: list[Event] = [self.halt, self.drain]
        self.addCleanup(self.__stop_tasks)

    def event(self) -> Event:
        event = Event()
        self.__events.append(event)
        return event

    def spawn(self, function: Callable[[], object]) -> Task:
        task = Task(function)
        self.__tasks.append(task)
        return task

    def __stop_tasks(self) -> None:
        for event in self.__events:
            event.set()
        for task in self.__tasks:
            task.thread.join(2)
        alive = [task for task in self.__tasks if task.thread.is_alive()]
        if alive:
            self.fail(f"{len(alive)} threads still running")

    def settle(self) -> None:
        """A short pause to show that something does NOT happen."""
        time.sleep(STEP * 6)


class TestSizes(unittest.TestCase):
    def test_download_size_table(self):
        cases: list[tuple[str, Mapping[str, object], int | None]] = [
            ("empty", {}, None),
            ("single info", {"filesize": 100}, 100),
            ("single approx", {"filesize_approx": 200}, 200),
            ("exact wins", {"filesize": 100, "filesize_approx": 200}, 100),
            ("approx when exact is None", {"filesize": None, "filesize_approx": 7}, 7),
            ("zero is unknown", {"filesize": 0}, None),
            ("zero falls back", {"filesize": 0, "filesize_approx": 9}, 9),
            ("float rounds up", {"filesize": 10.2}, 11),
            ("whole float", {"filesize": 10.0}, 10),
            ("tiny float", {"filesize": 0.5}, 1),
            ("nan", {"filesize": float("nan")}, None),
            ("inf", {"filesize": float("inf")}, None),
            ("bool", {"filesize": True}, None),
            ("bool falls back", {"filesize": True, "filesize_approx": 5}, 5),
            ("negative", {"filesize": -5}, None),
            ("string", {"filesize": "1234"}, None),
            ("list", {"filesize": [1]}, None),
            ("sum", info_of(100, 50), 150),
            (
                "sum of exact and approx",
                {"requested_formats": [fmt(1, 99), fmt(None, 4)]},
                5,
            ),
            ("partial", info_of(100, None), 100),
            ("none", info_of(None, None), None),
            ("junk adds nothing", info_of("x", True, -1, 20), 20),
            ("only junk", info_of("x", True, -1), None),
            ("floats sum", info_of(1.5, 2.5), 5),
            ("requested wins over info", {**info_of(5), "filesize": 999}, 5),
            ("empty requested uses info", {"requested_formats": [], "filesize": 8}, 8),
            ("requested is not a list", {"requested_formats": 5, "filesize": 8}, 8),
            ("non mapping entries", {"requested_formats": [1, "x", None, fmt(3)]}, 3),
            ("only non mapping entries", {"requested_formats": [1, "x"]}, None),
            ("tuple", {"requested_formats": (fmt(2), fmt(3))}, 5),
        ]
        for name, info, expected in cases:
            with self.subTest(name):
                self.assertEqual(download_size(info), expected)

    def test_download_size_is_an_int(self):
        size = download_size(info_of(1.5, 2.25))
        self.assertIsInstance(size, int)
        self.assertEqual(size, 2 + 3)

    def test_download_size_never_raises(self):
        class Broken(dict[str, Any]):
            def get(self, key: str, default: object = None) -> Any:
                raise RuntimeError("no access")

        self.assertIsNone(download_size(Broken()))

    def test_margin(self):
        self.assertEqual(margin(0), 256 * MIB)
        self.assertEqual(margin(GIB), GIB // 20 + 256 * MIB)
        self.assertEqual(margin(20 * GIB), GIB + 256 * MIB)
        self.assertEqual(margin(-5), 256 * MIB)
        self.assertIsInstance(margin(12345), int)

    def test_space_needed(self):
        size = 10 * GIB
        self.assertEqual(temp_space_needed(size), 2 * size + margin(size))
        self.assertEqual(output_space_needed(size), size + margin(size))
        self.assertEqual(temp_space_needed(0), 256 * MIB)
        self.assertEqual(output_space_needed(0), 256 * MIB)
        self.assertGreater(temp_space_needed(size), output_space_needed(size))

    def test_constants(self):
        self.assertEqual(GIB, 1024**3)
        self.assertEqual(MIB, 1024**2)
        self.assertEqual(Preflight.MINIMUM_FREE, GIB)
        self.assertEqual(Preflight.WAIT_STEP, 0.5)


class TestError(unittest.TestCase):
    def test_attributes(self):
        error = PreflightError(
            PreflightReason.TOO_LARGE, "Too large (14G)", True, False
        )
        self.assertIs(error.reason, PreflightReason.TOO_LARGE)
        self.assertEqual(error.message, "Too large (14G)")
        self.assertTrue(error.archive)
        self.assertFalse(error.retry)
        self.assertEqual(str(error), "Too large (14G)")
        self.assertEqual(error.msg, "Too large (14G)")

    def test_passes_through_yt_dlp(self):
        # DownloadCancelled is the one exception the extraction wrapper of
        # yt-dlp re-raises whatever "ignoreerrors" is
        self.assertTrue(issubclass(PreflightError, DownloadCancelled))
        self.assertTrue(issubclass(PreflightInterrupted, DownloadCancelled))
        self.assertTrue(issubclass(PreflightError, Exception))
        self.assertFalse(issubclass(PreflightError, DownloadError))
        self.assertFalse(issubclass(PreflightInterrupted, PreflightError))

    def test_interrupted_has_a_message(self):
        self.assertTrue(str(PreflightInterrupted()))


class TestDirectorySize(IsolatedTestCase):
    def test_sums_files_without_following_symlinks(self):
        root = self.temp_dir()
        os.makedirs(os.path.join(root, "a", "b"))
        for name, size in (
            ("x", 10),
            (os.path.join("a", "y"), 20),
            (os.path.join("a", "b", "z"), 5),
        ):
            with open(os.path.join(root, name), "wb") as f:
                _ = f.write(b"0" * size)
        outside = self.temp_dir()
        with open(os.path.join(outside, "big"), "wb") as f:
            _ = f.write(b"0" * 1000)
        os.symlink(outside, os.path.join(root, "link"))
        os.symlink(os.path.join(outside, "big"), os.path.join(root, "linkfile"))
        # A symlink to a file counts as the link, not its target
        self.assertLess(directory_size(root), 1000)
        self.assertGreaterEqual(directory_size(root), 35)

    def test_missing_directory_and_vanishing_files(self):
        self.assertEqual(directory_size(os.path.join(self.temp_dir(), "none")), 0)
        root = self.temp_dir()
        with open(os.path.join(root, "x"), "wb") as f:
            _ = f.write(b"0" * 10)
        with mock.patch("os.lstat", side_effect=FileNotFoundError):
            self.assertEqual(directory_size(root), 0)


class TestSpaceLedger(unittest.TestCase):
    def test_empty(self):
        ledger = SpaceLedger()
        self.assertEqual(ledger.reserved_temp(), 0)
        self.assertEqual(ledger.reserved_output(), 0)
        self.assertEqual(ledger.active(), 0)

    def test_reserve_and_exclude(self):
        ledger = SpaceLedger()
        ledger.reserve(0, 10, 1)
        ledger.reserve(1, 20, 2)
        self.assertEqual(ledger.reserved_temp(), 30)
        self.assertEqual(ledger.reserved_output(), 3)
        self.assertEqual(ledger.active(), 2)
        self.assertEqual(ledger.reserved_temp(exclude=0), 20)
        self.assertEqual(ledger.reserved_output(exclude=0), 2)
        self.assertEqual(ledger.active(exclude=0), 1)
        self.assertEqual(ledger.reserved_temp(exclude=5), 30)
        self.assertEqual(ledger.active(exclude=5), 2)

    def test_reserve_replaces_the_slots_reservation(self):
        ledger = SpaceLedger()
        ledger.reserve(0, 10, 1)
        ledger.reserve(0, 7, 0)
        self.assertEqual(ledger.reserved_temp(), 7)
        self.assertEqual(ledger.reserved_output(), 0)
        self.assertEqual(ledger.active(), 1)

    def test_release_is_idempotent(self):
        ledger = SpaceLedger()
        ledger.reserve(0, 10, 1)
        ledger.release(0)
        ledger.release(0)
        ledger.release(9)
        self.assertEqual(ledger.active(), 0)
        self.assertEqual(ledger.reserved_temp(), 0)

    def test_a_zero_reservation_is_active(self):
        ledger = SpaceLedger()
        ledger.reserve(3, 0, 0)
        self.assertEqual(ledger.active(), 1)
        self.assertEqual(ledger.active(exclude=3), 0)

    def test_negative_amounts_count_as_zero(self):
        ledger = SpaceLedger()
        ledger.reserve(0, -5, -1)
        self.assertEqual(ledger.reserved_temp(), 0)
        self.assertEqual(ledger.reserved_output(), 0)

    def test_reserve_if(self):
        ledger = SpaceLedger()
        ledger.reserve(1, 10, 2)
        seen: list[tuple[int, int]] = []

        def refuse(temp: int, output: int):
            seen.append((temp, output))
            return mock.sentinel.shortage

        shortage, others = ledger.reserve_if(0, 5, 5, refuse)
        self.assertIs(shortage, mock.sentinel.shortage)
        self.assertEqual(others, 1)
        self.assertEqual(seen, [(10, 2)])
        self.assertEqual(ledger.active(exclude=1), 0)  # nothing reserved

        shortage, others = ledger.reserve_if(0, 5, 6, lambda temp, output: None)
        self.assertIsNone(shortage)
        self.assertEqual(others, 1)
        self.assertEqual(ledger.reserved_temp(exclude=1), 5)
        self.assertEqual(ledger.reserved_output(exclude=1), 6)

    def test_thread_safety(self):
        ledger = SpaceLedger()
        errors: list[str] = []

        def worker(slot: int) -> None:
            for i in range(300):
                ledger.reserve(slot, slot + 1, 1)
                if (
                    ledger.reserved_temp(exclude=slot) < 0
                    or ledger.active(exclude=slot) > 7
                ):
                    errors.append("bad value")
                if i % 3 == 0:
                    ledger.release(slot)
            ledger.release(slot)

        threads = [Thread(target=worker, args=(slot,)) for slot in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(ledger.active(), 0)
        self.assertEqual(ledger.reserved_temp(), 0)
        self.assertEqual(ledger.reserved_output(), 0)

    def test_sums_stay_consistent_under_load(self):
        ledger = SpaceLedger()
        for slot in range(4):
            ledger.reserve(slot, 10, 1)
        stop = Event()
        bad: list[int] = []

        def churn() -> None:
            while not stop.is_set():
                ledger.reserve(10, 1000, 100)
                ledger.release(10)

        def read() -> None:
            for _ in range(2000):
                temp = ledger.reserved_temp(exclude=10)
                if temp != 40:
                    bad.append(temp)

        churner = Thread(target=churn)
        reader = Thread(target=read)
        churner.start()
        reader.start()
        reader.join(10)
        stop.set()
        churner.join(10)
        self.assertEqual(bad, [])


class TestGate(ThreadedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.gate = Gate()

    def shared(
        self,
        slot: int,
        halt: Event | None = None,
        drain: Event | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> Task:
        return self.spawn(
            lambda: self.gate.acquire_shared(
                slot, halt or self.halt, drain or self.drain, notify
            )
        )

    def exclusive(
        self,
        slot: int,
        halt: Event | None = None,
        drain: Event | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> Task:
        return self.spawn(
            lambda: self.gate.acquire_exclusive(
                slot, halt or self.halt, drain or self.drain, notify
            )
        )

    def waiting(self) -> tuple[Event, list[str], Callable[[str], None]]:
        """An event set, and the texts recorded, when a wait begins."""
        began = self.event()
        texts: list[str] = []

        def notify(text: str) -> None:
            texts.append(text)
            began.set()

        return began, texts, notify

    def held(self, expected: GateStatus) -> bool:
        return wait_until(lambda: self.gate.status() == expected, 3)

    def test_status_of_an_empty_gate(self):
        self.assertEqual(self.gate.status(), GateStatus(None, None, ()))
        status = self.gate.status()
        with self.assertRaises(FrozenInstanceError):
            cast(Any, status).exclusive = 1

    def test_many_shared_at_once(self):
        for slot in (3, 1, 2, 0):
            self.gate.acquire_shared(slot, self.halt, self.drain)
        self.assertEqual(self.gate.status(), GateStatus(None, None, (0, 1, 2, 3)))
        self.gate.release(1)
        self.assertEqual(self.gate.status().shared, (0, 2, 3))

    def test_exclusive_alone_is_granted_at_once(self):
        self.gate.acquire_exclusive(4, self.halt, self.drain)
        self.assertEqual(self.gate.status(), GateStatus(4, None, ()))
        self.gate.release(4)
        self.assertEqual(self.gate.status(), GateStatus())

    def test_no_notification_without_a_wait(self):
        notify = mock.Mock()
        self.gate.acquire_shared(0, self.halt, self.drain, notify)
        self.gate.acquire_shared(1, self.halt, self.drain, notify)
        self.gate.release(0)
        self.gate.release(1)
        self.gate.acquire_exclusive(2, self.halt, self.drain, notify)
        notify.assert_not_called()

    def test_exclusive_waits_for_shared_holders(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(1, self.halt, self.drain)
        began, texts, notify = self.waiting()
        task = self.exclusive(2, notify=notify)
        self.assertTrue(began.wait(3))
        self.assertEqual(self.gate.status(), GateStatus(None, 2, (0, 1)))
        self.gate.release(0)
        self.settle()
        self.assertFalse(task.done())
        self.assertEqual(self.gate.status(), GateStatus(None, 2, (1,)))
        self.gate.release(1)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)
        self.assertEqual(self.gate.status(), GateStatus(2, None, ()))
        self.assertEqual(len(texts), 1)

    def test_writer_preference(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        writer_began, _, writer_notify = self.waiting()
        writer = self.exclusive(1, notify=writer_notify)
        self.assertTrue(writer_began.wait(3))
        # A new reader must wait behind the waiting writer
        reader_began, reader_texts, reader_notify = self.waiting()
        reader = self.shared(2, notify=reader_notify)
        self.assertTrue(reader_began.wait(3))
        self.assertFalse(reader.done(STEP * 3))
        self.assertEqual(self.gate.status(), GateStatus(None, 1, (0,)))
        self.assertEqual(reader_texts, ["Waiting for the large download in slot 2"])
        self.gate.release(0)
        self.assertTrue(writer.done(3))
        self.assertEqual(self.gate.status(), GateStatus(1, None, ()))
        self.settle()
        self.assertFalse(reader.done())
        self.gate.release(1)
        self.assertTrue(reader.done(3))
        self.assertEqual(self.gate.status(), GateStatus(None, None, (2,)))

    def test_shared_waits_for_the_exclusive_holder_with_the_slot_in_the_text(self):
        self.gate.acquire_exclusive(2, self.halt, self.drain)
        began, texts, notify = self.waiting()
        task = self.shared(0, notify=notify)
        self.assertTrue(began.wait(3))
        self.assertEqual(texts, ["Waiting for the large download in slot 3"])
        self.assertEqual(self.gate.status(), GateStatus(2, None, ()))
        self.gate.release(2)
        self.assertTrue(task.done(3))
        self.assertEqual(self.gate.status(), GateStatus(None, None, (0,)))
        self.assertEqual(len(texts), 1)

    def test_exclusive_requests_are_served_in_arrival_order(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        order: list[int] = []
        lock = Lock()

        def serve(slot: int, notify: Callable[[str], None]) -> None:
            self.gate.acquire_exclusive(slot, self.halt, self.drain, notify)
            with lock:
                order.append(slot)
            self.gate.release(slot)

        tasks: list[Task] = []
        for slot in (3, 1, 4, 2):
            began, _, notify = self.waiting()
            tasks.append(
                self.spawn(lambda slot=slot, notify=notify: serve(slot, notify))
            )
            self.assertTrue(began.wait(3))  # queued before the next arrives
        self.assertEqual(self.gate.status(), GateStatus(0, 3, ()))
        self.gate.release(0)
        for task in tasks:
            self.assertTrue(task.done(3))
            self.assertIsNone(task.error)
        self.assertEqual(order, [3, 1, 4, 2])
        self.assertEqual(self.gate.status(), GateStatus())

    def test_waiting_exclusive_is_the_first_in_line(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        for slot in (5, 6):
            began, _, notify = self.waiting()
            _ = self.exclusive(slot, notify=notify)
            self.assertTrue(began.wait(3))
        self.assertEqual(self.gate.status(), GateStatus(0, 5, ()))

    def test_upgrade_from_shared_alone(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        notify = mock.Mock()
        self.gate.acquire_exclusive(0, self.halt, self.drain, notify)
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))
        notify.assert_not_called()

    def test_upgrade_waits_only_for_the_others(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(1, self.halt, self.drain)
        began, _, notify = self.waiting()
        task = self.exclusive(0, notify=notify)
        self.assertTrue(began.wait(3))
        self.assertFalse(task.done(STEP * 3))
        self.gate.release(1)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))

    def test_two_upgrades_do_not_deadlock(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(1, self.halt, self.drain)
        order: list[int] = []
        lock = Lock()

        def upgrade(slot: int) -> None:
            self.gate.acquire_exclusive(slot, self.halt, self.drain)
            with lock:
                order.append(slot)
            self.gate.release(slot)

        tasks = [self.spawn(lambda slot=slot: upgrade(slot)) for slot in (0, 1)]
        for task in tasks:
            self.assertTrue(task.done(5))
            self.assertIsNone(task.error)
        self.assertEqual(sorted(order), [0, 1])
        self.assertEqual(self.gate.status(), GateStatus())

    def test_holder_asking_again_keeps_what_it_has(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.assertEqual(self.gate.status().shared, (0,))
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))
        self.gate.release(0)
        self.assertEqual(self.gate.status(), GateStatus())

    def test_release_is_idempotent(self):
        self.gate.release(7)  # holds nothing
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(1, self.halt, self.drain)
        self.gate.release(0)
        self.gate.release(0)
        self.assertEqual(self.gate.status().shared, (1,))

    def test_release_of_exclusive_twice(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        self.gate.release(0)
        self.gate.release(0)
        self.gate.acquire_exclusive(1, self.halt, self.drain)
        self.gate.release(0)  # a stale release does not take slot 1's hold
        self.assertEqual(self.gate.status(), GateStatus(1, None, ()))

    def test_halt_ends_a_shared_wait(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        halt = self.event()
        began, _, notify = self.waiting()
        task = self.shared(1, halt=halt, notify=notify)
        self.assertTrue(began.wait(3))
        halt.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))

    def test_drain_ends_an_exclusive_wait(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        drain = self.event()
        began, _, notify = self.waiting()
        task = self.exclusive(1, drain=drain, notify=notify)
        self.assertTrue(began.wait(3))
        self.assertEqual(self.gate.status().waiting_exclusive, 1)
        drain.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        # Not queued anymore: it neither holds nor blocks
        self.assertEqual(self.gate.status(), GateStatus(None, None, (0,)))
        self.gate.acquire_shared(2, self.halt, self.drain)
        self.assertEqual(self.gate.status().shared, (0, 2))

    def test_an_interrupted_upgrade_holds_nothing(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        self.gate.acquire_shared(1, self.halt, self.drain)
        halt = self.event()
        began, _, notify = self.waiting()
        task = self.exclusive(0, halt=halt, notify=notify)
        self.assertTrue(began.wait(3))
        halt.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(None, None, (1,)))

    def test_an_interrupted_waiter_lets_the_next_one_pass(self):
        self.gate.acquire_shared(0, self.halt, self.drain)
        first_halt = self.event()
        began, _, notify = self.waiting()
        first = self.exclusive(1, halt=first_halt, notify=notify)
        self.assertTrue(began.wait(3))
        began2, _, notify2 = self.waiting()
        second = self.exclusive(2, notify=notify2)
        self.assertTrue(began2.wait(3))
        first_halt.set()
        self.assertTrue(first.done(3))
        self.gate.release(0)
        self.assertTrue(second.done(3))
        self.assertIsNone(second.error)
        self.assertEqual(self.gate.status(), GateStatus(2, None, ()))

    def test_events_set_before_the_call_prevent_it(self):
        for kind in (self.gate.acquire_shared, self.gate.acquire_exclusive):
            for which in ("halt", "drain"):
                halt, drain = self.event(), self.event()
                (halt if which == "halt" else drain).set()
                with self.subTest(kind=kind.__name__, event=which):
                    with self.assertRaises(PreflightInterrupted):
                        kind(0, halt, drain)
                    self.assertEqual(self.gate.status(), GateStatus())

    def test_release_from_another_thread_ends_a_wait(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        began, _, notify = self.waiting()
        task = self.exclusive(1, notify=notify)
        self.assertTrue(began.wait(3))
        self.assertEqual(self.gate.status(), GateStatus(0, 1, ()))
        self.gate.release(1)
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))
        # The slot can acquire again afterwards
        self.gate.release(0)
        self.gate.acquire_shared(1, self.halt, self.drain)
        self.assertEqual(self.gate.status(), GateStatus(None, None, (1,)))

    def test_notify_is_called_once_and_outside_the_lock(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        calls: list[str] = []

        def notify(text: str) -> None:
            # Each of these takes the internal lock, so they would deadlock
            # if notify ran under it
            calls.append(text)
            _ = self.gate.status()
            self.gate.release(9)

        task = self.shared(1, notify=notify)
        self.assertTrue(wait_until(lambda: calls != [], 3))
        self.settle()  # several polling steps pass
        self.gate.release(0)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)
        self.assertEqual(len(calls), 1)

    def test_a_failing_notify_does_not_break_the_wait(self):
        self.gate.acquire_exclusive(0, self.halt, self.drain)
        task = self.shared(1, notify=mock.Mock(side_effect=RuntimeError("ui")))
        self.settle()
        self.gate.release(0)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)

    def test_the_invariant_holds_under_stress(self):
        lock = Lock()
        counts = {"shared": 0, "exclusive": 0}
        violations: list[str] = []
        entries = {"shared": 0, "exclusive": 0}

        def enter(kind: str) -> None:
            with lock:
                if kind == "exclusive" and (counts["shared"] or counts["exclusive"]):
                    violations.append(f"exclusive with {counts}")
                if kind == "shared" and counts["exclusive"]:
                    violations.append(f"shared with {counts}")
                counts[kind] += 1
                entries[kind] += 1
                status = self.gate.status()
                if status.exclusive is not None and status.shared:
                    violations.append(f"status {status}")

        def leave(kind: str) -> None:
            with lock:
                counts[kind] -= 1

        def hold(kind: str, pause: float) -> None:
            enter(kind)
            time.sleep(pause)
            leave(kind)

        def worker(slot: int) -> None:
            rng = random.Random(slot)
            for _ in range(25):
                mode = rng.choice(["shared", "shared", "exclusive", "upgrade"])
                pause = rng.random() * 0.001
                if mode == "exclusive":
                    self.gate.acquire_exclusive(slot, self.halt, self.drain)
                    hold("exclusive", pause)
                else:
                    self.gate.acquire_shared(slot, self.halt, self.drain)
                    hold("shared", pause)
                    if mode == "upgrade":
                        self.gate.acquire_exclusive(slot, self.halt, self.drain)
                        hold("exclusive", pause)
                self.gate.release(slot)

        tasks = [self.spawn(lambda slot=slot: worker(slot)) for slot in range(8)]
        for task in tasks:
            self.assertTrue(task.done(30), "stress test stuck")
            self.assertIsNone(task.error)
        self.assertEqual(violations, [])
        self.assertGreater(entries["exclusive"], 0)
        self.assertGreater(entries["shared"], 0)
        self.assertEqual(self.gate.status(), GateStatus())


@dataclass
class FakeDisk:
    """Fake disk_usage and stat: devices maps existing paths to a device
    number, free the free bytes per device."""

    devices: dict[str, int]
    free: dict[int, int]
    written: int = 0  # what dir_size reports

    def __post_init__(self) -> None:
        self.usage_error: Exception | None = None
        self.stat_error: Exception | None = None
        self.size_error: Exception | None = None
        self.sizes: list[str] = []

    def usage(self, path: str) -> SimpleNamespace:
        if self.usage_error is not None:
            raise self.usage_error
        if path not in self.devices:
            raise FileNotFoundError(path)
        return SimpleNamespace(free=self.free[self.devices[path]], total=0, used=0)

    def stat(self, path: str) -> SimpleNamespace:
        if self.stat_error is not None:
            raise self.stat_error
        if path not in self.devices:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_dev=self.devices[path])

    def dir_size(self, path: str) -> int:
        self.sizes.append(path)
        if self.size_error is not None:
            raise self.size_error
        return self.written


class PreflightTestCase(ThreadedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.gate = Gate()
        self.ledger = SpaceLedger()
        self.disk = FakeDisk({"/t": 1, "/o": 2, "/": 1}, {1: 100 * GIB, 2: 100 * GIB})
        self.notes: list[str] = []

    def pp(self, slot: int = 0, **kwargs: Any) -> Preflight:
        options: dict[str, Any] = {
            "disk_usage": self.disk.usage,
            "stat": self.disk.stat,
            "dir_size": self.disk.dir_size,
            "notify": self.notes.append,
        }
        options.update(kwargs)
        halt = options.pop("halt", self.halt)
        drain = options.pop("drain", self.drain)
        pp = Preflight(slot, self.gate, self.ledger, halt, drain, **options)
        self.addCleanup(pp.finish)
        return pp

    def refusal(self, pp: Preflight, info: dict[str, Any]) -> PreflightError:
        with self.assertRaises(PreflightError) as cm:
            _ = pp.run(info)
        return cm.exception

    def nothing_held(self, slot: int = 0) -> None:
        self.assertNotIn(slot, self.gate.status().shared)
        self.assertNotEqual(self.gate.status().exclusive, slot)
        self.assertEqual(
            self.ledger.active(exclude=-1), self.ledger.active(exclude=slot)
        )


class TestRunBasics(PreflightTestCase):
    def test_returns_the_info_unchanged(self):
        pp = self.pp()
        info = info_of(GIB)
        before = copy.deepcopy(info)
        files, result = pp.run(info)
        self.assertEqual(files, [])
        self.assertIs(result, info)
        self.assertEqual(info, before)

    def test_key_and_no_progress_hooks(self):
        pp = self.pp()
        self.assertEqual(Preflight.pp_key(), "Preflight")
        self.assertEqual(pp.pp_key(), "Preflight")
        self.assertEqual(pp.PP_NAME, "Preflight")
        pp.add_progress_hook(lambda data: None)
        self.assertEqual(cast(Any, pp)._progress_hooks, [])

    def test_odd_data_never_raises(self):
        pp = self.pp(
            maximum_size=GIB, exclusive_size=GIB, temp_dir="/t", output_dir="/o"
        )
        odd: list[dict[str, Any]] = [
            {},
            {"requested_formats": "x"},
            {"requested_formats": [None, 5, "x", {"protocol": 5}]},
            {"extractor_key": 5, "protocol": ["m3u8"]},
            {"filesize": "big", "requested_formats": [{"filesize": float("nan")}]},
        ]
        for info in odd:
            with self.subTest(info=info):
                files, result = pp.run(info)
                self.assertEqual(files, [])
                self.assertIs(result, info)
                pp.finish()


class TestHls(PreflightTestCase):
    def hls(self, extractor_key: object, *protocols: str) -> dict[str, Any]:
        return {
            "extractor_key": extractor_key,
            "requested_formats": [fmt(protocol=p) for p in protocols],
        }

    def test_refused_for_youtube(self):
        error = self.refusal(
            self.pp(), self.hls("Youtube", "m3u8_native", "m3u8_native")
        )
        self.assertIs(error.reason, PreflightReason.HLS_ONLY)
        self.assertEqual(error.message, "HLS only, poor quality")
        self.assertFalse(error.archive)
        self.assertTrue(error.retry)

    def test_extractor_key_variants(self):
        for key in (
            "Youtube",
            "youtube",
            "YOUTUBE",
            "youtube:tab",
            "Youtube:Tab",
            " youtube ",
        ):
            with self.subTest(key=key):
                _ = self.refusal(self.pp(), self.hls(key, "m3u8"))

    def test_any_format_counts(self):
        _ = self.refusal(self.pp(), self.hls("Youtube", "https", "m3u8_native"))
        _ = self.refusal(self.pp(), self.hls("Youtube", "m3u8", "https"))

    def test_single_format_info(self):
        info = {"extractor_key": "Youtube", "protocol": "m3u8_native"}
        _ = self.refusal(self.pp(), info)
        info = {"extractor_key": "Youtube", "protocol": "https"}
        _ = self.pp().run(info)

    def test_allowed_cases(self):
        pp = self.pp()
        cases: list[dict[str, Any]] = [
            self.hls("Youtube", "https", "https"),
            self.hls("Youtube", "http_dash_segments"),
            self.hls("Vimeo", "m3u8_native"),
            self.hls("Twitch:Stream", "m3u8"),
            self.hls("YoutubeNotReally", "m3u8"),
            self.hls(None, "m3u8"),
            {"protocol": "m3u8"},
            {"extractor_key": "Youtube"},
        ]
        for info in cases:
            with self.subTest(info=info):
                files, _ = pp.run(info)
                self.assertEqual(files, [])

    def test_nothing_is_held_after_the_refusal(self):
        pp = self.pp(temp_dir="/t")
        _ = self.refusal(pp, self.hls("Youtube", "m3u8"))
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)


class TestTooLarge(PreflightTestCase):
    def test_refused_above_the_limit(self):
        pp = self.pp(maximum_size=10 * GIB)
        error = self.refusal(pp, info_of(8 * GIB, 6 * GIB))
        self.assertIs(error.reason, PreflightReason.TOO_LARGE)
        self.assertEqual(error.message, "Too large (14G)")
        self.assertTrue(error.archive)
        self.assertFalse(error.retry)
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_exactly_at_the_limit_is_allowed(self):
        pp = self.pp(maximum_size=10 * GIB)
        files, _ = pp.run(info_of(6 * GIB, 4 * GIB))
        self.assertEqual(files, [])
        pp.finish()
        _ = self.refusal(pp, info_of(6 * GIB, 4 * GIB + 1))

    def test_unknown_size_is_allowed(self):
        pp = self.pp(maximum_size=GIB)
        files, _ = pp.run(info_of(None, None))
        self.assertEqual(files, [])
        files, _ = pp.run({"requested_formats": []})
        self.assertEqual(files, [])

    def test_disabled_with_zero(self):
        pp = self.pp(maximum_size=0)
        files, _ = pp.run(info_of(10000 * GIB))
        self.assertEqual(files, [])

    def test_single_format_info_and_approximate_size(self):
        pp = self.pp(maximum_size=GIB)
        _ = self.refusal(pp, {"filesize_approx": 2 * GIB})

    def test_the_message_is_short(self):
        pp = self.pp(maximum_size=1)
        for size in (2, 1500, 5 * MIB, 1023 * GIB, 5000 * 1024 * GIB):
            with self.subTest(size=size):
                self.assertLessEqual(len(self.refusal(pp, info_of(size)).message), 60)

    def test_checked_before_hls_is_not_needed_but_after_it(self):
        pp = self.pp(maximum_size=1)
        info = {
            "extractor_key": "Youtube",
            "requested_formats": [fmt(100, protocol="m3u8")],
        }
        self.assertIs(self.refusal(pp, info).reason, PreflightReason.HLS_ONLY)


class TestSpace(PreflightTestCase):
    def test_nothing_is_checked_without_directories(self):
        self.disk.free = {1: 0, 2: 0}
        pp = self.pp()
        files, _ = pp.run(info_of(50 * GIB))
        self.assertEqual(files, [])
        self.assertEqual(self.ledger.active(), 0)  # nothing reserved either

    def test_enough_space_reserves(self):
        size = 5 * GIB
        pp = self.pp(temp_dir="/t", output_dir="/o")
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(size))
        self.assertEqual(self.ledger.reserved_output(), output_space_needed(size))
        self.assertEqual(self.gate.status().shared, (0,))
        pp.finish()
        self.assertEqual(self.ledger.active(), 0)
        self.assertEqual(self.gate.status(), GateStatus())

    def test_short_with_nothing_active_is_refused(self):
        self.disk.free[1] = 8 * GIB
        pp = self.pp(temp_dir="/t")
        error = self.refusal(pp, info_of(int(5.5 * GIB)))
        self.assertIs(error.reason, PreflightReason.NO_SPACE)
        self.assertEqual(error.message, "No space in temp: need 12G, free 8.0G")
        self.assertFalse(error.archive)
        self.assertTrue(error.retry)
        self.assertEqual(self.notes, [])  # nothing to wait for
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_the_need_is_in_the_message(self):
        self.disk.free[1] = GIB
        size = 20 * GIB
        error = self.refusal(self.pp(temp_dir="/t"), info_of(size))
        self.assertEqual(
            error.message,
            f"No space in temp: need {format_bytes(temp_space_needed(size))}, free 1.0G",
        )
        self.assertLessEqual(len(error.message), 60)

    def test_exactly_enough_is_enough(self):
        size = 5 * GIB
        self.disk.free[1] = temp_space_needed(size)
        pp = self.pp(temp_dir="/t")
        _ = pp.run(info_of(size))
        pp.finish()
        self.disk.free[1] = temp_space_needed(size) - 1
        _ = self.refusal(pp, info_of(size))

    def test_the_gate_is_released_when_the_space_is_short(self):
        self.disk.free[1] = 0
        pp = self.pp(temp_dir="/t")
        _ = self.refusal(pp, info_of(GIB))
        self.assertEqual(self.gate.status(), GateStatus())

    def test_short_with_another_reservation_waits_and_proceeds(self):
        size = 6 * GIB
        self.disk.free[1] = 20 * GIB
        other = self.pp(1, temp_dir="/t")
        _ = other.run(info_of(size))  # reserves 12.5G of the 20G
        pp = self.pp(0, temp_dir="/t")
        task = self.spawn(lambda: pp.run(info_of(size)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        self.settle()
        self.assertFalse(task.done())
        self.assertEqual(self.notes, ["Waiting for disk space"])
        # Not reserved yet, and not holding anything the other cannot release
        self.assertEqual(self.ledger.reserved_temp(exclude=1), 0)
        other.finish()
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)
        self.assertEqual(self.notes, ["Waiting for disk space"])  # once
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(size))

    def test_still_short_when_nothing_else_is_active_is_refused(self):
        size = 6 * GIB
        self.disk.free[1] = 10 * GIB  # not enough even alone
        other = self.pp(1)
        self.ledger.reserve(1, 1, 0)  # an active download
        pp = self.pp(0, temp_dir="/t")
        task = self.spawn(lambda: pp.run(info_of(size)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        other.finish()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightError)
        assert isinstance(task.error, PreflightError)
        self.assertIs(task.error.reason, PreflightReason.NO_SPACE)
        self.assertEqual(self.ledger.active(), 0)
        self.assertEqual(self.gate.status(), GateStatus())

    def test_halt_ends_the_wait_for_space(self):
        self.disk.free[1] = 10 * GIB
        self.ledger.reserve(1, 9 * GIB, 0)
        halt = self.event()
        pp = self.pp(0, temp_dir="/t", halt=halt)
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        halt.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(exclude=1), 0)

    def test_drain_ends_the_wait_for_space(self):
        self.disk.free[1] = 10 * GIB
        self.ledger.reserve(1, 9 * GIB, 0)
        drain = self.event()
        pp = self.pp(0, temp_dir="/t", drain=drain)
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        drain.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(exclude=1), 0)

    def test_the_other_reservation_is_subtracted(self):
        size = GIB
        need = temp_space_needed(size)
        self.disk.free[1] = need + 5 * GIB
        self.ledger.reserve(1, 5 * GIB, 0)
        pp = self.pp(0, temp_dir="/t")
        _ = pp.run(info_of(size))  # exactly fits
        pp.finish()
        self.assertEqual(self.notes, [])
        # One byte more reserved by the other, and the slot has to wait
        self.ledger.reserve(1, 5 * GIB + 1, 0)
        task = self.spawn(lambda: pp.run(info_of(size)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        self.ledger.release(1)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)

    def test_own_reservation_is_not_counted_against_itself(self):
        size = GIB
        self.disk.free[1] = temp_space_needed(size)
        pp = self.pp(0, temp_dir="/t")
        _ = pp.run(info_of(size))
        _ = pp.run(info_of(size))  # a second video, finish() not called
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(size))

    def test_bytes_already_in_temp_are_credited(self):
        size = 5 * GIB
        self.disk.free[1] = 8 * GIB
        self.disk.written = 5 * GIB  # 13G with the bytes already there
        pp = self.pp(temp_dir="/t")
        _ = pp.run(info_of(size))  # needs 10.5G
        self.assertEqual(self.disk.sizes[0], "/t")
        pp.finish()
        self.disk.written = 0
        error = self.refusal(pp, info_of(size))
        self.assertIs(error.reason, PreflightReason.NO_SPACE)

    def test_the_credit_is_taken_as_written_not_capped(self):
        size = GIB
        self.disk.free[1] = 0
        self.disk.written = temp_space_needed(size)
        _ = self.pp(temp_dir="/t").run(info_of(size))

    def test_unknown_size_requires_the_minimum_free(self):
        self.disk.free[1] = GIB - 100 * MIB
        pp = self.pp(temp_dir="/t")
        error = self.refusal(pp, info_of(None))
        self.assertIs(error.reason, PreflightReason.NO_SPACE)
        self.assertEqual(error.message, "No space in temp: need 1.0G, free 924M")
        self.disk.free[1] = GIB
        _ = pp.run(info_of(None))
        # Nothing to reserve, but the slot is active
        self.assertEqual(self.ledger.reserved_temp(), 0)
        self.assertEqual(self.ledger.reserved_output(), 0)
        self.assertEqual(self.ledger.active(), 1)
        pp.finish()
        self.assertEqual(self.ledger.active(), 0)

    def test_unknown_size_waits_like_a_known_one(self):
        self.disk.free[1] = GIB + 100
        self.ledger.reserve(1, 200, 0)
        pp = self.pp(0, temp_dir="/t")
        task = self.spawn(lambda: pp.run(info_of(None)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        self.ledger.release(1)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)

    # ----- Output directory

    def test_output_on_the_same_device_is_not_counted_separately(self):
        size = 5 * GIB
        self.disk.devices["/o"] = 1
        self.disk.free[1] = temp_space_needed(size)
        pp = self.pp(temp_dir="/t", output_dir="/o")
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(size))
        self.assertEqual(self.ledger.reserved_output(), 0)

    def test_output_on_another_device_is_checked(self):
        size = 5 * GIB
        self.disk.free[2] = 3 * GIB
        pp = self.pp(temp_dir="/t", output_dir="/o")
        error = self.refusal(pp, info_of(size))
        self.assertIs(error.reason, PreflightReason.NO_SPACE)
        need = format_bytes(output_space_needed(size))
        self.assertEqual(error.message, f"No space in output: need {need}, free 3.0G")
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_output_enough_on_another_device(self):
        size = 5 * GIB
        self.disk.free[2] = output_space_needed(size)
        pp = self.pp(temp_dir="/t", output_dir="/o")
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_output(), output_space_needed(size))

    def test_temp_is_checked_before_output(self):
        self.disk.free = {1: 0, 2: 0}
        error = self.refusal(self.pp(temp_dir="/t", output_dir="/o"), info_of(GIB))
        self.assertIn("in temp", error.message)

    def test_output_only(self):
        size = 5 * GIB
        self.disk.free[2] = 1 * GIB
        error = self.refusal(self.pp(output_dir="/o"), info_of(size))
        self.assertIn("in output", error.message)
        self.disk.free[2] = 50 * GIB
        pp = self.pp(output_dir="/o")
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_temp(), 0)
        self.assertEqual(self.ledger.reserved_output(), output_space_needed(size))

    def test_the_other_output_reservation_is_subtracted(self):
        size = 5 * GIB
        self.disk.free[2] = output_space_needed(size) + 100
        self.ledger.reserve(1, 0, 101)
        pp = self.pp(output_dir="/o")
        task = self.spawn(lambda: pp.run(info_of(size)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        self.ledger.release(1)
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)

    def test_missing_directories_use_the_nearest_existing_parent(self):
        size = GIB
        self.disk.devices["/o"] = 1  # the same device as /t
        pp = self.pp(temp_dir="/t/a/b", output_dir="/o/new/dir/deeper")
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_output(), 0)  # same device
        pp.finish()
        self.disk.devices["/o"] = 2
        _ = pp.run(info_of(size))
        self.assertEqual(self.ledger.reserved_output(), output_space_needed(size))
        pp.finish()
        # The free space is asked of the existing parent
        self.disk.free[1] = 0
        error = self.refusal(pp, info_of(size))
        self.assertIn("in temp", error.message)

    def test_relative_and_missing_everything(self):
        # No existing parent at all (not even the root): unknown, not refused
        disk = FakeDisk({}, {})
        pp = self.pp(
            temp_dir="/t", output_dir="/o", stat=disk.stat, disk_usage=disk.usage
        )
        _ = pp.run(info_of(GIB))
        self.assertEqual(self.ledger.active(), 1)


class TestMeasuringErrors(PreflightTestCase):
    def test_disk_usage_errors_never_refuse(self):
        for error in (OSError("boom"), PermissionError("no"), ValueError("odd")):
            with self.subTest(error=error):
                self.disk.usage_error = error
                pp = self.pp(temp_dir="/t", output_dir="/o")
                files, _ = pp.run(info_of(50 * GIB))
                self.assertEqual(files, [])
                pp.finish()

    def test_stat_errors_never_refuse(self):
        self.disk.free = {1: 0, 2: 0}
        self.disk.stat_error = OSError("boom")
        pp = self.pp(temp_dir="/t", output_dir="/o")
        files, _ = pp.run(info_of(50 * GIB))
        self.assertEqual(files, [])

    def test_stat_errors_for_one_directory_still_check_the_other(self):
        class OneFails(FakeDisk):
            def stat(self, path: str) -> SimpleNamespace:
                if path == "/o":
                    raise OSError("boom")
                return super().stat(path)

        disk = OneFails({"/t": 1, "/o": 2, "/": 1}, {1: 100 * GIB, 2: 100 * GIB})
        pp = self.pp(
            temp_dir="/t", output_dir="/o", stat=disk.stat, disk_usage=disk.usage
        )
        _ = pp.run(info_of(GIB))
        # /o and then its parent / are measured: the same device as /t, so
        # nothing is reserved for the output
        self.assertEqual(self.ledger.reserved_output(), 0)
        pp.finish()
        disk.free[1] = 0
        error = self.refusal(pp, info_of(GIB))
        self.assertIn("in temp", error.message)

    def test_dir_size_errors_never_refuse(self):
        self.disk.size_error = OSError("boom")
        pp = self.pp(temp_dir="/t")
        _ = pp.run(info_of(GIB))
        self.disk.size_error = ValueError("odd")
        _ = pp.run(info_of(GIB))

    def test_a_failing_dir_size_only_loses_the_credit(self):
        self.disk.free[1] = 1 * GIB
        self.disk.size_error = OSError("boom")
        error = self.refusal(self.pp(temp_dir="/t"), info_of(5 * GIB))
        self.assertIs(error.reason, PreflightReason.NO_SPACE)

    def test_odd_results_never_refuse(self):
        for value in (None, "x", -5, True, 2.5):
            with self.subTest(value=value):
                pp = self.pp(
                    temp_dir="/t",
                    disk_usage=lambda path, value=value: SimpleNamespace(free=value),
                    dir_size=lambda path, value=value: value,
                )
                _ = pp.run(info_of(GIB))
                pp.finish()

    def test_a_failing_logger_is_harmless(self):
        self.disk.usage_error = OSError("boom")
        pp = self.pp(temp_dir="/t")
        with mock.patch.object(pp, "report_warning", side_effect=RuntimeError("log")):
            _ = pp.run(info_of(GIB))

    def test_warnings_are_reported_once_per_run(self):
        self.disk.usage_error = OSError("boom")
        pp = self.pp(temp_dir="/t", output_dir="/o")
        with mock.patch.object(pp, "report_warning") as warn:
            _ = pp.run(info_of(GIB))
        self.assertGreaterEqual(warn.call_count, 1)
        self.assertTrue(
            all("Preflight" in call.args[0] for call in warn.call_args_list)
        )

    def test_unexpected_errors_release_everything_and_do_not_refuse(self):
        pp = self.pp(temp_dir="/t")
        with mock.patch.object(Gate, "acquire_shared", side_effect=RuntimeError("bug")):
            files, _ = pp.run(info_of(GIB))
        self.assertEqual(files, [])
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)


class TestFinish(PreflightTestCase):
    def test_finish_releases_gate_and_reservation(self):
        pp = self.pp(temp_dir="/t")
        _ = pp.run(info_of(GIB))
        self.assertEqual(self.gate.status().shared, (0,))
        self.assertEqual(self.ledger.active(), 1)
        pp.finish()
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_finish_is_idempotent_and_safe_unused(self):
        pp = self.pp(temp_dir="/t")
        pp.finish()
        _ = pp.run(info_of(GIB))
        pp.finish()
        pp.finish()
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_finish_does_not_touch_other_slots(self):
        one, two = self.pp(1, temp_dir="/t"), self.pp(2, temp_dir="/t")
        _ = one.run(info_of(GIB))
        _ = two.run(info_of(GIB))
        one.finish()
        self.assertEqual(self.gate.status().shared, (2,))
        self.assertEqual(self.ledger.active(), 1)

    def test_a_pp_can_be_used_again_after_finish(self):
        pp = self.pp(temp_dir="/t")
        for _ in range(3):
            _ = pp.run(info_of(GIB))
            pp.finish()
        self.assertEqual(self.gate.status(), GateStatus())

    def test_finish_from_another_thread_stops_a_run_in_the_gate(self):
        self.gate.acquire_exclusive(9, self.halt, self.drain)
        began = self.event()
        pp = self.pp(0, notify=lambda text: began.set())
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(began.wait(3))
        pp.finish()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(9, None, ()))

    def test_finish_from_another_thread_stops_a_run_waiting_for_space(self):
        self.disk.free[1] = 10 * GIB
        self.ledger.reserve(1, 9 * GIB, 0)
        pp = self.pp(0, temp_dir="/t")
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(wait_until(lambda: self.notes != [], 3))
        pp.finish()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(exclude=1), 0)


class TestInterruption(PreflightTestCase):
    def test_halt_before_the_start(self):
        pp = self.pp(temp_dir="/t")
        self.halt.set()
        with self.assertRaises(PreflightInterrupted):
            _ = pp.run(info_of(GIB))
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)
        self.assertEqual(self.disk.sizes, [])  # nothing was measured

    def test_drain_before_the_start(self):
        pp = self.pp(temp_dir="/t")
        self.drain.set()
        with self.assertRaises(PreflightInterrupted):
            _ = pp.run(info_of(GIB))
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)

    def test_interruption_wins_over_refusals(self):
        self.halt.set()
        pp = self.pp(maximum_size=1)
        with self.assertRaises(PreflightInterrupted):
            _ = pp.run(info_of(GIB))

    def test_halt_while_waiting_for_the_gate_leaves_nothing(self):
        self.gate.acquire_exclusive(9, self.halt, self.drain)
        halt = self.event()
        began = self.event()
        pp = self.pp(0, temp_dir="/t", halt=halt, notify=lambda text: began.set())
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(began.wait(3))
        halt.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(9, None, ()))
        self.assertEqual(self.ledger.active(), 0)

    def test_drain_while_waiting_for_the_gate_leaves_nothing(self):
        self.gate.acquire_exclusive(9, self.halt, self.drain)
        drain = self.event()
        began = self.event()
        pp = self.pp(0, drain=drain, notify=lambda text: began.set())
        task = self.spawn(lambda: pp.run(info_of(GIB)))
        self.assertTrue(began.wait(3))
        drain.set()
        self.assertTrue(task.done(3))
        self.assertIsInstance(task.error, PreflightInterrupted)
        self.assertEqual(self.gate.status(), GateStatus(9, None, ()))

    def test_a_running_download_is_not_affected_by_drain(self):
        pp = self.pp(temp_dir="/t")
        _ = pp.run(info_of(GIB))
        self.drain.set()
        pp.finish()  # releasing works as always
        self.assertEqual(self.gate.status(), GateStatus())


class TestExclusive(PreflightTestCase):
    LIMIT = 10 * GIB

    def test_large_downloads_run_exclusively(self):
        pp = self.pp(exclusive_size=self.LIMIT)
        _ = pp.run(info_of(self.LIMIT))  # exactly at the size
        self.assertEqual(self.gate.status(), GateStatus(0, None, ()))
        pp.finish()
        _ = pp.run(info_of(6 * GIB, 7 * GIB))  # the sum counts
        self.assertEqual(self.gate.status().exclusive, 0)

    def test_smaller_downloads_are_shared(self):
        pp = self.pp(exclusive_size=self.LIMIT)
        _ = pp.run(info_of(self.LIMIT - 1))
        self.assertEqual(self.gate.status(), GateStatus(None, None, (0,)))

    def test_disabled_and_unknown_sizes_are_shared(self):
        pp = self.pp(exclusive_size=0)
        _ = pp.run(info_of(10000 * GIB))
        self.assertEqual(self.gate.status().shared, (0,))
        pp.finish()
        pp = self.pp(exclusive_size=self.LIMIT)
        _ = pp.run(info_of(None))
        self.assertEqual(self.gate.status().shared, (0,))

    def test_an_exclusive_download_makes_another_slot_wait(self):
        big = self.pp(1, exclusive_size=self.LIMIT)
        _ = big.run(info_of(self.LIMIT))
        began = self.event()
        small_notes: list[str] = []
        small = self.pp(0, notify=lambda text: (small_notes.append(text), began.set()))
        task = self.spawn(lambda: small.run(info_of(GIB)))
        self.assertTrue(began.wait(3))
        self.settle()
        self.assertFalse(task.done())
        self.assertEqual(small_notes, ["Waiting for the large download in slot 2"])
        big.finish()
        self.assertTrue(task.done(3))
        self.assertIsNone(task.error)
        self.assertEqual(self.gate.status().shared, (0,))

    def test_an_exclusive_download_waits_for_the_running_ones(self):
        small = self.pp(0)
        _ = small.run(info_of(GIB))
        began = self.event()
        big = self.pp(1, exclusive_size=self.LIMIT, notify=lambda text: began.set())
        task = self.spawn(lambda: big.run(info_of(self.LIMIT)))
        self.assertTrue(began.wait(3))
        self.settle()
        self.assertFalse(task.done())
        # A new small download waits behind the large one
        other_began = self.event()
        other = self.pp(2, notify=lambda text: other_began.set())
        other_task = self.spawn(lambda: other.run(info_of(GIB)))
        self.assertTrue(other_began.wait(3))
        small.finish()
        self.assertTrue(task.done(3))
        self.assertEqual(self.gate.status().exclusive, 1)
        self.assertFalse(other_task.done(STEP * 3))
        big.finish()
        self.assertTrue(other_task.done(3))
        self.assertIsNone(other_task.error)

    def test_a_waiting_large_download_holds_no_reservation(self):
        self.gate.acquire_shared(5, self.halt, self.drain)
        began = self.event()
        big = self.pp(
            1, exclusive_size=self.LIMIT, temp_dir="/t", notify=lambda text: began.set()
        )
        task = self.spawn(lambda: big.run(info_of(self.LIMIT)))
        self.assertTrue(began.wait(3))
        self.assertEqual(self.ledger.active(), 0)
        self.gate.release(5)
        self.assertTrue(task.done(3))
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(self.LIMIT))

    def test_the_large_download_checks_space_alone(self):
        self.disk.free[1] = 100 * GIB
        big = self.pp(1, exclusive_size=self.LIMIT, temp_dir="/t")
        _ = big.run(info_of(self.LIMIT))
        # The need is 21.2G, 100G is free
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(self.LIMIT))

    def test_refusals_leave_nothing_held(self):
        self.disk.free[1] = 0
        big = self.pp(1, exclusive_size=self.LIMIT, temp_dir="/t")
        _ = self.refusal(big, info_of(self.LIMIT))
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, msg: str) -> None:
        self.messages.append(("debug", msg))

    def info(self, msg: str) -> None:
        self.messages.append(("info", msg))

    def warning(self, msg: str) -> None:
        self.messages.append(("warning", msg))

    def error(self, msg: str) -> None:
        self.messages.append(("error", msg))


class RecordingPP(PostProcessor):
    """Records a copy of the info dict it sees."""

    def __init__(self) -> None:
        super().__init__(None)
        self.seen: list[dict[str, Any]] = []

    def run(self, information: Any) -> tuple[list[str], Any]:
        self.seen.append(copy.deepcopy(dict(information)))
        return [], information


class PlainFailure(Exception):
    pass


class FailingPP(PostProcessor):
    def run(self, information: Any) -> tuple[list[str], Any]:
        raise PlainFailure("plain")


def youtube_dl(params: dict[str, object]) -> YoutubeDL:
    return cast(Callable[[dict[str, object]], YoutubeDL], YoutubeDL)(params)


def add_fake_extractor(ydl: YoutubeDL, info: dict[str, Any]) -> None:
    """Makes the fake extractor the only one: yt-dlp loads all extractors,
    and the generic one would match "fake:abc" and use the network."""
    cast(Any, ydl)._ies.clear()
    cast(Any, ydl)._ies_instances.clear()
    ydl.add_info_extractor(fake_extractor(info))


def fake_extractor(info: dict[str, Any]) -> InfoExtractor:
    class FakeIE(InfoExtractor):
        _VALID_URL = r"fake:(?P<id>\w+)"

        def _real_extract(self, url: str) -> Any:
            return copy.deepcopy(info)

    return FakeIE()


class TestYoutubeDLIntegration(IsolatedTestCase):
    """Drives a real YoutubeDL (skip_download, nothing is fetched) with the
    post-processor at before_dl."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.temp_dir()
        self.logger = FakeLogger()
        self.hooks: list[dict[str, Any]] = []
        self.gate = Gate()
        self.ledger = SpaceLedger()
        self.preflights: list[Preflight] = []
        self.addCleanup(lambda: [pp.finish() for pp in self.preflights])

    def ydl(self, **params: object) -> YoutubeDL:
        settings: dict[str, object] = {
            "quiet": True,
            "skip_download": True,
            "writethumbnail": False,
            "format": "bestvideo+bestaudio",
            "paths": {"home": self.home, "temp": self.home},
            "outtmpl": "%(id)s.%(ext)s",
            "logger": self.logger,
            "postprocessor_hooks": [self.hooks.append],
        }
        settings.update(params)
        ydl = youtube_dl(settings)
        self.addCleanup(ydl.close)
        return ydl

    def preflight(self, ydl: YoutubeDL, **kwargs: Any) -> tuple[Preflight, RecordingPP]:
        options: dict[str, Any] = {
            "temp_dir": self.home,
            "output_dir": self.home,
            "disk_usage": lambda path: SimpleNamespace(free=10**15),
        }
        options.update(kwargs)
        pp = Preflight(0, self.gate, self.ledger, Event(), Event(), **options)
        self.preflights.append(pp)
        recorder = RecordingPP()
        ydl.add_post_processor(pp, when="before_dl")
        ydl.add_post_processor(recorder, when="before_dl")
        return pp, recorder

    def info(
        self, video: int = 100 * MIB, audio: int = 10 * MIB, **extra: Any
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": "abc",
            "title": "Episode title",
            "extractor": "youtube",
            "extractor_key": "Youtube",
            "webpage_url": "https://v/abc",
            "channel": "Channel",
            "formats": [
                {
                    "format_id": "v",
                    "url": "https://v/abc.mp4",
                    "ext": "mp4",
                    "vcodec": "avc1.64001F",
                    "acodec": "none",
                    "protocol": "https",
                    "width": 1280,
                    "height": 720,
                    "filesize": video,
                },
                {
                    "format_id": "a",
                    "url": "https://v/abc.m4a",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "protocol": "https",
                    "filesize": audio,
                },
            ],
        }
        info.update(extra)
        return info

    @staticmethod
    def process(ydl: YoutubeDL, info: dict[str, Any]) -> dict[str, Any]:
        process = cast(
            Callable[[dict[str, Any], bool], dict[str, Any]], ydl.process_ie_result
        )
        return process(info, True)

    def test_an_accepted_download_continues(self):
        ydl = self.ydl()
        pp, recorder = self.preflight(ydl, maximum_size=GIB)
        result = self.process(ydl, self.info())
        (seen,) = recorder.seen
        self.assertEqual(len(seen["requested_formats"]), 2)
        self.assertEqual(download_size(seen), 110 * MIB)
        # The stage after before_dl ran: the file was "moved" (skip_download)
        self.assertEqual(len(result["requested_downloads"]), 1)
        self.assertIn("MoveFiles", {hook["postprocessor"] for hook in self.hooks})
        self.assertEqual(self.ledger.reserved_temp(), temp_space_needed(110 * MIB))
        self.assertEqual(self.gate.status().shared, (0,))
        pp.finish()
        self.assertEqual(self.ledger.active(), 0)
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(
            [m for m in self.logger.messages if m[0] in ("warning", "error")], []
        )

    def test_a_refusal_propagates_unwrapped(self):
        ydl = self.ydl()
        pp, recorder = self.preflight(ydl, maximum_size=100 * MIB)
        with self.assertRaises(PreflightError) as cm:
            _ = self.process(ydl, self.info())
        self.assertIs(type(cm.exception), PreflightError)
        self.assertIs(cm.exception.reason, PreflightReason.TOO_LARGE)
        self.assertEqual(cm.exception.message, "Too large (110M)")
        self.assertTrue(cm.exception.archive)
        self.assertEqual(recorder.seen, [])  # the next stage never ran
        self.assertNotIn("MoveFiles", {hook["postprocessor"] for hook in self.hooks})
        self.assertEqual(self.gate.status(), GateStatus())
        self.assertEqual(self.ledger.active(), 0)
        pp.finish()

    def test_hls_refusal_for_youtube(self):
        ydl = self.ydl()
        _, recorder = self.preflight(ydl)
        info = self.info()
        for entry in info["formats"]:
            entry["protocol"] = "m3u8_native"
        with self.assertRaises(PreflightError) as cm:
            _ = self.process(ydl, info)
        self.assertIs(cm.exception.reason, PreflightReason.HLS_ONLY)
        self.assertEqual(recorder.seen, [])

    def test_no_space_refusal(self):
        ydl = self.ydl()
        _, recorder = self.preflight(
            ydl, disk_usage=lambda path: SimpleNamespace(free=GIB)
        )
        with self.assertRaises(PreflightError) as cm:
            _ = self.process(ydl, self.info(video=5 * GIB))
        self.assertIs(cm.exception.reason, PreflightReason.NO_SPACE)
        self.assertEqual(recorder.seen, [])

    def test_control_a_plain_exception_is_swallowed_with_ignoreerrors(self):
        # Why PreflightError is a DownloadCancelled: extract_info swallows any
        # other exception when ignoring errors, and reports str(e) as an error
        ydl = self.ydl(ignoreerrors=True)
        add_fake_extractor(ydl, self.info())
        ydl.add_post_processor(FailingPP(), when="before_dl")
        self.assertEqual(ydl.download(["fake:abc"]), 1)  # no exception
        self.assertTrue(any("plain" in text for _, text in self.logger.messages))
        # Raised unwrapped without it
        ydl = self.ydl()
        add_fake_extractor(ydl, self.info())
        ydl.add_post_processor(FailingPP(), when="before_dl")
        with self.assertRaises(PlainFailure):
            _ = ydl.download(["fake:abc"])

    def test_download_raises_the_same_exception(self):
        for ignore in (False, True):
            with self.subTest(ignoreerrors=ignore):
                ydl = self.ydl(ignoreerrors=ignore)
                add_fake_extractor(
                    ydl, self.info(extractor="fake", extractor_key="Fake")
                )
                _, recorder = self.preflight(ydl, maximum_size=MIB)
                with self.assertRaises(PreflightError) as cm:
                    _ = ydl.download(["fake:abc"])
                self.assertIs(type(cm.exception), PreflightError)
                self.assertEqual(cm.exception.reason, PreflightReason.TOO_LARGE)
                self.assertEqual(recorder.seen, [])

    def test_download_continues_when_accepted(self):
        ydl = self.ydl()
        add_fake_extractor(ydl, self.info(extractor="fake", extractor_key="Fake"))
        _, recorder = self.preflight(ydl, maximum_size=GIB)
        self.assertEqual(ydl.download(["fake:abc"]), 0)
        self.assertEqual(len(recorder.seen), 1)

    def test_interruption_propagates_unwrapped(self):
        halt = Event()
        halt.set()
        ydl = self.ydl()
        pp = Preflight(0, self.gate, self.ledger, halt, Event())
        recorder = RecordingPP()
        ydl.add_post_processor(pp, when="before_dl")
        ydl.add_post_processor(recorder, when="before_dl")
        with self.assertRaises(PreflightInterrupted) as cm:
            _ = self.process(ydl, self.info())
        self.assertIs(type(cm.exception), PreflightInterrupted)
        self.assertEqual(recorder.seen, [])

    def test_no_progress_hooks_are_reported(self):
        ydl = self.ydl()
        pp, _ = self.preflight(ydl)
        _ = self.process(ydl, self.info())
        self.assertNotIn("Preflight", {hook["postprocessor"] for hook in self.hooks})
        pp.finish()
        ydl = self.ydl()
        _ = self.preflight(ydl, maximum_size=MIB)
        with self.assertRaises(PreflightError):
            _ = self.process(ydl, self.info())
        self.assertNotIn("Preflight", {hook["postprocessor"] for hook in self.hooks})


if __name__ == "__main__":
    unittest.main()
