from dataclasses import dataclass
from enum import Enum


class Msg(Enum):
    HALT = 0


@dataclass
class Message:
    kind: Msg
    body: None


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
