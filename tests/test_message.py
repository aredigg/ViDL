# pyright: standard
import dataclasses
import unittest

from vidl.item import Item
from vidl.message import (
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
    SlotMessage,
    StatusMessage,
    WarningMessage,
)

SLOT = Message.Provider.SLOT


class TestMessage(unittest.TestCase):
    def test_provider_values(self):
        self.assertEqual(
            {p.value for p in Message.Provider}, {"channel", "download", "hook", "slot"}
        )

    def test_control_messages_have_no_fields(self):
        for cls in (HaltMessage, RedrawMessage):
            self.assertEqual(dataclasses.fields(cls()), ())
            self.assertIsInstance(cls(), Message)
            self.assertNotIsInstance(cls(), SlotMessage)

    def test_slot_messages_carry_index_and_provider(self):
        messages = [
            InitMessage(index=1, provider=SLOT),
            SleepMessage(index=1, provider=SLOT, sleep_time=2.5),
            CountMessage(index=1, provider=SLOT, value=3),
            CutoffMessage(index=1, provider=SLOT, value=4),
            PathMessage(index=1, provider=SLOT, path="/p"),
            ProgressMessage(index=1, provider=SLOT, progress=Item.Progress()),
            MediaMessage(
                index=1, provider=SLOT, media=Item.Media(0, "", "", "", "", "")
            ),
            EntityMessage(
                index=1,
                provider=SLOT,
                entity=Item.Entity("id", 1, 1, 0, "title", "", 0, "", "top"),
            ),
        ]
        for message in messages:
            self.assertIsInstance(message, SlotMessage)
            self.assertEqual((message.index, message.provider), (1, SLOT))

    def test_sleep_is_not_required_by_default(self):
        self.assertFalse(SleepMessage(index=0, provider=SLOT, sleep_time=1).required)

    def test_status_messages(self):
        for cls in (InfoMessage, WarningMessage, ErrorMessage):
            message = cls(index=0, provider=SLOT, target="t", message="m")
            self.assertIsInstance(message, StatusMessage)
            self.assertEqual((message.target, message.message), ("t", "m"))

    def test_equality_by_value(self):
        self.assertEqual(
            CountMessage(index=0, provider=SLOT, value=1),
            CountMessage(index=0, provider=SLOT, value=1),
        )


if __name__ == "__main__":
    unittest.main()
