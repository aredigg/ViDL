from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

from .backoff import Backoff


def _ignore() -> None: ...


@dataclass(frozen=True)
class Shared:
    """State shared by the coordinator, all slots and their channels."""

    backoff: Backoff = field(default_factory=Backoff)
    # Set to finish the current downloads and stop, without cancelling them
    drain_event: Event = field(default_factory=Event)
    # Passive network health reports, e.g. for the header status bar
    network_error: Callable[[], None] = _ignore
    network_success: Callable[[], None] = _ignore
