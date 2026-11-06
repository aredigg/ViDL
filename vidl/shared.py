from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

from .preflight import Gate, SpaceLedger
from .suspension import Suspension


def _ignore() -> None: ...


@dataclass(frozen=True)
class Shared:
    """State shared by the coordinator, all slots and their channels."""

    suspension: Suspension = field(default_factory=Suspension)
    # Set to finish the current downloads and stop, without cancelling them
    drain_event: Event = field(default_factory=Event)
    # Lets large downloads run alone, and the disk space reserved by downloads
    gate: Gate = field(default_factory=Gate)
    ledger: SpaceLedger = field(default_factory=SpaceLedger)
    # Passive network health reports, e.g. for the header status bar
    network_error: Callable[[], None] = _ignore
    network_success: Callable[[], None] = _ignore
