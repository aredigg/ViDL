from dataclasses import dataclass
from enum import Enum

from .item import Item


@dataclass
class Message:
    class Provider(Enum):
        CHANNEL = "channel"
        DOWNLOAD = "download"
        HOOK = "hook"
        LOGGER = "logger"
        SLOT = "slot"


@dataclass
class HaltMessage(Message): ...


@dataclass
class RedrawMessage(Message): ...


@dataclass
class SlotMessage(Message):
    index: int
    provider: Message.Provider


@dataclass
class InitMessage(SlotMessage): ...


@dataclass
class SleepMessage(SlotMessage):
    sleep_time: float


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
class UrlMessage(SlotMessage):
    url: str


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
