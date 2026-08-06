import io
import sys
import threading
import traceback
from datetime import datetime, timezone
from queue import Queue
from threading import Event, Thread

from .ansi import ANSI
from .config import Config


class Debug:
    BUFFER_SIZE = 4096
    buffer = ""
    wrapper = None
    queue = None
    thread = None
    inactive = None

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
            Debug.print("=== Inactive ===")
            Debug.thread.join()

    @staticmethod
    def __loop():
        if file_name := Config.settings["Debug"]["file_name"]:
            with open(file_name, "a") as Debug.wrapper:
                if Debug.queue is not None and Debug.inactive is not None:
                    try:
                        Debug.__write("=== Begin ===")
                        while not Debug.inactive.is_set():
                            message = Debug.queue.get()
                            if message:
                                Debug.__write(message)
                    finally:
                        Debug.__write("=== End ===")
                        if Debug.wrapper is not None:
                            Debug.wrapper.write(Debug.buffer)
                            Debug.wrapper.flush()

    @staticmethod
    def __write(message):
        prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        message = ANSI.remove(str(message))
        Debug.buffer += f"{prefix} UTC > {message}\n"
        if Debug.wrapper is not None and len(Debug.buffer) > Debug.BUFFER_SIZE:
            Debug.wrapper.write(Debug.buffer)
            Debug.wrapper.flush()
            Debug.buffer = ""

    @staticmethod
    def __except_handler(exc_type, exc_value, exc_traceback):
        buffer = io.StringIO()
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=buffer)
        Debug.print("Exception:\n" + buffer.getvalue())

    @staticmethod
    def __thread_except_handler(args):
        Debug.__except_handler(args.exc_type, args.exc_value, args.exc_traceback)

    # Called externally
    @staticmethod
    def print(message):
        if Debug.queue is not None and Debug.inactive is not None:
            Debug.queue.put(message)
