import math
import time
from collections.abc import Callable
from threading import Event, Lock

from .debug import Debug
from .util import Util


class Backoff:
    """Decides when all slots should back off together.

    A single restriction error (e.g. "Video unavailable") may just be a deleted
    video, so a global back-off only starts when enough slots report
    restrictions within WINDOW seconds of each other.
    """

    WINDOW: float = (
        15 * Util.SECONDS_PER_MINUTE
    )  # Restriction reports expire after this
    DURATION: float = 6 * Util.SECONDS_PER_HOUR  # Length of a global back-off
    WAIT_STEP: float = 1.0  # Max seconds between event checks in wait()

    def __init__(
        self, slots: int = 1, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.__lock = Lock()
        self.__clock = clock
        self.__slots = max(1, slots)
        self.__restrictions: dict[int, float] = {}  # Slot -> time of report
        self.__until: float | None = None
        self.__never = Event()  # Waited on when wait() is given no events

    def set_slots(self, slots: int) -> None:
        with self.__lock:
            self.__slots = max(1, slots)

    def threshold(self) -> int:
        with self.__lock:
            return self.__threshold()

    def report_restriction(self, slot: int, reason: str = "") -> bool:
        name = Backoff.__name(slot)
        with self.__lock:
            now = self.__clock()
            if self.__active(now):
                # Reports from slots finishing work during a back-off must not carry over
                left = Backoff.__format(self.__remaining(now))
                messages = [
                    f"Backoff: {name} not recorded, backing off for {left}: {reason}"
                ]
                result = True
            else:
                self.__restrictions[slot] = now
                self.__purge(now)
                count, threshold = len(self.__restrictions), self.__threshold()
                messages = [
                    f"Backoff: {name} restricted ({count}/{threshold}): {reason}"
                ]
                result = count >= threshold
                if result:
                    self.__until = now + Backoff.DURATION
                    # Restrictions after the back-off count from zero
                    self.__restrictions.clear()
                    slots, duration = self.__slots, Backoff.__format(Backoff.DURATION)
                    messages.append(
                        f"Backoff: triggered by {count} of {slots} slots for {duration}"
                    )
        for message in messages:
            Debug.print(-1, message)
        return result

    def report_success(self, slot: int) -> None:
        with self.__lock:
            _ = self.__restrictions.pop(slot, None)
            self.__purge(self.__clock())

    def active(self) -> bool:
        with self.__lock:
            now = self.__clock()
            self.__purge(now)
            return self.__active(now)

    def remaining(self) -> float:
        with self.__lock:
            now = self.__clock()
            self.__purge(now)
            return self.__remaining(now)

    def restricted_slots(self) -> int:
        with self.__lock:
            self.__purge(self.__clock())
            return len(self.__restrictions)

    # Blocks while a back-off is active. Returns True as soon as any of the
    # events is set, False when the back-off is over (or was not active)
    def wait(self, *events: Event) -> bool:
        waiter = events[0] if events else self.__never
        while True:
            if any(event.is_set() for event in events):
                return True
            remaining = self.remaining()
            if remaining <= 0:
                return False
            if waiter.wait(min(Backoff.WAIT_STEP, remaining)):
                return True

    def __threshold(self) -> int:
        if self.__slots <= 1:
            return 1
        return max(2, math.ceil(self.__slots / 2))

    def __active(self, now: float) -> bool:
        return self.__until is not None and now < self.__until

    def __remaining(self, now: float) -> float:
        if self.__until is None:
            return 0.0
        return max(0.0, self.__until - now)

    def __purge(self, now: float) -> None:
        expired = [
            slot
            for slot, reported in self.__restrictions.items()
            if now - reported >= Backoff.WINDOW
        ]
        for slot in expired:
            del self.__restrictions[slot]

    @staticmethod
    def __format(seconds: float) -> str:
        return Util.format_seconds(math.ceil(seconds), two_parts=False)

    @staticmethod
    def __name(slot: int) -> str:
        # Slot indices are zero based, Debug shows them one based
        return f"SLOT-{slot + 1:02}"
