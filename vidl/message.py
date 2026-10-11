from dataclasses import dataclass
from enum import Enum

from .item import Item
from .keyboard import Key
from .monitor import SystemStatus


@dataclass
class Message:
    class Provider(Enum):
        CHANNEL = "channel"
        DOWNLOAD = "download"
        HOOK = "hook"
        SLOT = "slot"


@dataclass
class HaltMessage(Message): ...


@dataclass
class RedrawMessage(Message): ...


# Keys read from the terminal
@dataclass
class InputMessage(Message):
    keys: tuple[Key, ...]


# A lasting state shown in the header, e.g. a pending quit or a back-off.
# An empty status clears it, title is a short form for the title bar
@dataclass
class HeaderMessage(Message):
    status: str
    title: str = ""


# A short lived notice shown in the header, e.g. the result of a command
@dataclass
class NoticeMessage(Message):
    text: str
    error: bool = False


@dataclass
class SystemStatusMessage(Message):
    status: SystemStatus


@dataclass
class SlotMessage(Message):
    index: int
    provider: Message.Provider


@dataclass
class InitMessage(SlotMessage): ...


@dataclass
class SleepMessage(SlotMessage):
    sleep_time: float
    required: bool = False


@dataclass
class CountMessage(SlotMessage):
    value: int


@dataclass
class CutoffMessage(SlotMessage):
    value: int


@dataclass
class PathMessage(SlotMessage):
    path: str


@dataclass
class EntityMessage(SlotMessage):
    entity: Item.Entity


@dataclass
class ProgressMessage(SlotMessage):
    progress: Item.Progress


@dataclass
class MediaMessage(SlotMessage):
    media: Item.Media


@dataclass
class StatusMessage(SlotMessage):
    target: str
    message: str


@dataclass
class InfoMessage(StatusMessage): ...


@dataclass
class WarningMessage(StatusMessage): ...


@dataclass
class ErrorMessage(StatusMessage): ...
