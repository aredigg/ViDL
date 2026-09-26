# pyright: standard
import time
import unittest

from tests.helpers import IsolatedTestCase
from vidl.config import Config
from vidl.item import Item
from vidl.util import Util

Info = dict[str, object]


class TestGetters(unittest.TestCase):
    def test_get_str(self):
        info = {"a": "text", "b": 5, "c": ""}
        self.assertEqual(Item.get_str(info, "a"), "text")
        self.assertEqual(Item.get_str(info, "b"), "")
        self.assertEqual(Item.get_str(info, "c", "default"), "default")
        self.assertEqual(Item.get_str(info, "missing", "default"), "default")
        self.assertEqual(Item.get_str({}, "a", "default"), "default")

    def test_get_int(self):
        info = {"a": 5, "b": 5.9, "c": "5", "d": 0}
        self.assertEqual(Item.get_int(info, "a"), 5)
        self.assertEqual(Item.get_int(info, "b"), 5)
        self.assertEqual(Item.get_int(info, "c", -1), -1)
        self.assertEqual(Item.get_int(info, "d", -1), -1)

    def test_get_flt(self):
        info = {"a": 1.5, "b": 2, "c": None}
        self.assertEqual(Item.get_flt(info, "a"), 1.5)
        self.assertEqual(Item.get_flt(info, "b"), 2.0)
        self.assertIsInstance(Item.get_flt(info, "b"), float)
        self.assertEqual(Item.get_flt(info, "c", 3.0), 3.0)

    def test_get_lst(self):
        info = {
            "list": [1, 2],
            "tuple": (1, 2),
            "generator": (i for i in range(2)),
            "str": "ab",
            "dict": {"a": 1},
        }
        self.assertEqual(Item.get_lst(info, "list"), [1, 2])
        self.assertEqual(Item.get_lst(info, "tuple"), [1, 2])
        self.assertEqual(Item.get_lst(info, "generator"), [0, 1])
        self.assertEqual(Item.get_lst(info, "str"), [])
        self.assertEqual(Item.get_lst(info, "dict"), [])
        self.assertEqual(Item.get_lst(info, "missing"), [])

    def test_get_dct_keeps_string_keys_only(self):
        info = {"d": {"a": 1, 2: "b"}, "s": "text"}
        self.assertEqual(Item.get_dct(info, "d"), {"a": 1})
        self.assertEqual(Item.get_dct(info, "s"), {})
        self.assertEqual(Item.get_dct(info, "missing"), {})


class TestEntity(unittest.TestCase):
    def test_fields(self):
        info = {
            "id": "abc",
            "title": "Title",
            "timestamp": 100,
            "playlist_index": 3,
            "availability": "public",
            "age_limit": 18,
        }
        entity = Item.get_entity(info, name="Channel", index=2)
        self.assertEqual(
            entity,
            Item.Entity("abc", 2, 3, 100, "Title", "public", 18, "", "Channel"),
        )

    def test_date_falls_back_to_epoch(self):
        self.assertEqual(Item.get_entity({"epoch": 50}, "", 1).date, 50)

    def test_stream_state(self):
        self.assertEqual(Item.get_entity({"is_live": True}, "", 1).stream, "LIVE")
        for status in ("is_upcoming", "was_live", "post_live"):
            entity = Item.get_entity({"live_status": status}, "", 1)
            self.assertEqual(entity.stream, "STREAM")
        self.assertEqual(Item.get_entity({"live_status": "not_live"}, "", 1).stream, "")


class TestProgress(unittest.TestCase):
    def test_bytes_bitrate_and_remaining_time(self):
        # 1310720 bytes in 10 s is 1024 kbps, the same amount remains
        progress = Item.get_progress(
            {
                "elapsed": 10.0,
                "downloaded_bytes": 1_310_720,
                "total_bytes": 2_621_440,
                "filename": "/tmp/abc.f137.mp4.part",
                "status": "downloading",
                "eta": 12,
            }
        )
        self.assertEqual(progress.bitrate, 1024)
        self.assertEqual(progress.time_total, 20.0)
        self.assertEqual(progress.percent, 50.0)
        self.assertEqual(progress.extension, "PART")
        self.assertEqual(progress.eta, 12)
        self.assertEqual(progress.process, "Progress")
        self.assertEqual(progress.status, "downloading")

    def test_total_bytes_estimate(self):
        progress = Item.get_progress(
            {"downloaded_bytes": 25, "total_bytes_estimate": 100}
        )
        self.assertEqual((progress.size_total, progress.percent), (100, 25.0))

    def test_percent_from_fragments(self):
        progress = Item.get_progress({"fragment_index": 3, "fragment_count": 12})
        self.assertEqual(progress.percent, 25.0)

    def test_reported_percent_takes_precedence(self):
        progress = Item.get_progress(
            {"_percent": 42.5, "downloaded_bytes": 1, "total_bytes": 2}
        )
        self.assertEqual(progress.percent, 42.5)

    def test_postprocessor(self):
        progress = Item.get_progress({"postprocessor": "Merger", "status": "started"})
        self.assertEqual((progress.process, progress.status), ("Merger", "started"))
        self.assertEqual((progress.bitrate, progress.percent), (0, 0))


class TestMedia(unittest.TestCase):
    def test_video_audio_and_subtitles(self):
        media = Item.get_media(
            {
                "width": 1920,
                "height": 1080,
                "fps": 29.97,
                "dynamic_range": "HDR10",
                "vcodec": "avc1.640028",
                "asr": 48000,
                "audio_channels": 2,
                "acodec": "mp4a.40.2",
                "requested_subtitles": {"en-US": {}, "no": {}},
                "duration": 90,
                "ext": "mp4",
                "protocol": "https",
            }
        )
        self.assertEqual(media.video_stat, "1920x1080@29 HDR10 AVC1")
        self.assertEqual(media.audio_stat, "48000x2 MP4A")
        self.assertEqual(media.subtitle_stat, "en/no")
        self.assertEqual((media.length, media.extension), (90, "mp4"))
        self.assertEqual(media.container, "https")

    def test_missing_information(self):
        media = Item.get_media({"width": 640, "height": 360})
        self.assertEqual(media.video_stat, "640x360 SDR ----")
        self.assertEqual((media.audio_stat, media.subtitle_stat), ("", ""))
        self.assertEqual(Item.get_media({}), Item.Media(0, "", "", "", "", ""))


class TestFilters(IsolatedTestCase):
    def test_no_vertical(self):
        vertical: Info = {"width": 1080, "height": 1920}
        horizontal: Info = {"width": 1920, "height": 1080}
        Config.settings["Download"]["allow_vertical"] = False
        self.assertTrue(Item.no_vertical(vertical))
        self.assertFalse(Item.no_vertical(horizontal))
        self.assertFalse(Item.no_vertical({}))
        Config.settings["Download"]["allow_vertical"] = True
        self.assertFalse(Item.no_vertical(vertical))

    def test_high_resolution(self):
        Config.settings["Download"]["minimum_resolution"] = 720
        self.assertFalse(Item.high_resolution({"height": 480}))
        self.assertTrue(Item.high_resolution({"height": 720}))
        self.assertTrue(Item.high_resolution({}))  # unknown height is accepted
        Config.settings["Download"]["minimum_resolution"] = 0
        self.assertTrue(Item.high_resolution({"height": 144}))

    def test_outside_deferred(self):
        now = int(time.time())
        recent: Info = {"timestamp": now - Util.SECONDS_PER_DAY}
        old: Info = {"timestamp": now - 3 * Util.SECONDS_PER_DAY}
        full_hd: Info = {"height": 1080}
        uhd: Info = {"height": 2160}
        Config.settings["Download"]["resolution_defer"] = 0
        self.assertTrue(Item.outside_deferred(recent, full_hd))
        Config.settings["Download"]["resolution_defer"] = 2
        self.assertFalse(Item.outside_deferred(recent, full_hd))
        self.assertTrue(Item.outside_deferred(old, full_hd))
        self.assertTrue(Item.outside_deferred(recent, uhd))
        self.assertGreater(2160, Item.DEFER_EXEMPT_HEIGHT)


class TestFormats(unittest.TestCase):
    def test_enumerate_best_format_prefers_mp4_height_fps_and_hdr(self):
        formats = [
            {"ext": "webm", "height": 2160},
            {"ext": "mp4", "height": 1080, "fps": 30},
            {"ext": "mp4", "height": 1080, "fps": 60},
            {"ext": "mp4", "height": 1080, "fps": 60, "dynamic_range": "HDR10"},
            {"ext": "mp4", "height": 720, "fps": 60},
        ]
        best = Item.enumerate_best_format({"formats": formats})
        self.assertEqual(best, formats[3])

    def test_enumerate_best_format_other_extension_and_none(self):
        formats = [{"ext": "webm", "height": 2160}, {"ext": "mp4", "height": 720}]
        self.assertEqual(
            Item.enumerate_best_format({"formats": formats}, "webm"), formats[0]
        )
        self.assertEqual(Item.enumerate_best_format({"formats": [{"ext": "webm"}]}), {})
        self.assertEqual(Item.enumerate_best_format({}), {})

    def test_requested_format_merges_audio_into_video(self):
        info: Info = {
            "requested_formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 1080},
                {"vcodec": "none", "acodec": "mp4a", "asr": 44100, "audio_channels": 2},
            ]
        }
        merged = Item.requested_format(info)
        self.assertEqual(merged["height"], 1080)
        self.assertEqual(
            (merged["acodec"], merged["asr"], merged["audio_channels"]),
            ("mp4a", 44100, 2),
        )

    def test_requested_format_falls_back_to_best_format(self):
        info: Info = {"formats": [{"ext": "mp4", "height": 480}]}
        self.assertEqual(Item.requested_format(info), {"ext": "mp4", "height": 480})


if __name__ == "__main__":
    unittest.main()
