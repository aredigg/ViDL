from dataclasses import dataclass


# Requests from the keyboard (view thread) to the coordinator
@dataclass(frozen=True)
class Command: ...


# Quit now, running downloads are cancelled
@dataclass(frozen=True)
class QuitCommand(Command): ...


# Finish the running downloads and quit, or cancel a pending finish
@dataclass(frozen=True)
class DrainCommand(Command): ...


@dataclass(frozen=True)
class AddChannelCommand(Command):
    text: str
