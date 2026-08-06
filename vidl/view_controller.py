from functools import singledispatchmethod
from queue import Empty, Queue
from threading import Event, Thread
from time import time

from vidl.ansi import ANSI
from vidl.hook import Hook

from .message import (
    CountMessage,
    EntityMessage,
    ErrorMessage,
    HaltMessage,
    InfoMessage,
    InitMessage,
    MediaMessage,
    Message,
    PathMessage,
    ProgressMessage,
    RedrawMessage,
    SleepMessage,
    WarningMessage,
)
from .terminal import Terminal
from .util import Util
from .view import View


class ViewController:
    index = 0
    UPDATES_PER_SECOND = 4
    BLINKER_INTERVAL = UPDATES_PER_SECOND >> 1
    RUN_LOOP_WAIT = 1 / UPDATES_PER_SECOND

    def __init__(self) -> None:
        self.__ready: bool = False
        self.__views: dict[int, View] = {}
        self.__slots_ = set()
        self.__redraw_required = False
        self.__queue = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(
            target=self.__run, name=f"ViewController-{ViewController.index}"
        )
        self.__thread.start()
        ViewController.index += 1

    def __run(self) -> None:
        with Terminal() as terminal:
            self.__create_header()
            self.__redraw(terminal)
            self.__ready = True
            counter = ViewController.UPDATES_PER_SECOND
            while not self.__halt_event.is_set():
                try:
                    message: Message = self.__queue.get(
                        timeout=ViewController.RUN_LOOP_WAIT
                    )
                    if not isinstance(message, HaltMessage):
                        self.__dispatch_message(message)
                except Empty:
                    ...
            if self.__redraw_required:
                self.__redraw(terminal)
            if counter == 0:
                title_bar = []
                for view in self.__views.values():
                    view.update(terminal)
                    view.blink(terminal)
                    title_bar.append(f"{view.get_head_index()}")
                terminal.print(ANSI.title_bar(" | ".join(title_bar)), 1, 1)
                terminal.flush()
                counter = ViewController.UPDATES_PER_SECOND
            elif counter == ViewController.BLINKER_INTERVAL:
                for view in self.__views.values():
                    view.blink(terminal)
            else:
                for view in self.__views.values():
                    view.update_status(terminal)
                counter -= 1

    def __create_header(self) -> None:
        self.__views[View.HEADER] = View(header=True)

    def __redraw(self, terminal: Terminal) -> None:
        self.__redraw_required = False
        terminal.clear()
        char_rows, char_cols = terminal.get_size()
        # Set header size
        self.__views[View.HEADER].resize(rows=View.HEADER_HEIGHT, cols=char_cols)
        # Calculate and set each views size and positions
        view_rows = max(1, (char_rows - View.HEADER_HEIGHT) // View.ROW_MIN_SIZE)
        view_cols = max(1, (char_cols - 1) // View.COL_MIN_SIZE)
        view_col_size = char_cols // view_cols
        slots = iter(sorted(slot for slot in self.__views.keys() if slot > 0))
        for row in range(view_rows):
            for col in range(view_cols):
                try:
                    slot = next(slots)
                    self.__views[slot].resize(
                        origin_row=row * View.ROW_MIN_SIZE + View.HEADER_HEIGHT,
                        origin_col=col * view_col_size,
                        rows=View.ROW_MIN_SIZE,
                        cols=view_col_size,
                    )
                except StopIteration:
                    return

    @singledispatchmethod
    def __dispatch_message(self, message):
        raise NotImplementedError(
            f"Message type {type(message).__name__} not implemented"
        )

    @__dispatch_message.register
    def _(self, message: InitMessage):
        if message.provider == Message.Provider.SLOT:
            view = View()
            view.set_head_index(message.index + 1)
            self.__views[message.index] = view

    @__dispatch_message.register
    def _(self, message: RedrawMessage):
        self.__redraw_required = True

    @__dispatch_message.register
    def _(self, message: SleepMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_timer(int(message.sleep_time + 1))
            if message.provider == Message.Provider.DOWNLOAD:
                view.set_status(View.Status(View.Status.State.DOWNLOAD_WAIT))
            elif message.provider == Message.Provider.CHANNEL:
                view.reset()
                view.set_status(
                    status=View.Status(
                        View.Status.State.SLEEPING,
                        provider="Sleeping",
                        message=f"ETA {Util.get_time(int(time()) + message.sleep_time)}",
                    )
                )
            else:
                view.set_status(View.Status(View.Status.State.PROCESS_WAIT))

    @__dispatch_message.register
    def _(self, message: CountMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_count(message.value)

    @__dispatch_message.register
    def _(self, message: PathMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_filepath(message.path)

    @__dispatch_message.register
    def _(self, message: EntityMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_item_entity(message.entity)
            if message.provider == Message.Provider.CHANNEL:
                if message.entity.top_name:
                    view.set_top(message.entity.top_name)

    @__dispatch_message.register
    def _(self, message: ProgressMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_item_progress(message.progress)
            if message.provider == Message.Provider.HOOK:
                if message.progress.process == Hook.State.PROGRESS:
                    if message.progress.status == Hook.Status.DOWNLOADING:
                        view.set_status(View.Status(View.Status.State.DOWNLOAD))
                else:
                    if message.progress.status == Hook.Status.STARTED:
                        view.set_status(
                            View.Status(
                                state=View.Status.State.PROCESS,
                                provider=Hook.State.process(message.progress.process),
                                message=message.progress.status,
                            )
                        )
                    elif message.progress.status == Hook.Status.FINISHED:
                        view.set_status(
                            View.Status(
                                state=View.Status.State.INACTIVE,
                                provider=Hook.State.process(message.progress.process),
                                message=message.progress.status,
                            )
                        )

    @__dispatch_message.register
    def _(self, message: MediaMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_item_media(message.media)

    @__dispatch_message.register
    def _(self, message: InfoMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            if isinstance(message.provider, str):
                view.set_status(
                    status=View.Status(
                        View.Status.State.PROCESS,
                        provider=message.provider,
                        message=message.target,
                    )
                )

    @__dispatch_message.register
    def _(self, message: WarningMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            if isinstance(message.provider, str):
                view.set_status(
                    status=View.Status(
                        View.Status.State.WARNING,
                        provider=message.target,
                        message=message.message,
                    )
                )

    @__dispatch_message.register
    def _(self, message: ErrorMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            parts = message.message.split(":", maxsplit=1)
            if len(parts) == 2:
                view.set_status(
                    status=View.Status(
                        View.Status.State.ERROR,
                        provider=parts[0].strip(),
                        message=parts[1].strip(),
                    )
                )
            elif isinstance(message.provider, str):
                view.set_status(
                    status=View.Status(
                        View.Status.State.ERROR,
                        provider=f"{message.provider}/{message.target}",
                        message=message.message,
                    )
                )
            else:
                view.set_status(
                    status=View.Status(
                        View.Status.State.ERROR,
                        provider=f"{message.provider}",
                        message=message.message,
                    )
                )

    def get_queue(self) -> Queue:
        return self.__queue

    def halt(self):
        self.__ready = False
        self.__queue.put(HaltMessage())
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
