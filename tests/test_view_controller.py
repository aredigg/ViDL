# pyright: standard
import time
import unittest

from tests.helpers import IsolatedTestCase, private, wait_until
from vidl.item import Item
from vidl.message import (
    CountMessage,
    CutoffMessage,
    EntityMessage,
    ErrorMessage,
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
from vidl.view import View
from vidl.view_controller import ViewController

State = View.Status.State
CHANNEL, DOWNLOAD, HOOK, SLOT = (
    Message.Provider.CHANNEL,
    Message.Provider.DOWNLOAD,
    Message.Provider.HOOK,
    Message.Provider.SLOT,
)


class ControllerTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = self.quiet_console(cols=130, rows=40)

    def start(self) -> ViewController:
        controller = ViewController()
        self.addCleanup(self.stop, controller)
        return controller

    def stop(self, controller: ViewController) -> None:
        controller.halt()
        controller.join()

    def views(self, controller: ViewController) -> dict[int, View]:
        return private(controller, ViewController, "views")


class TestDispatch(ControllerTestCase):
    """Handlers are called directly on a stopped controller."""

    def setUp(self):
        super().setUp()
        self.controller = self.start()
        self.stop(self.controller)
        self.dispatch(InitMessage(index=0, provider=SLOT))
        self.view = self.views(self.controller)[0]

    def dispatch(self, message: Message) -> None:
        private(self.controller, ViewController, "dispatch_message")(message)

    def state(self) -> State:
        return self.view.get_status().state

    def test_init_creates_a_numbered_view(self):
        self.assertEqual(self.view.get_top_index(), 1)
        self.dispatch(InitMessage(index=1, provider=CHANNEL))  # only slots create views
        self.assertNotIn(1, self.views(self.controller))

    def test_messages_for_unknown_slots_are_ignored(self):
        for message in (
            SleepMessage(index=9, provider=CHANNEL, sleep_time=1),
            ErrorMessage(index=9, provider=CHANNEL, target="t", message="m"),
            CountMessage(index=9, provider=CHANNEL, value=1),
        ):
            self.dispatch(message)

    def test_unknown_message_type_is_an_error(self):
        with self.assertRaises(NotImplementedError):
            self.dispatch(Message())

    def test_download_sleep(self):
        self.dispatch(SleepMessage(index=0, provider=DOWNLOAD, sleep_time=5))
        self.assertEqual(self.state(), State.DOWNLOAD_WAIT)
        self.dispatch(
            SleepMessage(index=0, provider=DOWNLOAD, sleep_time=5, required=True)
        )
        self.assertEqual(self.state(), State.DOWNLOAD_REQ_WAIT)

    def test_channel_sleep_resets_the_view(self):
        self.view.set_item_entity(Item.Entity("a", 1, 1, 0, "t", "", 0, "", ""))
        self.dispatch(SleepMessage(index=0, provider=CHANNEL, sleep_time=5))
        status = self.view.get_status()
        self.assertEqual((status.state, status.provider), (State.SLEEPING, "Sleeping"))
        self.assertTrue(status.message.startswith("ETA "))
        self.assertIsNone(private(self.view, View, "item_entity"))
        self.dispatch(
            SleepMessage(index=0, provider=CHANNEL, sleep_time=5, required=True)
        )
        self.assertEqual(self.view.get_status().provider, "Mandatory sleep")

    def test_other_sleep_keeps_errors(self):
        self.dispatch(SleepMessage(index=0, provider=HOOK, sleep_time=5))
        self.assertEqual(self.state(), State.PROCESS_WAIT)
        self.view.set_status(View.Status(State.ERROR))
        self.dispatch(SleepMessage(index=0, provider=HOOK, sleep_time=5))
        self.assertEqual(self.state(), State.ERROR)

    def test_top_information(self):
        entity = Item.Entity("a", 1, 1, 0, "t", "", 0, "", "Channel name")
        self.dispatch(EntityMessage(index=0, provider=CHANNEL, entity=entity))
        self.dispatch(CountMessage(index=0, provider=CHANNEL, value=7))
        self.dispatch(CutoffMessage(index=0, provider=CHANNEL, value=86_400))
        self.dispatch(PathMessage(index=0, provider=DOWNLOAD, path="/tmp/a.NA"))
        self.dispatch(
            MediaMessage(
                index=0, provider=CHANNEL, media=Item.Media(1, "", "", "", "", "")
            )
        )
        top = private(self.view, View, "top")
        self.assertEqual((top.name, top.count), ("Channel name", "(7)"))
        self.assertTrue(top.cutoff)
        self.assertIs(private(self.view, View, "item_entity"), entity)
        self.assertEqual(private(self.view, View, "temp_filepath"), "/tmp/a.NA")
        self.assertIsNotNone(private(self.view, View, "item_media"))

    def test_entities_without_top_name_keep_the_title(self):
        self.view.set_top("Channel")
        entity = Item.Entity("a", 1, 1, 0, "t", "", 0, "", "")
        self.dispatch(EntityMessage(index=0, provider=CHANNEL, entity=entity))
        self.assertEqual(private(self.view, View, "top").name, "Channel")

    def test_progress_starts_the_download(self):
        self.dispatch(SleepMessage(index=0, provider=DOWNLOAD, sleep_time=5))
        progress = Item.Progress(status="downloading", process="Progress")
        self.dispatch(ProgressMessage(index=0, provider=HOOK, progress=progress))
        self.assertEqual(self.state(), State.DOWNLOAD)
        self.assertEqual(self.view.timer(), "00′00″")

    def test_postprocessing_states(self):
        started = Item.Progress(status="started", process="Merger")
        self.dispatch(ProgressMessage(index=0, provider=HOOK, progress=started))
        status = self.view.get_status()
        self.assertEqual((status.state, status.provider), (State.PROCESS, "Merge"))
        finished = Item.Progress(status="finished", process="Merger")
        self.dispatch(ProgressMessage(index=0, provider=HOOK, progress=finished))
        self.assertEqual(self.state(), State.INACTIVE)

    def test_info_and_warning(self):
        self.dispatch(InfoMessage(index=0, provider=CHANNEL, target="t", message="m"))
        self.assertEqual(self.state(), State.PROCESS)
        self.dispatch(
            WarningMessage(index=0, provider=CHANNEL, target="t", message="m")
        )
        status = self.view.get_status()
        self.assertEqual(
            (status.state, status.provider, status.message), (State.WARNING, "t", "m")
        )

    def test_errors(self):
        cases = (
            (
                ErrorMessage(
                    index=0, provider=CHANNEL, target="t", message="id: failed"
                ),
                ("id", "failed"),
            ),
            (
                ErrorMessage(index=0, provider=CHANNEL, target="t", message="failed"),
                ("t", "failed"),
            ),
            (
                ErrorMessage(index=0, provider=SLOT, target="t", message="failed"),
                ("slot/t", "failed"),
            ),
        )
        for message, (provider, text) in cases:
            with self.subTest(message=message):
                self.dispatch(message)
                status = self.view.get_status()
                self.assertEqual(
                    (status.state, status.provider, status.message),
                    (State.ERROR, provider, text),
                )


class TestRunning(ControllerTestCase):
    def test_halt_right_after_start_never_hangs(self):
        for _ in range(20):
            controller = ViewController()
            controller.halt()
            controller.join()

    def test_halt_shows_shutdown_in_the_header(self):
        controller = self.start()
        self.stop(controller)
        header = self.views(controller)[View.HEADER]
        self.assertEqual(header.get_status().message, "Shutdown in progress")

    def test_queues(self):
        controller = self.start()
        self.assertIsNot(controller.get_queue(), controller.get_input_queue())

    def test_panels_are_drawn(self):
        controller = self.start()
        for index in range(2):
            controller.get_queue().put(InitMessage(index=index, provider=SLOT))
        self.assertTrue(wait_until(lambda: "╭" in self.stdout.getvalue()))
        self.assertTrue(wait_until(lambda: "\x1b]2;1 " in self.stdout.getvalue()))
        output = self.stdout.getvalue()
        self.assertIn("ViDL", output)
        blank = View.COUNTDOWN[0]
        self.assertIn(f"\x1b]2;1 ⚫ {blank} | 2 ⚫ {blank}\x1b\\", output)

    def test_layout_hides_panels_that_do_not_fit(self):
        controller = self.start()
        for index in range(3):
            controller.get_queue().put(InitMessage(index=index, provider=SLOT))
        self.assertTrue(wait_until(lambda: len(self.views(controller)) == 4))
        time.sleep(0.3)
        self.stop(controller)
        # 40 rows fit two 12 row panels below the header, 130 columns fit one
        visible = [
            private(self.views(controller)[i], View, "visible") for i in range(3)
        ]
        self.assertEqual(visible, [True, True, False])

    def test_redraw_request_is_handled(self):
        controller = self.start()
        self.assertTrue(wait_until(lambda: "ViDL" in self.stdout.getvalue()))
        self.stdout.truncate(0)
        self.stdout.seek(0)
        controller.get_queue().put(RedrawMessage())
        self.assertTrue(wait_until(lambda: "\x1b[2J" in self.stdout.getvalue()))

    def test_message_flood_is_handled_in_batches(self):
        controller = self.start()
        queue = controller.get_queue()
        for index in range(6):
            queue.put(InitMessage(index=index, provider=SLOT))
        started = time.monotonic()
        for n in range(20_000):
            progress = Item.Progress(
                size_current=n, size_total=20_000, status="downloading"
            )
            queue.put(ProgressMessage(index=n % 6, provider=HOOK, progress=progress))
        self.assertTrue(wait_until(queue.empty, timeout=10))
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
