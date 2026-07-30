from dataclasses import dataclass
from enum import Enum

from .item import Item


class Msg(Enum):
    HALT = 0
    SLEEP = 1
    INIT = 2
    INFO = 3
    WARN = 4
    ERROR = 5


@dataclass
class ProviderMessage:
    index: int
    provider: str


@dataclass
class SleepMessage(ProviderMessage):
    time_offset: int


@dataclass
class InfoMessage(ProviderMessage):
    target: None | str
    message: None | str


@dataclass
class WarnMessage(ProviderMessage):
    target: None | str
    message: None | str


@dataclass
class ErrorMessage(ProviderMessage):
    message: None | str


@dataclass
class URLMessage(ProviderMessage):
    url: str


@dataclass
class FilePathMessage(ProviderMessage):
    path: str


@dataclass
class ChannelMessage(ProviderMessage):
    channel: Item


@dataclass
class Message:
    kind: Msg
    body: None | ProviderMessage


# messages
# - slot init (slot id)
# - download init ()
# - download start
# - debug/hook
# - download complete
# - slot clear

# slot id, status, dl size, res, bitrate, channel
# title
# last dl dte, timer, status msg
