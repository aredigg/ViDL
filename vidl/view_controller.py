from functools import singledispatchmethod
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic_ns, time

from .ansi import ANSI
from .hook import Hook
from .message import (
    CountMessage,
    CutoffMessage,
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
    UrlMessage,
    WarningMessage,
)
from .terminal import Terminal
from .util import Util
from .view import View


class ViewController:
    index: int = 0
    UPDATES_PER_SECOND: int = 4
    updates_per_second: int = -(-UPDATES_PER_SECOND // len(View.ANIMATED)) * len(
        View.ANIMATED
    )
    RUN_LOOP_WAIT: float = 1 / updates_per_second

    def __init__(self) -> None:
        self.__ready: bool = False
        self.__views: dict[int, View] = {}
        #        self.__slots = set()
        self.__redraw_required = False
        self.__input_queue: Queue[str] = Queue()
        self.__queue: Queue[Message] = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(
            target=self.__run, name=f"ViewController-{ViewController.index}"
        )
        self.__thread.start()
        ViewController.index += 1

    def __run(self) -> None:
        with Terminal(self.__input_queue) as terminal:
            self.__create_header()
            self.__redraw(terminal)
            self.__ready = True
            completed = False
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
                tacho = (
                    (monotonic_ns() % 1_000_000_000)
                    * ViewController.updates_per_second
                    // 1_000_000_000
                )
                if tacho == 0 and not completed:
                    title_bar: list[str] = []
                    for view in self.__views.values():
                        view.update(terminal, tacho)
                        if view.get_top_index() > 0:
                            title_bar.append(
                                f"{view.get_top_index()} {view.get_status().emoji()}{view.countdown()}"
                            )
                    terminal.print(ANSI.title_bar(" | ".join(title_bar)), 1, 1)
                    completed = True
                else:
                    completed = False
                    for view in self.__views.values():
                        view.update_status(terminal, tacho)
                terminal.flush()

    def __create_header(self) -> None:
        self.__views[View.HEADER] = View(header=True)

    def __redraw(self, terminal: Terminal) -> None:
        self.__redraw_required = False
        terminal.clear()
        char_rows, char_cols = terminal.get_size()
        # Set header size
        _ = self.__views[View.HEADER].resize(rows=View.HEADER_HEIGHT, cols=char_cols)
        # Calculate and set each views size and positions
        view_rows = max(1, (char_rows - View.HEADER_HEIGHT) // View.ROW_MIN_SIZE)
        view_cols = max(1, (char_cols - 1) // View.COL_MIN_SIZE)
        view_col_size = char_cols // view_cols
        slots = iter(sorted(slot for slot in self.__views if slot >= 0))
        for row in range(view_rows):
            for col in range(view_cols):
                try:
                    slot = next(slots)
                    _ = self.__views[slot].resize(
                        origin_row=row * View.ROW_MIN_SIZE + View.HEADER_HEIGHT,
                        origin_col=col * view_col_size + 1,
                        rows=View.ROW_MIN_SIZE,
                        cols=view_col_size,
                    )
                except StopIteration:
                    return

    @singledispatchmethod
    def __dispatch_message(self, message: str) -> None:
        raise NotImplementedError(
            f"Message type {type(message).__name__} not implemented"
        )

    @__dispatch_message.register
    def _(self, message: InitMessage):
        if message.provider == Message.Provider.SLOT:
            view = View()
            view.set_top_index(message.index + 1)
            self.__views[message.index] = view
            self.__redraw_required = True

    @__dispatch_message.register
    def _(self, _message: RedrawMessage):
        self.__redraw_required = True

    @__dispatch_message.register
    def _(self, message: SleepMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_timer(int(message.sleep_time + 1))
            if message.provider == Message.Provider.DOWNLOAD:
                if message.required:
                    view.set_status(View.Status(View.Status.State.DOWNLOAD_REQ_WAIT))
                else:
                    view.set_status(View.Status(View.Status.State.DOWNLOAD_WAIT))
            elif message.provider == Message.Provider.CHANNEL:
                view.reset()
                view.set_status(
                    status=View.Status(
                        View.Status.State.SLEEPING,
                        provider="Sleeping",
                        message=f"ETA {Util.get_time(int(time() + message.sleep_time))}",
                    )
                )
            else:
                if view.get_status().state not in (
                    View.Status.State.ERROR,
                    View.Status.State.WARNING,
                ):
                    view.set_status(
                        View.Status(
                            state=View.Status.State.PROCESS_WAIT,
                            provider=view.get_status().provider,
                            message=view.get_status().message,
                        )
                    )

    @__dispatch_message.register
    def _(self, message: CountMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_count(message.value)

    @__dispatch_message.register
    def _(self, message: CutoffMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_cutoff(message.value)

    @__dispatch_message.register
    def _(self, message: PathMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_filepath(message.path)

    @__dispatch_message.register
    def _(self, message: UrlMessage):
        if message.index in self.__views:
            ...
            # view = self.__views[message.index]
            # view.set_url(message.url)

    @__dispatch_message.register
    def _(self, message: EntityMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_item_entity(message.entity)
            if message.provider == Message.Provider.CHANNEL and message.entity.top_name:
                view.set_top(message.entity.top_name)

    @__dispatch_message.register
    def _(self, message: ProgressMessage):
        if message.index in self.__views:
            view = self.__views[message.index]
            view.set_item_progress(message.progress)
            if message.provider == Message.Provider.HOOK:
                if (
                    message.progress.process.lower()
                    == Hook.State.PROGRESS.value.lower()
                ):
                    if (
                        message.progress.status.lower()
                        == Hook.Status.DOWNLOADING.value.lower()
                    ) and view.get_status().state in (
                        View.Status.State.DOWNLOAD_WAIT,
                        View.Status.State.DOWNLOAD_REQ_WAIT,
                    ):
                        view.set_timer(0)
                        view.set_status(View.Status(View.Status.State.DOWNLOAD))
                else:
                    if message.progress.status == Hook.Status.STARTED.value:
                        view.set_status(
                            View.Status(
                                state=View.Status.State.PROCESS,
                                provider=Hook.State.process(message.progress.process),
                                message=message.progress.status,
                            )
                        )
                    elif message.progress.status == Hook.Status.FINISHED.value:
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
            view.set_status(
                status=View.Status(
                    View.Status.State.PROCESS,
                    provider=message.target,
                    message=message.message,
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
            elif message.provider == Message.Provider.CHANNEL:
                view.set_status(
                    status=View.Status(
                        View.Status.State.ERROR,
                        provider=f"{message.target}",
                        message=message.message,
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

    def get_queue(self) -> Queue[Message]:
        return self.__queue

    def get_input_queue(self) -> Queue[str]:
        return self.__input_queue

    def halt(self):
        self.__views[View.HEADER].set_status(
            View.Status(
                state=View.Status.State.INACTIVE, message="Shutdown in progress"
            )
        )
        self.__ready = False
        self.__queue.put(HaltMessage())
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
