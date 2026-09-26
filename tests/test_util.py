# pyright: standard
import unittest
from datetime import datetime

from tests.helpers import IsolatedTestCase
from vidl.util import Util


class TestFormatSeconds(unittest.TestCase):
    def test_minutes_and_seconds_below_one_hour(self):
        self.assertEqual(Util.format_seconds(0), "00′00″")
        self.assertEqual(Util.format_seconds(65), "01′05″")
        self.assertEqual(Util.format_seconds(3570), "59′30″")

    def test_negative_values_use_the_absolute_value(self):
        self.assertEqual(Util.format_seconds(-65), "01′05″")

    def test_hours_are_shown_as_hours_and_rounded_up_minutes(self):
        self.assertEqual(Util.format_seconds(3725), "01:03′")
        self.assertEqual(Util.format_seconds(3720), "01:02′")

    def test_three_parts(self):
        self.assertEqual(Util.format_seconds(3725, two_parts=False), "01:02:05")
        self.assertEqual(Util.format_seconds(5, two_parts=False), "00:00:05")

    def test_without_seconds_rounds_minutes_up(self):
        self.assertEqual(Util.format_seconds(125, include_seconds=False), "00:03′")
        self.assertEqual(Util.format_seconds(120, include_seconds=False), "00:02′")

    def test_rounding_up_carries_into_hours(self):
        self.assertEqual(Util.format_seconds(3570, include_seconds=False), "01:00′")


class TestDates(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.use_utc()

    def test_zero_epoch_is_empty(self):
        self.assertEqual(Util.get_date(0), "")
        self.assertEqual(Util.get_time(0), "")

    def test_epoch_is_formatted_in_local_time(self):
        epoch = 365 * Util.SECONDS_PER_DAY + Util.SECONDS_PER_HOUR + 5 * 60
        self.assertEqual(Util.get_date(epoch), "1971-01-01")
        self.assertEqual(Util.get_time(epoch), "01:05")

    def test_none_is_now(self):
        now = datetime.now().astimezone()
        self.assertEqual(Util.get_date(), now.strftime(Util.date_fmt))
        self.assertRegex(Util.get_time(), r"^\d\d:\d\d$")

    def test_time_constants(self):
        self.assertEqual(Util.SECONDS_PER_HOUR, 60 * Util.SECONDS_PER_MINUTE)
        self.assertEqual(Util.SECONDS_PER_DAY, 24 * Util.SECONDS_PER_HOUR)


if __name__ == "__main__":
    unittest.main()
