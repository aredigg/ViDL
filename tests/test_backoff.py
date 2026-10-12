# pyright: standard
import sys
import time
import unittest
from collections.abc import Callable
from threading import Barrier, Event, Lock, Thread
from unittest import mock

from tests.helpers import IsolatedTestCase
from vidl.backoff import Backoff
from vidl.debug import Debug


class FakeClock:
    """A controllable clock. Every call advances it by step seconds."""

    def __init__(self, now: float = 1000.0, step: float = 0.0) -> None:
        self.now = now
        self.step = step
        self.__lock = Lock()

    def __call__(self) -> float:
        with self.__lock:
            now = self.now
            self.now += self.step
            return now

    def advance(self, seconds: float) -> None:
        with self.__lock:
            self.now += seconds


class BackoffTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()

    def backoff(self, slots: int) -> Backoff:
        return Backoff(slots, self.clock)

    def report(self, backoff: Backoff, *slots: int) -> list[bool]:
        return [backoff.report_restriction(slot, "Video unavailable") for slot in slots]

    def capture_debug(self) -> list[str]:
        messages: list[str] = []

        def record(slot_index: int, message: str) -> None:
            messages.append(message)

        patcher = mock.patch.object(Debug, "print", new=record)
        patcher.start()
        self.addCleanup(patcher.stop)
        return messages

    def fast_wait_step(self, seconds: float = 0.01) -> None:
        patcher = mock.patch.object(Backoff, "WAIT_STEP", seconds)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_wait(self, backoff: Backoff, *events: Event) -> tuple[Thread, list[bool]]:
        result: list[bool] = []
        thread = Thread(
            target=lambda: result.append(backoff.wait(*events)),
            name="Backoff-wait",
            daemon=True,
        )
        thread.start()
        return thread, result

    def finish_wait(
        self, thread: Thread, result: list[bool], timeout: float = 2.0
    ) -> bool:
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "wait() did not return in time")
        self.assertEqual(len(result), 1)
        return result[0]


class TestThreshold(BackoffTestCase):
    def test_threshold_table(self):
        expected = {0: 1, 1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4}
        for slots, threshold in expected.items():
            with self.subTest(slots=slots):
                self.assertEqual(self.backoff(slots).threshold(), threshold)

    def test_negative_slots_are_one(self):
        self.assertEqual(self.backoff(-4).threshold(), 1)

    def test_default_is_one_slot(self):
        self.assertEqual(Backoff().threshold(), 1)


class TestTrigger(BackoffTestCase):
    def test_one_slot_one_restriction_triggers(self):
        backoff = self.backoff(1)
        self.assertTrue(backoff.report_restriction(0, "Sign in to confirm"))
        self.assertTrue(backoff.active())

    def test_two_slots(self):
        backoff = self.backoff(2)
        self.assertEqual(self.report(backoff, 0), [False])
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.restricted_slots(), 1)
        self.assertEqual(self.report(backoff, 1), [True])
        self.assertTrue(backoff.active())

    def test_same_slot_does_not_count_twice(self):
        backoff = self.backoff(2)
        self.assertEqual(self.report(backoff, 0, 0, 0), [False, False, False])
        self.assertEqual(backoff.restricted_slots(), 1)
        self.assertFalse(backoff.active())

    def test_three_slots_two_trigger(self):
        backoff = self.backoff(3)
        self.assertEqual(self.report(backoff, 2, 0), [False, True])

    def test_four_slots_two_trigger(self):
        backoff = self.backoff(4)
        self.assertEqual(self.report(backoff, 3), [False])
        self.assertFalse(backoff.active())
        self.assertEqual(self.report(backoff, 1), [True])

    def test_five_slots_three_trigger(self):
        backoff = self.backoff(5)
        self.assertEqual(self.report(backoff, 0, 1), [False, False])
        self.assertFalse(backoff.active())
        self.assertEqual(self.report(backoff, 4), [True])

    def test_arbitrary_slot_indices(self):
        backoff = self.backoff(2)
        self.assertEqual(self.report(backoff, 17, -3), [False, True])

    def test_entries_are_cleared_on_trigger(self):
        backoff = self.backoff(3)
        _ = self.report(backoff, 0, 1)
        self.assertEqual(backoff.restricted_slots(), 0)


class TestWindow(BackoffTestCase):
    def test_reports_within_the_window_trigger(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.WINDOW - 1)  # 14:59
        self.assertEqual(self.report(backoff, 1), [True])

    def test_reports_at_the_window_do_not_trigger(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.WINDOW)  # 15:00
        self.assertEqual(self.report(backoff, 1), [False])
        self.assertEqual(backoff.restricted_slots(), 1)
        self.assertFalse(backoff.active())

    def test_window_is_fifteen_minutes(self):
        self.assertEqual(Backoff.WINDOW, 15 * 60)

    def test_entries_expire(self):
        backoff = self.backoff(4)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.WINDOW - 1)
        self.assertEqual(backoff.restricted_slots(), 1)
        self.clock.advance(1)
        self.assertEqual(backoff.restricted_slots(), 0)

    def test_refreshing_a_report_extends_it(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        self.clock.advance(600)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.WINDOW - 1)  # 24:59 after the first report
        self.assertEqual(backoff.restricted_slots(), 1)
        self.assertEqual(self.report(backoff, 1), [True])


class TestSuccess(BackoffTestCase):
    def test_success_clears_the_slot(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        backoff.report_success(0)
        self.assertEqual(backoff.restricted_slots(), 0)
        self.assertEqual(self.report(backoff, 1), [False])
        self.assertFalse(backoff.active())

    def test_success_only_clears_its_own_slot(self):
        backoff = self.backoff(3)
        _ = self.report(backoff, 0)
        backoff.report_success(1)
        self.assertEqual(backoff.restricted_slots(), 1)
        self.assertEqual(self.report(backoff, 1), [True])

    def test_success_without_entry(self):
        backoff = self.backoff(2)
        backoff.report_success(5)
        self.assertEqual(backoff.restricted_slots(), 0)

    def test_success_does_not_end_a_back_off(self):
        backoff = self.backoff(1)
        _ = self.report(backoff, 0)
        backoff.report_success(0)
        self.assertTrue(backoff.active())
        self.assertEqual(backoff.remaining(), Backoff.DURATION)


class TestActive(BackoffTestCase):
    def test_inactive_initially(self):
        backoff = self.backoff(3)
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.remaining(), 0.0)
        self.assertEqual(backoff.restricted_slots(), 0)

    def test_duration_is_six_hours(self):
        self.assertEqual(Backoff.DURATION, 6 * 3600)

    def test_active_until_duration_has_passed(self):
        backoff = self.backoff(1)
        _ = self.report(backoff, 0)
        self.assertTrue(backoff.active())
        self.assertEqual(backoff.remaining(), Backoff.DURATION)
        self.clock.advance(Backoff.DURATION - 1)
        self.assertTrue(backoff.active())
        self.assertEqual(backoff.remaining(), 1.0)
        self.clock.advance(1)
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.remaining(), 0.0)
        self.clock.advance(Backoff.DURATION)
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.remaining(), 0.0)

    def test_reports_during_a_back_off_are_not_recorded(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0, 1)
        self.clock.advance(60)
        self.assertEqual(self.report(backoff, 0, 1), [True, True])
        self.assertEqual(backoff.restricted_slots(), 0)
        # The back-off is not extended either
        self.assertEqual(backoff.remaining(), Backoff.DURATION - 60)
        self.clock.advance(Backoff.DURATION)
        self.assertFalse(backoff.active())
        self.assertEqual(self.report(backoff, 0), [False])
        self.assertFalse(backoff.active())

    def test_triggers_again_after_a_back_off(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0, 1)
        self.clock.advance(Backoff.DURATION)
        self.assertEqual(self.report(backoff, 1, 0), [False, True])
        self.assertEqual(backoff.remaining(), Backoff.DURATION)


class TestWallClock(BackoffTestCase):
    def test_the_wall_clock_is_the_default(self):
        # The monotonic clock stops while the computer sleeps (macOS), and the
        # back-off would last longer by that time
        self.assertIs(Backoff()._Backoff__clock, time.time)  # pyright: ignore[reportAttributeAccessIssue]

    def test_set_back_clock_never_extends_a_back_off(self):
        backoff = self.backoff(1)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.DURATION - 60)
        self.assertEqual(backoff.remaining(), 60)
        self.clock.advance(-3600)
        self.assertEqual(backoff.remaining(), 60)
        self.clock.advance(60)
        self.assertFalse(backoff.active())

    def test_time_asleep_counts(self):
        # The wall clock jumps ahead after the computer slept
        backoff = self.backoff(1)
        _ = self.report(backoff, 0)
        self.clock.advance(Backoff.DURATION + 3600)
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.remaining(), 0.0)

    def test_set_back_clock_never_extends_a_report(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        self.clock.advance(-3600)
        self.assertEqual(backoff.restricted_slots(), 1)
        self.clock.advance(Backoff.WINDOW - 1)
        self.assertEqual(backoff.restricted_slots(), 1)
        self.clock.advance(1)
        self.assertEqual(backoff.restricted_slots(), 0)
        self.assertEqual(self.report(backoff, 1), [False])


class TestSetSlots(BackoffTestCase):
    def test_threshold_follows_the_slots(self):
        backoff = self.backoff(1)
        for slots, threshold in ((5, 3), (7, 4), (2, 2), (1, 1), (0, 1), (-3, 1)):
            with self.subTest(slots=slots):
                backoff.set_slots(slots)
                self.assertEqual(backoff.threshold(), threshold)

    def test_growing_raises_the_threshold(self):
        backoff = self.backoff(2)
        _ = self.report(backoff, 0)
        backoff.set_slots(5)
        self.assertEqual(self.report(backoff, 1), [False])
        self.assertEqual(self.report(backoff, 2), [True])

    def test_shrinking_lowers_the_threshold_on_the_next_report(self):
        backoff = self.backoff(5)
        _ = self.report(backoff, 0, 1)
        backoff.set_slots(2)
        self.assertFalse(backoff.active())
        self.assertEqual(backoff.restricted_slots(), 2)
        self.assertEqual(self.report(backoff, 0), [True])


class TestWait(BackoffTestCase):
    def active_backoff(self) -> Backoff:
        backoff = self.backoff(1)
        _ = self.report(backoff, 0)
        return backoff

    def test_returns_false_when_inactive(self):
        backoff = self.backoff(2)
        thread, result = self.start_wait(backoff)
        self.assertFalse(self.finish_wait(thread, result, timeout=0.5))
        thread, result = self.start_wait(backoff, Event())
        self.assertFalse(self.finish_wait(thread, result, timeout=0.5))

    def test_returns_true_when_an_event_is_already_set(self):
        backoff = self.active_backoff()
        event = Event()
        event.set()
        thread, result = self.start_wait(backoff, event)
        self.assertTrue(self.finish_wait(thread, result, timeout=0.5))

    def test_already_set_event_wins_when_inactive(self):
        event = Event()
        event.set()
        self.assertTrue(self.backoff(2).wait(event))

    def test_returns_true_when_an_event_is_set(self):
        backoff = self.active_backoff()
        event = Event()
        thread, result = self.start_wait(backoff, event)
        time.sleep(0.05)
        self.assertTrue(thread.is_alive())
        started = time.monotonic()
        event.set()
        self.assertTrue(self.finish_wait(thread, result))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_any_of_multiple_events(self):
        self.fast_wait_step(0.05)
        backoff = self.active_backoff()
        first, second = Event(), Event()
        thread, result = self.start_wait(backoff, first, second)
        time.sleep(0.05)
        self.assertTrue(thread.is_alive())
        second.set()
        self.assertTrue(self.finish_wait(thread, result))
        self.assertFalse(first.is_set())

    def test_returns_false_when_the_back_off_is_over(self):
        self.fast_wait_step()
        backoff = self.active_backoff()
        self.clock.step = Backoff.DURATION / 4
        thread, result = self.start_wait(backoff, Event())
        self.assertFalse(self.finish_wait(thread, result))
        self.assertFalse(backoff.active())

    def test_returns_false_when_the_clock_is_advanced(self):
        self.fast_wait_step(0.05)
        backoff = self.active_backoff()
        event = Event()
        thread, result = self.start_wait(backoff, event)
        time.sleep(0.1)
        self.assertTrue(thread.is_alive())
        self.clock.advance(Backoff.DURATION)
        self.assertFalse(self.finish_wait(thread, result))
        self.assertFalse(event.is_set())

    def test_without_events(self):
        self.fast_wait_step()
        backoff = self.active_backoff()
        self.clock.step = Backoff.DURATION / 4
        thread, result = self.start_wait(backoff)
        self.assertFalse(self.finish_wait(thread, result))

    def test_waits_at_most_a_step_between_checks(self):
        backoff = self.active_backoff()
        event = Event()
        timeouts: list[float | None] = []

        def record(timeout: float | None = None) -> bool:
            timeouts.append(timeout)
            self.clock.advance(Backoff.DURATION - 0.5 if len(timeouts) == 1 else 1)
            return False

        with mock.patch.object(event, "wait", side_effect=record):
            self.assertFalse(backoff.wait(event))
        self.assertEqual(timeouts, [Backoff.WAIT_STEP, 0.5])


class TestLogging(BackoffTestCase):
    def test_decisions_are_logged(self):
        messages = self.capture_debug()
        backoff = self.backoff(2)
        _ = backoff.report_restriction(0, "Video unavailable")
        self.assertEqual(len(messages), 1)
        self.assertIn("SLOT-01", messages[0])
        self.assertIn("(1/2)", messages[0])
        self.assertIn("Video unavailable", messages[0])
        _ = backoff.report_restriction(1, "HTTP Error 429")
        self.assertEqual(len(messages), 3)
        self.assertIn("(2/2)", messages[1])
        self.assertIn("triggered", messages[2])
        self.assertIn("06:00:00", messages[2])
        _ = backoff.report_restriction(0, "Sign in to confirm")
        self.assertEqual(len(messages), 4)
        self.assertIn("not recorded", messages[3])
        self.assertIn("Sign in to confirm", messages[3])

    def test_inactive_debug_is_harmless(self):
        Debug.queue = Debug.inactive = None
        self.assertTrue(self.backoff(1).report_restriction(0))


class TestThreadSafety(BackoffTestCase):
    THREADS: int = 32

    def setUp(self):
        super().setUp()
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, interval)
        self.messages = self.capture_debug()

    def triggers(self) -> int:
        return sum("triggered" in message for message in self.messages)

    def run_threads(self, target: Callable[[int], None], count: int) -> None:
        barrier = Barrier(count)
        errors: list[BaseException] = []

        def run(index: int) -> None:
            try:
                _ = barrier.wait(5)
                target(index)
            except BaseException as e:  # noqa: BLE001 - reported by the test
                errors.append(e)

        threads = [
            Thread(target=run, args=(i,), name=f"Backoff-{i}", daemon=True)
            for i in range(count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_concurrent_reports_trigger_once(self):
        for round_ in range(20):
            with self.subTest(round=round_):
                self.messages.clear()
                backoff = self.backoff(8)
                results: list[bool] = []

                def report(
                    index: int,
                    backoff: Backoff = backoff,
                    results: list[bool] = results,
                ) -> None:
                    results.append(backoff.report_restriction(index, "busy"))

                self.run_threads(report, self.THREADS)
                self.assertEqual(self.triggers(), 1)
                self.assertEqual(len(results), self.THREADS)
                # Only the reports before the trigger were recorded
                self.assertEqual(results.count(False), backoff.threshold() - 1)
                self.assertEqual(backoff.restricted_slots(), 0)
                self.assertTrue(backoff.active())
                self.assertEqual(backoff.remaining(), Backoff.DURATION)

    def test_concurrent_mixed_calls(self):
        backoff = self.backoff(self.THREADS)

        def hammer(index: int) -> None:
            for _ in range(200):
                if backoff.report_restriction(index, "busy"):
                    return
                backoff.report_success(index)
                _ = backoff.restricted_slots()
                _ = backoff.active()
                _ = backoff.report_restriction(index, "busy")

        self.run_threads(hammer, self.THREADS)
        self.assertEqual(self.triggers(), 1)
        self.assertTrue(backoff.active())
        self.assertEqual(backoff.restricted_slots(), 0)


if __name__ == "__main__":
    unittest.main()
