import io
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from queue import Queue
from threading import Event, ExceptHookArgs, Thread
from types import TracebackType
from typing import cast

from .ansi import ANSI
from .config import Config


class Debug:
    BUFFER_SIZE: int = 16384
    buffer: str = ""
    wrapper: io.TextIOWrapper | None = None
    queue: Queue[tuple[int, str]] | None = None
    thread: Thread | None = None
    inactive: Event | None = None

    @staticmethod
    def activate():
        Debug.queue = Queue()
        Debug.inactive = Event()
        Debug.thread = Thread(target=Debug.__loop, name="Debug-thread")
        Debug.thread.start()
        sys.excepthook = Debug.__except_handler
        threading.excepthook = Debug.__thread_except_handler

    @staticmethod
    def deactivate():
        if Debug.inactive is not None:
            Debug.inactive.set()
        if Debug.thread is not None:
            Debug.print(-1, "=== Inactive ===")
            Debug.thread.join()

    @staticmethod
    def __loop():
        if (
            (file_name := str(Config.settings["Debug"]["file_name"]))
            and Debug.queue is not None
            and Debug.inactive is not None
        ):
            with open(file_name, "a") as Debug.wrapper:
                try:
                    Debug.__write(-1, "=== Begin ===")
                    while not Debug.inactive.is_set():
                        slot_index, message = Debug.queue.get()
                        if message:
                            Debug.__write(slot_index, message)
                        if Debug.__wrap_around(file_name):
                            Debug.__write(-1, "=== Wrap ===")
                            break
                finally:
                    Debug.__write(-1, "=== End ===")
                    _ = Debug.wrapper.write(Debug.buffer)
                    Debug.wrapper.flush()
            if not Debug.inactive.is_set():
                Debug.__wrap_move(file_name)
                Debug.__loop()

    @staticmethod
    def __write(slot_index: int, message: str):
        prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        message = ANSI.remove(str(message))
        if slot_index < 0:
            Debug.buffer += f"{prefix} UTC > {message}\n"
        else:
            Debug.buffer += f"{prefix} UTC > SLOT-{slot_index + 1:02} > {message}\n"
        if Debug.wrapper is not None and len(Debug.buffer) > Debug.BUFFER_SIZE:
            _ = Debug.wrapper.write(Debug.buffer)
            Debug.wrapper.flush()
            Debug.buffer = ""

    @staticmethod
    def __wrap_around(file_name: str) -> bool:
        return (
            os.path.getsize(file_name)
            > cast(int, Config.settings["Debug"]["wrap_size"]) * Debug.BUFFER_SIZE
        )

    @staticmethod
    def __wrap_move(file_name: str):
        count = 0
        while count < 1000 and os.path.exists(f"{file_name}.{count:03}"):
            count += 1
        if count == 1000:
            os.remove(f"{file_name}.999")
            count -= 1
        for index in range(count - 1, -1, -1):
            os.rename(
                f"{file_name}.{index:03}",
                f"{file_name}.{index + 1:03}",
            )
        os.rename(file_name, f"{file_name}.000")

    @staticmethod
    def __except_handler(
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ):
        buffer = io.StringIO()
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=buffer)
        Debug.print(-1, "Exception:\n" + buffer.getvalue())

    @staticmethod
    def __thread_except_handler(args: ExceptHookArgs):
        Debug.__except_handler(args.exc_type, args.exc_value, args.exc_traceback)

    # Called externally
    @staticmethod
    def print(slot_index: int, message: str):
        if Debug.queue is not None and Debug.inactive is not None:
            Debug.queue.put((slot_index, message))
