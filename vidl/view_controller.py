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
    TICK_NS: int = 1_000_000_000 // updates_per_second
    # Message driven renders are coalesced to at most one per interval
    MIN_RENDER_INTERVAL_NS: int = 100_000_000

    def __init__(self) -> None:
        # The header exists before the thread starts, so halt() is always safe
        self.__views: dict[int, View] = {View.HEADER: View(header=True)}
        self.__redraw_required = False
        self.__input_queue: Queue[str] = Queue()
        self.__queue: Queue[Message] = Queue()
        self.__halt_event = Event()
        self.__thread = Thread(
            target=self.__run, name=f"ViewController-{ViewController.index}"
        )
        self.__thread.start()
        ViewController.index += 1

    # Rendering: a full update once per second (tacho 0), status lines on every
    # tick for the animation, and message driven status updates in between,
    # limited to MIN_RENDER_INTERVAL_NS
    def __run(self) -> None:
        with Terminal(self.__input_queue) as terminal:
            self.__redraw(terminal)
            full_update = True
            pending = False
            next_tick = 0
            last_render = 0
            while not self.__halt_event.is_set():
                deadline = next_tick
                if pending or full_update:
                    deadline = min(
                        deadline, last_render + ViewController.MIN_RENDER_INTERVAL_NS
                    )
                timeout = max(0, deadline - monotonic_ns()) / 1_000_000_000
                if self.__process_messages(timeout):
                    pending = True
                if self.__redraw_required:
                    self.__redraw(terminal)
                    full_update = True
                now = monotonic_ns()
                tick = now // ViewController.TICK_NS
                tacho = tick % ViewController.updates_per_second
                due = now >= next_tick
                if due:
                    next_tick = (tick + 1) * ViewController.TICK_NS
                    if tacho == 0:
                        full_update = True
                throttled = now - last_render < ViewController.MIN_RENDER_INTERVAL_NS
                if not due and (throttled or not (pending or full_update)):
                    continue
                if full_update:
                    self.__update(terminal, tacho)
                else:
                    for view in self.__views.values():
                        view.update_status(terminal, tacho)
                full_update = pending = False
                last_render = now
                terminal.flush()

    # Waits up to timeout for a message, then handles all queued messages
    # within the render interval, returns True if any message was handled
    def __process_messages(self, timeout: float) -> bool:
        try:
            message = self.__queue.get(timeout=timeout)
        except Empty:
            return False
        budget_end = monotonic_ns() + ViewController.MIN_RENDER_INTERVAL_NS
        while True:
            if not isinstance(message, HaltMessage):
                self.__dispatch_message(message)
            if monotonic_ns() >= budget_end:
                return True
            try:
                message = self.__queue.get_nowait()
            except Empty:
                return True

    def __update(self, terminal: Terminal, tacho: int) -> None:
        title_bar: list[str] = []
        for view in self.__views.values():
            view.update(terminal, tacho)
            if view.get_top_index() > 0:
                title_bar.append(
                    f"{view.get_top_index()} "
                    + f"{view.get_status().emoji()}{view.countdown()}"
                )
        terminal.print(ANSI.title_bar(" | ".join(title_bar)), 1, 1)

    def __redraw(self, terminal: Terminal) -> None:
        self.__redraw_required = False
        terminal.clear()
        char_rows, char_cols = terminal.get_size()
        # Set header size
        _ = self.__views[View.HEADER].resize(rows=View.HEADER_HEIGHT, cols=char_cols)
        # Calculate and set each views size and positions
        for view in self.__views.values():
            view.set_visible(False)
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
                    self.__views[slot].set_visible(True)
                except StopIteration:
                    return

    @singledispatchmethod
    def __dispatch_message(self, message: Message) -> None:
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
                if message.required:
                    view.set_status(
                        status=View.Status(
                            View.Status.State.SLEEPING,
                            provider="Mandatory sleep",
                            message=f"ETA {Util.get_time(int(time() + message.sleep_time))}",
                        )
                    )
                else:
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
            else:
                view.set_status(
                    status=View.Status(
                        View.Status.State.ERROR,
                        provider=f"{message.provider.value}/{message.target}",
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
        self.__queue.put(HaltMessage())
        self.__halt_event.set()

    def join(self):
        self.__thread.join()
