# pyright: standard
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from threading import Event, Thread
from typing import Any, ClassVar, cast
from unittest import mock

from mutagen.mp4 import MP4, AtomDataType, MP4Cover, MP4FreeForm
from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP

from tests.helpers import IsolatedTestCase
from vidl.config import Config
from vidl.metadata import (
    HD_VIDEO_LEVELS,
    AtomPostProcessor,
    MetadataContext,
    MetadataPostProcessor,
    NewFileAtomPP,
    NewFileEmbedSubtitlePP,
    NewFileEmbedThumbnailPP,
    NewFileMetadataPP,
    atom_values,
    channel_name,
    channel_thumbnail,
    episode_date,
    episode_id,
    episode_number,
    hashtags,
    hd_video,
    is_new_download,
    iso_timestamp,
    keywords,
    network_name,
    new_file_atom_pp,
    new_file_embed_subtitle_pp,
    new_file_embed_thumbnail_pp,
    new_file_metadata_pp,
    season_number,
    short_description,
    synopsis,
    video_height,
)

YT3 = "https://yt3.googleusercontent.com"
AVATAR = f"{YT3}/ytc/AIdro_kQ1example=s900-c-k-c0x00ffffff-no-rj"
AVATAR_UNCROPPED = f"{YT3}/ytc/AIdro_kQ1example=s0"
BANNER = f"{YT3}/bannerExample=w1060-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj"
BANNER_TV = (
    f"{YT3}/bannerExample=w2120-fcrop64=1,00000000ffffffff-k-c0xffffffff-no-nd-rj"
)
BANNER_UNCROPPED = f"{YT3}/bannerExample=s0"
VIDEO_THUMBNAIL = "https://i.ytimg.com/vi/abc/maxresdefault.jpg"


def youtube_tab_thumbnails(
    avatar: bool = True, banner: bool = True
) -> list[dict[str, object]]:
    """Channel thumbnails as built by the youtube:tab extractor
    (_extract_metadata_from_tabs): avatars, then banners. _extract_thumbnails
    sets no "id", only the appended uncropped entries have one."""
    thumbnails: list[dict[str, object]] = []
    if avatar:
        thumbnails += [
            {"url": AVATAR, "height": 900, "width": 900},
            {"url": AVATAR_UNCROPPED, "id": "avatar_uncropped", "preference": 1},
        ]
    if banner:
        thumbnails += [
            {"url": BANNER, "height": 175, "width": 1060, "preference": -10},
            {"url": BANNER_TV, "height": 1192, "width": 2120, "preference": -10},
            {"url": BANNER_UNCROPPED, "id": "banner_uncropped", "preference": -5},
        ]
    return thumbnails


def channel_info(thumbnails: object) -> dict[str, object]:
    return {"_type": "playlist", "id": "UC123", "thumbnails": thumbnails}


class RaisingDict(dict[str, Any]):
    """An info dict that fails as soon as it is read or written."""

    def get(self, key: str, default: object = None) -> Any:
        raise RuntimeError("no access")

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("no access")

    def __setitem__(self, key: str, value: Any) -> None:
        raise RuntimeError("no access")


class RecordingLogger:
    def __init__(self, fail: bool = False) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.messages: list[str] = []
        self.fail = fail

    def debug(self, message: str) -> None:
        self.messages.append(message)

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("logger failed")
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


class RecordingPP(PostProcessor):
    """Records a copy of the info dict it sees."""

    def __init__(self) -> None:
        super().__init__(None)
        self.seen: list[dict[str, Any]] = []

    def run(self, information: Any) -> tuple[list[str], Any]:
        self.seen.append(copy.deepcopy(dict(information)))
        return [], information


class ReportingMetadataPP(MetadataPostProcessor):
    REPORT_PROGRESS: ClassVar[bool] = True


def youtube_dl(params: dict[str, object]) -> YoutubeDL:
    return cast(Callable[[dict[str, object]], YoutubeDL], YoutubeDL)(params)


class TestEpisodeNumber(unittest.TestCase):
    def test_table(self):
        cases: list[tuple[object, object, int | None]] = [
            (10, 1, 10),  # newest
            (10, 10, 1),  # oldest
            (10, 4, 7),
            (1, 1, 1),
            (10, 0, None),
            (10, -1, None),
            (10, 11, None),
            (0, 0, None),
            (0, 1, None),
            (-3, 1, None),
            (True, 1, None),
            (10, True, None),
            (10, False, None),
            (10, 1.0, None),
            ("10", "1", None),
            (None, 1, None),
            (10, None, None),
        ]
        for count, index, expected in cases:
            with self.subTest(count=count, index=index):
                self.assertEqual(
                    episode_number(cast(int, count), cast(int, index)), expected
                )


class TestChannelThumbnail(unittest.TestCase):
    def test_youtube_tab_prefers_uncropped_avatar(self):
        info = channel_info(youtube_tab_thumbnails())
        self.assertEqual(channel_thumbnail(info), AVATAR_UNCROPPED)

    def test_sanitized_ids(self):
        # After _sanitize_thumbnails every entry has an id, the index as str
        thumbnails = youtube_tab_thumbnails()
        for i, thumbnail in enumerate(thumbnails):
            thumbnail.setdefault("id", str(i))
        self.assertEqual(channel_thumbnail(channel_info(thumbnails)), AVATAR_UNCROPPED)

    def test_uncropped_beats_larger_avatar(self):
        thumbnails: list[dict[str, object]] = [
            {"id": "avatar", "url": AVATAR, "width": 900, "height": 900},
            {"id": "avatar_uncropped", "url": AVATAR_UNCROPPED},
        ]
        self.assertEqual(channel_thumbnail(channel_info(thumbnails)), AVATAR_UNCROPPED)

    def test_largest_avatar_then_preference(self):
        thumbnails: list[dict[str, object]] = [
            {"id": "avatar_88", "url": f"{YT3}/a=s88", "width": 88, "height": 88},
            {"id": "Avatar_900", "url": f"{YT3}/a=s900", "width": 900, "height": 900},
            {"id": "avatar_176", "url": f"{YT3}/a=s176", "width": 176, "height": 176},
        ]
        self.assertEqual(channel_thumbnail(channel_info(thumbnails)), f"{YT3}/a=s900")
        same_size: list[dict[str, object]] = [
            {"id": "avatar_a", "url": f"{YT3}/a", "width": 9, "height": 9},
            {
                "id": "avatar_b",
                "url": f"{YT3}/b",
                "width": 9,
                "height": 9,
                "preference": 2,
            },
            {
                "id": "avatar_c",
                "url": f"{YT3}/c",
                "width": 9,
                "height": 9,
                "preference": 1,
            },
        ]
        self.assertEqual(channel_thumbnail(channel_info(same_size)), f"{YT3}/b")
        # Without a preference an entry ranks as -1, like _sort_thumbnails
        negative: list[dict[str, object]] = [
            {"id": "avatar_a", "url": f"{YT3}/a", "preference": -2},
            {"id": "avatar_b", "url": f"{YT3}/b"},
        ]
        self.assertEqual(channel_thumbnail(channel_info(negative)), f"{YT3}/b")

    def test_banners_and_unnamed_entries_are_ignored(self):
        banners = youtube_tab_thumbnails(avatar=False)
        self.assertIsNone(channel_thumbnail(channel_info(banners)))
        # Plain avatars carry no id in unprocessed results
        unnamed = youtube_tab_thumbnails(banner=False)[:1]
        self.assertIsNone(channel_thumbnail(channel_info(unnamed)))
        mixed: list[dict[str, object]] = [
            {"id": "avatar_banner", "url": f"{YT3}/x", "width": 5000, "height": 900}
        ]
        self.assertIsNone(channel_thumbnail(channel_info(mixed)))
        playlist: list[dict[str, object]] = [
            {
                "url": "https://i.ytimg.com/vi/x/hqdefault.jpg",
                "width": 480,
                "height": 270,
            }
        ]
        self.assertIsNone(channel_thumbnail(channel_info(playlist)))

    def test_only_http_urls(self):
        thumbnails: list[dict[str, object]] = [
            {"id": "avatar_uncropped", "url": "ftp://yt3.example/a=s0"},
            {"id": "avatar", "url": AVATAR, "width": 900, "height": 900},
        ]
        self.assertEqual(channel_thumbnail(channel_info(thumbnails)), AVATAR)
        for url in (
            "file:///etc/passwd",
            "data:image/png;base64,AAAA",
            "javascript:alert(1)",
            "yt3.googleusercontent.com/a=s0",
            "https://",
            "https://yt3.example/a b",
            "https://yt3.example/a\nb",
            "",
        ):
            with self.subTest(url=url):
                info = channel_info([{"id": "avatar_uncropped", "url": url}])
                self.assertIsNone(channel_thumbnail(info))
        upper = channel_info([{"id": "avatar", "url": "HTTPS://yt3.example/a"}])
        self.assertEqual(channel_thumbnail(upper), "HTTPS://yt3.example/a")

    def test_protocol_relative_url_becomes_https(self):
        info = channel_info([{"id": "avatar_uncropped", "url": "//yt3.ggpht.com/a=s0"}])
        self.assertEqual(channel_thumbnail(info), "https://yt3.ggpht.com/a=s0")

    def test_malformed_entries(self):
        thumbnails: list[object] = [
            None,
            "https://yt3.example/str",
            5,
            ["avatar", AVATAR],
            {"id": "avatar"},
            {"id": 5, "url": f"{YT3}/int-id"},
            {"id": None, "url": f"{YT3}/none-id"},
            {"id": "avatar_x", "url": 5},
            {"id": "avatar_y", "url": f"{YT3}/y", "width": "900", "height": None},
            {"id": "avatar_z", "url": f"{YT3}/z", "width": True, "height": True},
            {"id": "avatar_n", "url": f"{YT3}/n", "preference": float("nan")},
            {"id": "avatar_p", "url": f"{YT3}/p", "preference": "high"},
            {"id": "avatar_ok", "url": f"{YT3}/ok", "width": 2, "height": 2},
        ]
        self.assertEqual(channel_thumbnail(channel_info(thumbnails)), f"{YT3}/ok")

    def test_odd_containers(self):
        for value in (
            None,
            "https://yt3.example/a",
            {"id": "avatar", "url": AVATAR},
            ({"id": "avatar", "url": AVATAR},),
            42,
        ):
            with self.subTest(value=value):
                self.assertIsNone(channel_thumbnail(channel_info(value)))
        self.assertIsNone(channel_thumbnail({}))
        self.assertIsNone(channel_thumbnail(cast(dict[str, object], None)))
        self.assertIsNone(channel_thumbnail(RaisingDict()))


class TestMetadataPostProcessor(IsolatedTestCase):
    def video_info(self, **extra: object) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": "abc",
            "title": "Episode title",
            "thumbnails": [{"id": "0", "url": VIDEO_THUMBNAIL}],
            "thumbnail": VIDEO_THUMBNAIL,
        }
        info.update(extra)
        return info

    def run_pp(self, context: MetadataContext | None, info: dict[str, Any]):
        pp = MetadataPostProcessor()
        pp.set_context(context)
        files, result = pp.run(info)
        self.assertEqual(files, [])
        self.assertIs(result, info)
        return result

    def test_key_and_name(self):
        self.assertEqual(MetadataPostProcessor.pp_key(), "EpisodeMetadata")
        self.assertEqual(MetadataPostProcessor().PP_NAME, "EpisodeMetadata")
        self.assertNotEqual(MetadataPostProcessor.pp_key(), FFmpegMetadataPP.pp_key())

    def test_without_context_nothing_changes(self):
        info = self.video_info()
        expected = copy.deepcopy(info)
        self.assertEqual(self.run_pp(None, info), expected)
        pp = MetadataPostProcessor()
        pp.set_context(MetadataContext(series="Channel", episode_number=1))
        pp.set_context(None)
        _, result = pp.run(info)
        self.assertEqual(result, expected)

    def test_empty_context_adds_only_what_the_video_has(self):
        info = self.video_info()
        expected = copy.deepcopy(info)
        context = MetadataContext(season_number=None)
        result = self.run_pp(context, info)
        # The video's own fields are kept
        self.assertEqual({k: result[k] for k in expected}, expected)
        for key in (
            "series",
            "episode",
            "episode_number",
            "season_number",
            "meta_artist",
            "meta_album",
            "meta_show",
            "meta_grouping",
            "vidl_channel_description",
        ):
            self.assertNotIn(key, result)
        self.assertEqual(result["meta_episode_id"], "abc")

    def test_episode_number_overwrites(self):
        info = self.run_pp(
            MetadataContext(episode_number=7), self.video_info(episode_number=99)
        )
        self.assertEqual(info["episode_number"], 7)
        self.assertNotIn("series", info)
        self.assertEqual(info["season_number"], 1)

    def test_episode_title_is_not_set(self):
        # The episode id (tven) is the video id, so "episode" stays alone
        info = self.run_pp(MetadataContext(episode_number=7), self.video_info())
        self.assertNotIn("episode", info)
        info = self.run_pp(
            MetadataContext(episode_number=7), self.video_info(episode="Real episode")
        )
        self.assertEqual(info["episode"], "Real episode")
        self.assertEqual(info["meta_episode_id"], "abc")

    def test_no_episode_without_number(self):
        info = self.run_pp(MetadataContext(series="Channel"), self.video_info())
        self.assertNotIn("episode", info)
        self.assertNotIn("episode_number", info)

    def test_invalid_numbers_are_ignored(self):
        context = MetadataContext(
            episode_number=cast(int, True), season_number=cast(int, "2")
        )
        info = self.run_pp(context, self.video_info())
        self.assertNotIn("episode_number", info)
        self.assertNotIn("season_number", info)

    def test_series_does_not_clobber(self):
        context = MetadataContext(series="Channel")
        info = self.run_pp(context, self.video_info(series="Real series"))
        self.assertEqual(info["series"], "Real series")
        for missing in ({}, {"series": None}, {"series": ""}):
            with self.subTest(missing=missing):
                info = self.run_pp(context, self.video_info(**missing))
                self.assertEqual(info["series"], "Channel")

    def test_season_number_defaults_to_one(self):
        self.assertEqual(MetadataContext().season_number, 1)
        info = self.run_pp(MetadataContext(), self.video_info())
        self.assertEqual(info["season_number"], 1)
        info = self.run_pp(MetadataContext(season_number=2), self.video_info())
        self.assertEqual(info["season_number"], 2)

    def test_a_positive_video_season_is_kept(self):
        info = self.run_pp(
            MetadataContext(season_number=2), self.video_info(season_number=5)
        )
        self.assertEqual(info["season_number"], 5)

    def test_thumbnail_is_replaced(self):
        info = self.video_info()
        video_thumbnails = info["thumbnails"]
        info = self.run_pp(MetadataContext(thumbnail_url=AVATAR_UNCROPPED), info)
        self.assertEqual(
            info["thumbnails"],
            [{"id": "channel", "url": AVATAR_UNCROPPED, "ext": "jpg"}],
        )
        self.assertEqual(info["thumbnail"], AVATAR_UNCROPPED)
        # The list is replaced, not modified
        self.assertEqual(video_thumbnails, [{"id": "0", "url": VIDEO_THUMBNAIL}])

    def test_thumbnail_extension(self):
        cases = {
            AVATAR: "jpg",
            "https://img.example/cover.png": "png",
            "https://img.example/cover.WEBP?x=1": "webp",
            "https://img.example/cover.jpeg": "jpg",
            "https://img.example/cover.php?id=1": "jpg",
            "https://img.example/cover": "jpg",
        }
        for url, ext in cases.items():
            with self.subTest(url=url):
                info = self.run_pp(
                    MetadataContext(thumbnail_url=url), self.video_info()
                )
                self.assertEqual(info["thumbnails"][0]["ext"], ext)

    def test_non_http_thumbnail_keeps_video_thumbnails(self):
        for url in ("ftp://img.example/a.jpg", "file:///tmp/a.jpg", "", "a.jpg"):
            with self.subTest(url=url):
                context = MetadataContext(episode_number=1, thumbnail_url=url)
                info = self.run_pp(context, self.video_info())
                self.assertEqual(
                    info["thumbnails"], [{"id": "0", "url": VIDEO_THUMBNAIL}]
                )
                self.assertEqual(info["thumbnail"], VIDEO_THUMBNAIL)
                self.assertEqual(info["episode_number"], 1)

    def test_exceptions_are_reported_not_raised(self):
        context = MetadataContext(series="Channel", episode_number=1)
        # Without a downloader report_warning does nothing
        info = RaisingDict(id="abc")
        self.assertIs(self.run_pp(context, info), info)
        self.assertEqual(dict(info), {"id": "abc"})

        logger = RecordingLogger()
        ydl = youtube_dl({"quiet": True, "logger": logger})
        self.addCleanup(ydl.close)
        pp = MetadataPostProcessor()
        ydl.add_post_processor(pp, when="video")
        pp.set_context(context)
        files, result = pp.run(info)
        self.assertEqual((files, result), ([], info))
        self.assertEqual(dict(info), {"id": "abc"})
        (warning,) = logger.warnings
        self.assertTrue(warning.startswith("[EpisodeMetadata] Metadata not added"))
        self.assertIn("RuntimeError: no access", warning)

        logger.fail = True
        self.assertEqual(pp.run(info), ([], info))

    def test_no_progress_hooks_by_default(self):
        hooks: list[dict[str, Any]] = []
        ydl = youtube_dl({"quiet": True, "postprocessor_hooks": [hooks.append]})
        self.addCleanup(ydl.close)
        pp = MetadataPostProcessor()
        ydl.add_post_processor(pp, when="video")
        pp.set_context(MetadataContext(episode_number=1))
        _ = pp.run(self.video_info())
        self.assertEqual(hooks, [])

    def test_progress_hooks_when_enabled(self):
        hooks: list[dict[str, Any]] = []
        ydl = youtube_dl({"quiet": True, "postprocessor_hooks": [hooks.append]})
        self.addCleanup(ydl.close)
        pp = ReportingMetadataPP()
        ydl.add_post_processor(pp, when="video")
        pp.set_context(MetadataContext(episode_number=1))
        _ = pp.run(self.video_info())
        self.assertEqual(
            [(h["postprocessor"], h["status"]) for h in hooks],
            [("EpisodeMetadata", "started"), ("EpisodeMetadata", "finished")],
        )

    def test_set_context_from_threads(self):
        pp = MetadataPostProcessor()
        stop = Event()
        mismatches: list[dict[str, Any]] = []

        def writer(offset: int) -> None:
            i = 0
            while not stop.is_set():
                i = i % 50 + 1
                number = i + offset
                pp.set_context(
                    MetadataContext(series=f"s{number}", episode_number=number)
                )
            pp.set_context(None)

        def reader() -> None:
            for _ in range(2000):
                _, info = pp.run({"title": "t"})
                number = info.get("episode_number")
                if number is not None and info.get("series") != f"s{number}":
                    mismatches.append(info)

        writers = [Thread(target=writer, args=(offset,)) for offset in (0, 100)]
        readers = [Thread(target=reader) for _ in range(4)]
        for thread in writers + readers:
            thread.start()
        for thread in readers:
            thread.join(timeout=30)
        stop.set()
        for thread in writers:
            thread.join(timeout=30)
        self.assertFalse(any(t.is_alive() for t in writers + readers))
        self.assertEqual(mismatches, [])


class TestYoutubeDLIntegration(IsolatedTestCase):
    """Drives a real YoutubeDL through process_ie_result with skip_download,
    so the video stage runs without fetching anything."""

    def setUp(self) -> None:
        super().setUp()
        self.use_utc()
        self.home = self.temp_dir()
        self.logger = RecordingLogger()
        self.hooks: list[dict[str, Any]] = []

    def process(
        self,
        context: MetadataContext | None,
        extra: dict[str, object] | None = None,
        **params: object,
    ):
        settings: dict[str, object] = {
            "quiet": True,
            "skip_download": True,
            "writethumbnail": False,
            "paths": {"home": self.home, "temp": self.home},
            "outtmpl": Config.ydl_settings["outtmpl"],
            "logger": self.logger,
            "postprocessor_hooks": [self.hooks.append],
        }
        settings.update(params)
        ydl = youtube_dl(settings)
        self.addCleanup(ydl.close)
        pp = MetadataPostProcessor()
        pp.set_context(context)
        recorder = RecordingPP()
        ydl.add_post_processor(pp, when="video")
        ydl.add_post_processor(recorder, when="before_dl")
        info: dict[str, object] = {
            "id": "abc",
            "title": "Episode title",
            "extractor": "fake",
            "extractor_key": "Fake",
            "webpage_url": "https://v/abc",
            "channel": "Channel",
            "timestamp": 1705320000,  # 2024-01-15 12:00 UTC
            "thumbnails": [{"url": VIDEO_THUMBNAIL, "width": 1280, "height": 720}],
            "formats": [
                {
                    "format_id": "18",
                    "url": "https://v/abc.mp4",
                    "ext": "mp4",
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                    "protocol": "https",
                    "width": 640,
                    "height": 360,
                }
            ],
        }
        info.update(extra or {})
        process = cast(
            Callable[[dict[str, object], bool], dict[str, Any]], ydl.process_ie_result
        )
        result = process(info, True)
        (seen,) = recorder.seen
        return seen, result

    def test_fields_reach_later_postprocessors(self):
        context = MetadataContext(
            series="Channel", episode_number=3, thumbnail_url=AVATAR_UNCROPPED
        )
        seen, result = self.process(context)
        self.assertEqual(seen["episode_number"], 3)
        self.assertNotIn("episode", seen)
        self.assertEqual(seen["series"], "Channel")
        self.assertEqual(seen["season_number"], 1)
        self.assertEqual(
            seen["thumbnails"],
            [{"id": "channel", "url": AVATAR_UNCROPPED, "ext": "jpg"}],
        )
        self.assertEqual(seen["thumbnail"], AVATAR_UNCROPPED)
        (download,) = result["requested_downloads"]
        self.assertEqual(download["episode_number"], 3)
        self.assertEqual(download["thumbnail"], AVATAR_UNCROPPED)
        self.assertEqual(self.logger.warnings, [])
        self.assertEqual(self.logger.errors, [])
        # The video stage reports no progress, later postprocessors do
        names = {hook["postprocessor"] for hook in self.hooks}
        self.assertNotIn("EpisodeMetadata", names)
        self.assertIn("MoveFiles", names)

    def test_all_fields_reach_later_postprocessors(self):
        context = MetadataContext(
            series="Top channel",
            episode_number=3,
            group="Playlist",
            channel_description="About the channel",
        )
        extra: dict[str, object] = {
            "title": "Episode #One",
            "description": "Line one #two\nline two #ONE",
            "channel_id": "UC123",
            "age_limit": 18,
        }
        seen, _ = self.process(context, extra)
        expected: dict[str, object] = {
            "meta_artist": "Channel",
            "meta_album_artist": "Channel",
            "meta_album": "Channel",
            "meta_show": "Channel",
            "series": "Top channel",
            "episode_number": 3,
            "season_number": 1,
            "meta_media_type": "10",
            "meta_hd_video": "0",
            "meta_date": "2024-01-15",
            "meta_episode_id": "abc",
            "meta_network": "Fake",
            "meta_description": "Line one #two line two #ONE",
            "meta_synopsis": "Line one #two\nline two #ONE",
            "meta_keywords": "One, two",
            "meta_comment": "",
            "meta_grouping": "Playlist",
            "vidl_channel_description": "About the channel",
        }
        self.assertEqual({k: seen.get(k) for k in expected}, expected)
        self.assertNotIn("episode", seen)
        for key, value in seen.items():
            if key.startswith("meta_"):
                self.assertIsInstance(value, str, key)
        self.assertEqual(self.logger.warnings, [])

    def test_without_context_the_video_is_untouched(self):
        seen, _ = self.process(None)
        self.assertNotIn("episode_number", seen)
        self.assertNotIn("series", seen)
        self.assertEqual(seen["thumbnails"][0]["url"], VIDEO_THUMBNAIL)
        self.assertEqual(seen["thumbnail"], VIDEO_THUMBNAIL)

    def test_written_thumbnail_uses_the_channel_entry(self):
        # An existing thumbnail file is reused (overwrites=False), so
        # _write_thumbnails handles the entry without fetching anything
        directory = os.path.join(self.home, "Channel", "2024-01")
        os.makedirs(directory)
        thumbnail = os.path.join(directory, "abc.jpg")
        with open(thumbnail, "wb") as f:
            _ = f.write(b"\xff\xd8\xff\xe0")
        context = MetadataContext(episode_number=1, thumbnail_url=AVATAR_UNCROPPED)
        seen, _ = self.process(context, writethumbnail=True, overwrites=False)
        (entry,) = seen["thumbnails"]
        self.assertEqual(entry["id"], "channel")
        self.assertEqual(entry["filepath"], thumbnail)
        self.assertEqual(self.logger.warnings, [])


def run(pp: PostProcessor, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """PostProcessor.run, which the stubs type with a TypedDict."""
    return cast(Any, pp).run(info)


class TestNewFilesOnly(IsolatedTestCase):
    """Existing files are never touched by the metadata and thumbnail
    post-processors."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.temp_dir()
        self.temp = self.temp_dir()
        self.logger = RecordingLogger()
        self.hooks: list[dict[str, Any]] = []

    def ydl(self, **params: object) -> YoutubeDL:
        settings: dict[str, object] = {
            "quiet": True,
            "paths": {"home": self.home, "temp": self.temp},
            "logger": self.logger,
            "postprocessor_hooks": [self.hooks.append],
        }
        settings.update(params)
        ydl = youtube_dl(settings)
        self.addCleanup(ydl.close)
        return ydl

    def write(self, path: str, content: bytes = b"data") -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            _ = f.write(content)
        return path

    def test_keys_are_the_yt_dlp_ones(self):
        # The view names post-processors by these keys
        self.assertEqual(NewFileMetadataPP.pp_key(), "Metadata")
        self.assertEqual(NewFileEmbedThumbnailPP.pp_key(), "EmbedThumbnail")

    def test_is_new_download(self):
        self.assertTrue(is_new_download({"__real_download": True}))
        for value in (False, None, 1, "yes"):
            self.assertFalse(is_new_download({"__real_download": value}))
        self.assertFalse(is_new_download({}))

    def test_factories_build_the_guarded_classes(self):
        ydl = self.ydl()
        self.assertIsInstance(new_file_metadata_pp(ydl), NewFileMetadataPP)
        self.assertIsInstance(new_file_embed_thumbnail_pp(ydl), NewFileEmbedThumbnailPP)

    def metadata_pp(self) -> tuple[PostProcessor, list[tuple[object, ...]]]:
        ydl = self.ydl()
        pp = new_file_metadata_pp(ydl)
        ydl.add_post_processor(pp, when="post_process")
        calls: list[tuple[object, ...]] = []

        def run_ffmpeg(inputs: Any, output: str, options: Any) -> None:
            calls.append((inputs, output, list(options)))
            with open(output, "wb") as f:
                _ = f.write(b"tagged")

        cast(Any, pp).run_ffmpeg_multiple_files = run_ffmpeg
        return pp, calls

    def metadata_info(self, path: str, **extra: object) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": "abc",
            "title": "Episode",
            "ext": "mp4",
            "filepath": path,
            "series": "Channel",
            "episode_number": 3,
        }
        info.update(extra)
        return info

    def test_metadata_is_written_to_new_downloads(self):
        path = self.write(os.path.join(self.temp, "abc.mp4"))
        pp, calls = self.metadata_pp()
        _ = run(pp, self.metadata_info(path, __real_download=True))
        ((_, _, options),) = calls
        self.assertIn("show=Channel", cast(list[str], options))
        self.assertIn("episode_sort=3", cast(list[str], options))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"tagged")
        statuses = [h["status"] for h in self.hooks if h["postprocessor"] == "Metadata"]
        self.assertEqual(statuses, ["started", "finished"])

    def test_existing_files_get_no_metadata(self):
        for extra in ({"__real_download": False}, {}):
            with self.subTest(extra=extra):
                path = self.write(os.path.join(self.home, "abc.mp4"), b"original")
                before = os.stat(path)
                pp, calls = self.metadata_pp()
                files, _ = run(pp, self.metadata_info(path, **extra))
                self.assertEqual((files, calls), ([], []))
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), b"original")
                self.assertEqual(os.stat(path).st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(self.hooks, [])  # not reported as running

    def thumbnail_info(self, *paths: str) -> dict[str, Any]:
        return {
            "id": "abc",
            "ext": "mp4",
            "filepath": os.path.join(self.home, "abc.mp4"),
            "thumbnails": [
                {"id": str(i), "url": "https://x/t.jpg", "filepath": path}
                for i, path in enumerate(paths)
            ],
            "__files_to_move": {path: None for path in paths},
        }

    def test_thumbnails_for_existing_files_are_removed_from_temp_only(self):
        temp_thumbnail = self.write(os.path.join(self.temp, "Channel", "abc.jpg"))
        home_thumbnail = self.write(os.path.join(self.home, "Channel", "abc.jpg"))
        outside = self.write(os.path.join(self.temp + "-other", "abc.jpg"))
        # Cleanups run last first: the file, then its directory
        self.addCleanup(os.rmdir, os.path.dirname(outside))
        self.addCleanup(os.remove, outside)
        link = os.path.join(self.temp, "link.jpg")
        os.symlink(home_thumbnail, link)
        ydl = self.ydl()
        pp = new_file_embed_thumbnail_pp(ydl)
        info = self.thumbnail_info(temp_thumbnail, home_thumbnail, outside, link)
        files, _ = run(pp, info)
        self.assertEqual(files, [temp_thumbnail])
        # Through yt-dlp, the file is deleted and not moved anymore
        run_pp = cast(
            Callable[[PostProcessor, dict[str, Any]], dict[str, Any]], ydl.run_pp
        )
        info = run_pp(pp, self.thumbnail_info(temp_thumbnail, home_thumbnail))
        self.assertFalse(os.path.exists(temp_thumbnail))
        self.assertTrue(os.path.exists(home_thumbnail))
        self.assertTrue(os.path.exists(outside))
        self.assertNotIn(temp_thumbnail, info["__files_to_move"])
        self.assertEqual(self.hooks, [])

    def test_odd_thumbnail_data_deletes_nothing(self):
        ydl = self.ydl()
        pp = new_file_embed_thumbnail_pp(ydl)
        for thumbnails in (None, "x", [None, {"filepath": None}, {"filepath": 3}]):
            with self.subTest(thumbnails=thumbnails):
                info: dict[str, Any] = {"id": "abc", "thumbnails": thumbnails}
                self.assertEqual(run(pp, info)[0], [])
        no_temp = new_file_embed_thumbnail_pp(self.ydl(paths={"home": self.home}))
        path = self.write(os.path.join(self.temp, "abc.jpg"))
        self.assertEqual(run(no_temp, self.thumbnail_info(path))[0], [])

    def test_an_existing_download_is_left_untouched_end_to_end(self):
        # The video exists in the output directory, so yt-dlp does not
        # download it, and runs the post_process stage with it
        video = self.write(
            os.path.join(self.home, "Channel", "2024-01", "abc.mp4"), b"original"
        )
        before = os.stat(video).st_mtime_ns
        # A thumbnail left in the temporary directory is reused (overwrites
        # False), so nothing is fetched
        temp_thumbnail = self.write(
            os.path.join(self.temp, "Channel", "2024-01", "abc.jpg")
        )
        final_thumbnail = os.path.join(self.home, "Channel", "2024-01", "abc.jpg")
        ydl = self.ydl(
            outtmpl=Config.ydl_settings["outtmpl"],
            writethumbnail=True,
            overwrites=False,
        )
        ydl.add_post_processor(new_file_metadata_pp(ydl), when="post_process")
        ydl.add_post_processor(new_file_embed_thumbnail_pp(ydl), when="post_process")
        info: dict[str, object] = {
            "id": "abc",
            "title": "Episode title",
            "extractor": "fake",
            "extractor_key": "Fake",
            "webpage_url": "https://v/abc",
            "channel": "Channel",
            "timestamp": 1705320000,
            "thumbnails": [{"url": VIDEO_THUMBNAIL}],
            "formats": [
                {
                    "format_id": "18",
                    "url": "https://v/abc.mp4",
                    "ext": "mp4",
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                    "protocol": "https",
                    "width": 640,
                    "height": 360,
                }
            ],
        }
        process = cast(
            Callable[[dict[str, object], bool], dict[str, Any]], ydl.process_ie_result
        )
        self.use_utc()
        _ = process(info, True)
        with open(video, "rb") as f:
            self.assertEqual(f.read(), b"original")
        self.assertEqual(os.stat(video).st_mtime_ns, before)
        self.assertFalse(os.path.exists(temp_thumbnail))
        self.assertFalse(os.path.exists(final_thumbnail))
        names = {hook["postprocessor"] for hook in self.hooks}
        self.assertNotIn("Metadata", names)
        self.assertNotIn("EmbedThumbnail", names)
        self.assertEqual(self.logger.errors, [])


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are needed")

VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n"
PREFIX = "----:com.apple.iTunes:"


def ffmpeg(*args: str) -> None:
    _ = subprocess.run(
        [cast(str, FFMPEG), "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


def ffprobe(path: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            cast(str, FFPROBE),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


_base_mp4: bytes | None = None


def base_mp4() -> bytes:
    """A two second mpeg4+aac mp4 (ffmpeg here has no libx264), made once."""
    global _base_mp4
    if _base_mp4 is None:
        with tempfile.TemporaryDirectory(prefix="vidl-test-") as directory:
            path = os.path.join(directory, "base.mp4")
            ffmpeg(
                *("-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=10"),
                *("-f", "lavfi", "-i", "sine=duration=2"),
                *("-c:v", "mpeg4", "-c:a", "aac", "-shortest", path),
            )
            with open(path, "rb") as f:
                _base_mp4 = f.read()
    return _base_mp4


def tiny_mp4(path: str, metadata: dict[str, str] | None = None) -> str:
    """Writes a tiny video remuxed by ffmpeg (-c copy), as yt-dlp does, so it
    has the encoder atom ©too. The container follows the extension."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    source = path + ".source.mp4"
    with open(source, "wb") as f:
        _ = f.write(base_mp4())
    options: list[str] = []
    for key, value in (metadata or {}).items():
        options += ["-metadata", f"{key}={value}"]
    video_only = ["-vn"] if path.endswith(".m4a") else []
    ffmpeg("-i", source, *video_only, "-c", "copy", *options, path)
    os.remove(source)
    return path


def tags_of(path: str) -> dict[str, Any]:
    tags = MP4(path).tags
    return dict(tags) if tags is not None else {}


def freeform(path: str, name: str) -> str | None:
    values = tags_of(path).get(PREFIX + name)
    return None if values is None else bytes(values[0]).decode("utf-8")


class TestRules(unittest.TestCase):
    """The pure functions behind each tag."""

    def test_channel_name(self):
        context = MetadataContext(series="Series")
        cases: list[tuple[dict[str, object], str | None]] = [
            ({"channel": "C", "uploader": "U"}, "C"),
            ({"channel": "", "uploader": "U"}, "U"),
            ({"channel": None, "uploader": "  "}, "Series"),
            ({"channel": 5, "uploader": b"x"}, "Series"),
            ({"channel": "  C  "}, "C"),
            ({"channel": "\0"}, "Series"),
            ({"channel": "C\0h"}, "Ch"),
            ({}, "Series"),
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.assertEqual(channel_name(info, context), expected)
        for series in (None, "", "  "):
            with self.subTest(series=series):
                self.assertIsNone(channel_name({}, MetadataContext(series=series)))

    def test_hd_video_levels_table(self):
        heights = [height for height, _ in HD_VIDEO_LEVELS]
        self.assertEqual(heights, sorted(heights, reverse=True))
        self.assertEqual(heights[-1], 0)

    def test_hd_video_boundaries(self):
        cases = {
            1: "0",
            479: "0",
            719: "0",
            720: "1",
            1079: "1",
            1080: "2",
            2159: "2",
            2160: "3",
            4320: "3",
        }
        for height, level in cases.items():
            with self.subTest(height=height):
                self.assertEqual(hd_video({"height": height}), level)
                self.assertEqual(video_height({"height": height}), height)

    def test_hd_video_without_a_usable_height(self):
        for height in (None, True, "720", 720.0, 0, -5, [720]):
            with self.subTest(height=height):
                self.assertIsNone(hd_video({"height": height}))
                self.assertIsNone(video_height({"height": height}))
        self.assertIsNone(hd_video({}))

    def test_hd_video_from_requested_formats(self):
        formats = [{"height": 720}, {"height": None}, {"height": 1080}, "x", None]
        self.assertEqual(hd_video({"requested_formats": formats}), "2")
        self.assertEqual(video_height({"requested_formats": formats}), 1080)
        # An unusable height falls back to the formats
        info = {"height": True, "requested_formats": [{"height": 2160}]}
        self.assertEqual(hd_video(info), "3")
        # info["height"] comes first
        info = {"height": 480, "requested_formats": [{"height": 2160}]}
        self.assertEqual(hd_video(info), "0")
        for formats in (None, [], "x", {"height": 720}, [{}, {"height": "720"}]):
            with self.subTest(formats=formats):
                self.assertIsNone(hd_video({"requested_formats": formats}))

    def test_episode_date_from_upload_date(self):
        self.assertEqual(episode_date({"upload_date": "20240115"}), "2024-01-15")
        self.assertEqual(episode_date({"upload_date": "20240229"}), "2024-02-29")
        # It wins over the timestamp
        info = {"upload_date": "20240101", "timestamp": 1705320000}
        self.assertEqual(episode_date(info), "2024-01-01")

    def test_episode_date_invalid_upload_date(self):
        for value in (
            "20241301",
            "20240230",
            "20230229",
            "20240100",
            "00000101",
            "2024011",
            "202401155",
            "2024-01-15",
            "abcdefgh",
            "\uff12\uff10\uff12\uff14\uff10\uff11\uff11\uff15",
            "20240115\n",
            "",
            20240115,
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(episode_date({"upload_date": value}))
                # The timestamp is the fallback
                info = {"upload_date": value, "timestamp": 1705320000}
                self.assertEqual(episode_date(info), "2024-01-15")

    def test_episode_date_from_timestamp(self):
        cases: dict[float, str] = {
            1705320000: "2024-01-15",
            1705320000.9: "2024-01-15",
            1705363199: "2024-01-15",  # 23:59:59 UTC
            1705363200: "2024-01-16",
            0: "1970-01-01",
            -1: "1969-12-31",
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                self.assertEqual(episode_date({"timestamp": timestamp}), expected)
        for timestamp in (
            True,
            "1705320000",
            None,
            float("nan"),
            float("inf"),
            1e30,
            -1e30,
        ):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(episode_date({"timestamp": timestamp}))
        self.assertIsNone(episode_date({}))

    def test_iso_timestamp(self):
        cases: dict[float, str] = {
            1705320000: "2024-01-15T12:00:00Z",
            1705320000.75: "2024-01-15T12:00:00Z",
            0: "1970-01-01T00:00:00Z",
            -1: "1969-12-31T23:59:59Z",
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                self.assertEqual(iso_timestamp(timestamp), expected)
        for timestamp in (True, "1705320000", None, float("nan"), 1e30, [1], {}):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(iso_timestamp(timestamp))

    def test_episode_id(self):
        self.assertEqual(episode_id({"id": "abc"}), "abc")
        self.assertEqual(episode_id({"id": "a\0b"}), "ab")
        for value in ("", "  ", "\0", 5, True, None, ["a"]):
            with self.subTest(value=value):
                self.assertIsNone(episode_id({"id": value}))
        self.assertIsNone(episode_id({}))

    def test_season_number(self):
        cases: list[tuple[dict[str, object], MetadataContext, int | None]] = [
            ({}, MetadataContext(), 1),
            ({}, MetadataContext(season_number=4), 4),
            ({}, MetadataContext(season_number=0), 0),
            ({}, MetadataContext(season_number=None), None),
            ({}, MetadataContext(season_number=cast(int, True)), None),
            ({}, MetadataContext(season_number=cast(int, "2")), None),
            ({}, MetadataContext(season_number=cast(int, 2.0)), None),
            # A positive season of the video is kept (None = leave it)
            ({"season_number": 3}, MetadataContext(), None),
            ({"season_number": 3}, MetadataContext(season_number=None), None),
            # Anything else is replaced
            ({"season_number": 0}, MetadataContext(), 1),
            ({"season_number": -2}, MetadataContext(), 1),
            ({"season_number": True}, MetadataContext(), 1),
            ({"season_number": "2"}, MetadataContext(), 1),
            ({"season_number": 2.0}, MetadataContext(), 1),
            ({"season_number": None}, MetadataContext(), 1),
        ]
        for info, context, expected in cases:
            with self.subTest(info=info, context=context):
                self.assertEqual(season_number(info, context), expected)

    def test_network_name(self):
        cases: list[tuple[dict[str, object], str | None]] = [
            ({"extractor_key": "Youtube"}, "YouTube"),
            ({"extractor_key": "Youtube:tab"}, "YouTube"),
            ({"extractor_key": "YoutubeTab"}, "YouTube"),
            ({"extractor_key": "  Youtube  "}, "YouTube"),
            ({"extractor_key": "Vimeo"}, "Vimeo"),
            ({"extractor_key": "TwitchVod"}, "Twitch"),
            ({"extractor_key": "twitch:vod"}, "Twitch"),
            ({"extractor_key": "Soundcloud"}, "SoundCloud"),
            ({"extractor_key": "SomethingNew"}, "SomethingNew"),
            ({"extractor_key": "Foo:bar:baz"}, "Foo"),
            # The extractor name is the fallback
            ({"extractor": "youtube:tab"}, "YouTube"),
            ({"extractor_key": "", "extractor": "vimeo"}, "Vimeo"),
            ({"extractor_key": ":x", "extractor": "vimeo"}, "Vimeo"),
            ({"extractor_key": 5, "extractor": "dailymotion"}, "Dailymotion"),
            ({"extractor_key": "Youtube", "extractor": "vimeo"}, "YouTube"),
            ({"extractor_key": "Y\0outube"}, "YouTube"),
            ({}, None),
            ({"extractor_key": None, "extractor": None}, None),
            ({"extractor_key": ":", "extractor": " "}, None),
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.assertEqual(network_name(info), expected)

    def test_short_description_without_text(self):
        for value in (None, "", "  \n\t ", "\0", 5, ["a"], b"abc"):
            with self.subTest(value=value):
                self.assertIsNone(short_description(value))
                self.assertIsNone(synopsis(value))

    def test_short_description_collapses_whitespace(self):
        cases = {
            "Hello": "Hello",
            "  Hello  ": "Hello",
            "a\n\nb\t c   d\r\ne": "a b c d e",
            "a\u00a0\u2003b": "a b",
            "a\0b": "ab",
            "Fin\u2026": "Fin\u2026",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(short_description(text), expected)

    def test_short_description_length_boundaries(self):
        exact = ("word " * 100)[:254] + "x"  # 255 characters, ends in a word
        self.assertEqual(len(exact), 255)
        self.assertEqual(short_description(exact), exact)
        over = exact + "y"
        result = short_description(over)
        assert result is not None
        self.assertLessEqual(len(result), 255)
        self.assertTrue(result.endswith("\u2026"))
        body = result[:-1]
        # Cut at a word boundary: the next character is the separator
        self.assertTrue(over.startswith(body))
        self.assertEqual(over[len(body)], " ")
        self.assertFalse(body.endswith(" "))
        self.assertEqual(len(body), 249)

    def test_short_description_cut_exactly_at_a_boundary(self):
        text = ("word " * 100)[:254] + " more"
        result = short_description(text)
        assert result is not None
        self.assertEqual(result, text[:254] + "\u2026")
        self.assertEqual(len(result), 255)

    def test_short_description_long_texts(self):
        cases = {
            "single word": "x" * 300,
            "accents": "\u00e9" * 300,
            "multi line": "line one\n" * 100,
            "cjk": "\u65e5\u672c\u8a9e " * 200,
            "emoji": "\U0001f600 " * 300,
            "huge": "word " * 50000,
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                result = short_description(text)
                assert result is not None
                self.assertLessEqual(len(result), 255)
                self.assertTrue(result.endswith("\u2026"))
                body = result[:-1]
                normalized = " ".join(text.split())
                self.assertTrue(normalized.startswith(body))
                self.assertEqual(body, body.rstrip())
        self.assertEqual(short_description("x" * 300), "x" * 254 + "\u2026")
        self.assertEqual(len(cast(str, short_description("\u00e9" * 300))), 255)
        self.assertTrue(
            cast(str, short_description("\u65e5\u672c\u8a9e " * 200)).endswith(
                "\u8a9e\u2026"
            )
        )

    def test_synopsis_is_the_full_text(self):
        for text in (
            "Full\ntext  with   spaces\n",
            "  leading",
            "x " * 5000,
            "\u65e5\u672c\u8a9e\n\U0001f600",
        ):
            with self.subTest(text=text):
                self.assertEqual(synopsis(text), text)
        self.assertEqual(synopsis("a\0b\0"), "ab")

    def test_hashtags(self):
        cases: list[tuple[tuple[object, ...], list[str]]] = [
            (("#a #b", "#c"), ["a", "b", "c"]),
            (("#Tag #tag #TAG",), ["Tag"]),
            (("#Tag", "#tAG #other"), ["Tag", "other"]),
            (("#b", "#a #b"), ["b", "a"]),
            (
                ("#\u65e5\u672c\u8a9e #caf\u00e9 #na\u00efve_2",),
                ["\u65e5\u672c\u8a9e", "caf\u00e9", "na\u00efve_2"],
            ),
            (("#\u00c9t\u00e9 #\u00e9T\u00c9",), ["\u00c9t\u00e9"]),
            (("#tag, #tag2. (#paren) [#br]",), ["tag", "tag2", "paren", "br"]),
            (("line\n#one\n\t#two",), ["one", "two"]),
            (("#1 hit",), ["1"]),
            (("a#b ##c word#d x_#e",), []),
            (("#", "# space", "##"), []),
            (("#ta\0g",), ["tag"]),
            ((None, 5, ["#a"], b"#b"), []),
            (("",), []),
            ((), []),
        ]
        for texts, expected in cases:
            with self.subTest(texts=texts):
                self.assertEqual(hashtags(*texts), expected)

    def test_hashtags_are_limited(self):
        text = " ".join(f"#t{i}" for i in range(80))
        tags = hashtags(text)
        self.assertEqual(len(tags), 50)
        self.assertEqual((tags[0], tags[-1]), ("t0", "t49"))
        # The limit counts unique tags over both texts
        tags = hashtags(" ".join(f"#a{i}" for i in range(30)), text)
        self.assertEqual(len(tags), 50)
        self.assertEqual(tags[29], "a29")
        self.assertEqual(tags[30], "t0")

    def test_keywords(self):
        info = {"title": "Title #One", "description": "x #two #one\n#Three"}
        self.assertEqual(keywords(info), "One, two, Three")
        self.assertEqual(keywords({"title": "#a"}), "a")
        self.assertEqual(keywords({"description": "#a #b"}), "a, b")
        for info in ({}, {"title": "None", "description": "here"}, {"title": 5}):
            with self.subTest(info=info):
                self.assertIsNone(keywords(info))


class TestAtomValues(unittest.TestCase):
    def test_nothing_without_sources(self):
        self.assertEqual(atom_values({}), {})

    def test_rating(self):
        cases: list[tuple[object, bool]] = [
            (18, True),
            (19, True),
            (21, True),
            (17, False),
            (0, False),
            (-1, False),
            (True, False),
            ("18", False),
            (18.0, False),
            (None, False),
        ]
        for age_limit, rated in cases:
            with self.subTest(age_limit=age_limit):
                values = atom_values({"age_limit": age_limit})
                self.assertEqual(values, {"rtng": [1]} if rated else {})

    def test_channel_description(self):
        self.assertEqual(
            atom_values({"vidl_channel_description": "About\nus"}),
            {"sdes": ["About\nus"]},
        )
        self.assertEqual(
            atom_values({"vidl_channel_description": "a\0b"}), {"sdes": ["ab"]}
        )
        for value in ("", "  ", None, 5):
            with self.subTest(value=value):
                self.assertEqual(atom_values({"vidl_channel_description": value}), {})

    def test_freeform_atoms(self):
        info = {
            "channel_url": "https://c/\u00e9",
            "channel_id": "UC1",
            "webpage_url": "https://v/abc",
            "timestamp": 1705320000,
        }
        values = atom_values(info)
        prefix = "----:com.apple.iTunes:"
        self.assertEqual(
            set(values),
            {
                prefix + "CHANNEL_URL",
                prefix + "CHANNEL_ID",
                prefix + "VIDEO_URL",
                prefix + "UPLOAD_TIMESTAMP",
            },
        )
        decoded = {
            key: bytes(cast(MP4FreeForm, value[0])).decode()
            for key, value in values.items()
        }
        self.assertEqual(decoded[prefix + "CHANNEL_URL"], "https://c/\u00e9")
        self.assertEqual(decoded[prefix + "CHANNEL_ID"], "UC1")
        self.assertEqual(decoded[prefix + "VIDEO_URL"], "https://v/abc")
        self.assertEqual(decoded[prefix + "UPLOAD_TIMESTAMP"], "2024-01-15T12:00:00Z")
        for (value,) in values.values():
            self.assertIsInstance(value, MP4FreeForm)
            self.assertEqual(cast(MP4FreeForm, value).dataformat, AtomDataType.UTF8)

    def test_invalid_freeform_sources_are_left_out(self):
        info = {
            "channel_url": "",
            "channel_id": 5,
            "webpage_url": "  ",
            "timestamp": "1705320000",
        }
        self.assertEqual(atom_values(info), {})
        for timestamp in (True, None, float("nan"), 1e30):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(atom_values({"timestamp": timestamp}), {})
        values = atom_values({"timestamp": 1705320000.5})
        self.assertEqual(len(values), 1)


class FailingDescription(dict[str, Any]):
    """An info dict that fails when the description is read."""

    def get(self, key: str, default: object = None) -> Any:
        if key == "description":
            raise RuntimeError("no description")
        return super().get(key, default)


class TestVideoStageTags(IsolatedTestCase):
    """What the video stage sets, through MetadataPostProcessor.run."""

    def run_pp(
        self, info: dict[str, Any], context: MetadataContext | None = None
    ) -> dict[str, Any]:
        pp = MetadataPostProcessor()
        pp.set_context(context or MetadataContext())
        files, result = pp.run(info)
        self.assertEqual(files, [])
        return result

    def full_info(self) -> dict[str, Any]:
        return {
            "id": "abc",
            "title": "Title #First",
            "description": "Some text #second\nmore text",
            "channel": "Chan",
            "uploader": "Uploader",
            "upload_date": "20240115",
            "timestamp": 1705320000,
            "height": 1080,
            "extractor_key": "Youtube",
        }

    def test_every_tag(self):
        context = MetadataContext(
            series="Series", group="Playlist", channel_description="About"
        )
        info = self.run_pp(self.full_info(), context)
        self.assertEqual(
            {k: v for k, v in info.items() if k.startswith(("meta_", "vidl_"))},
            {
                "meta_artist": "Chan",
                "meta_album_artist": "Chan",
                "meta_album": "Chan",
                "meta_show": "Chan",
                "meta_media_type": "10",
                "meta_hd_video": "2",
                "meta_date": "2024-01-15",
                "meta_episode_id": "abc",
                "meta_network": "YouTube",
                "meta_description": "Some text #second more text",
                "meta_synopsis": "Some text #second\nmore text",
                "meta_keywords": "First, second",
                "meta_comment": "",
                "meta_grouping": "Playlist",
                "vidl_channel_description": "About",
            },
        )
        self.assertEqual(info["series"], "Series")
        self.assertEqual(info["season_number"], 1)
        self.assertNotIn("episode", info)

    def test_channel_name_fallbacks(self):
        info = self.full_info()
        context = MetadataContext(series="Series")
        for removed, expected in (
            ((), "Chan"),
            (("channel",), "Uploader"),
            (("channel", "uploader"), "Series"),
        ):
            with self.subTest(removed=removed):
                changed = {k: v for k, v in info.items() if k not in removed}
                result = self.run_pp(changed, context)
                for key in (
                    "meta_artist",
                    "meta_album_artist",
                    "meta_album",
                    "meta_show",
                ):
                    self.assertEqual(result[key], expected)
        # Without any name there are no channel tags
        result = self.run_pp({"id": "abc"})
        for key in ("meta_artist", "meta_album_artist", "meta_album", "meta_show"):
            self.assertNotIn(key, result)

    def test_series_comes_from_the_context_only(self):
        result = self.run_pp(self.full_info(), MetadataContext(series="Series"))
        self.assertEqual(result["series"], "Series")
        self.assertEqual(result["meta_show"], "Chan")
        result = self.run_pp(self.full_info())
        self.assertNotIn("series", result)

    def test_constant_tags(self):
        for info in ({}, {"id": "abc"}, self.full_info()):
            with self.subTest(info=info):
                result = self.run_pp(info, MetadataContext(season_number=None))
                self.assertEqual(result["meta_media_type"], "10")
                self.assertEqual(result["meta_comment"], "")

    def test_missing_values_set_no_tags(self):
        result = self.run_pp({}, MetadataContext(season_number=None))
        self.assertEqual(result, {"meta_media_type": "10", "meta_comment": ""})

    def test_no_description_sets_no_description_tags(self):
        for description in (None, "", "  \n\t", 5, "\0"):
            with self.subTest(description=description):
                info = self.full_info()
                info["description"] = description
                result = self.run_pp(info)
                self.assertNotIn("meta_description", result)
                self.assertNotIn("meta_synopsis", result)
        info = self.full_info()
        del info["description"]
        result = self.run_pp(info)
        self.assertNotIn("meta_description", result)
        self.assertNotIn("meta_synopsis", result)
        # The title still gives keywords
        self.assertEqual(result["meta_keywords"], "First")

    def test_long_description(self):
        info = self.full_info()
        info["description"] = "word\n" * 200
        result = self.run_pp(info)
        self.assertEqual(result["meta_synopsis"], "word\n" * 200)
        self.assertLessEqual(len(result["meta_description"]), 255)
        self.assertTrue(result["meta_description"].endswith("word\u2026"))
        self.assertNotIn("\n", result["meta_description"])

    def test_grouping_and_channel_description(self):
        for value, expected in (
            ("Playlist", "Playlist"),
            ("  Playlist ", "Playlist"),
            ("", None),
            ("  ", None),
            (None, None),
            (5, None),
        ):
            with self.subTest(value=value):
                context = MetadataContext(
                    group=cast(str, value), channel_description=cast(str, value)
                )
                result = self.run_pp(self.full_info(), context)
                self.assertEqual(result.get("meta_grouping"), expected)
                described = None if value in ("", "  ", None, 5) else value
                self.assertEqual(result.get("vidl_channel_description"), described)

    def test_no_hd_video_without_height(self):
        info = self.full_info()
        del info["height"]
        self.assertNotIn("meta_hd_video", self.run_pp(info))
        info["requested_formats"] = [{"height": 720}, {"height": 360}]
        self.assertEqual(self.run_pp(info)["meta_hd_video"], "1")

    def test_date_falls_back_to_the_timestamp(self):
        info = self.full_info()
        info["upload_date"] = "20241301"
        self.assertEqual(self.run_pp(info)["meta_date"], "2024-01-15")
        info = self.full_info()
        info["upload_date"] = "20241301"
        del info["timestamp"]
        self.assertNotIn("meta_date", self.run_pp(info))

    def test_existing_season_is_kept(self):
        info = self.full_info()
        info["season_number"] = 4
        self.assertEqual(self.run_pp(info)["season_number"], 4)
        info["season_number"] = 0
        self.assertEqual(self.run_pp(info)["season_number"], 1)

    def test_all_tags_are_text_without_nul(self):
        info: dict[str, Any] = {
            "id": "a\0b",
            "title": "T\0itle #ta\0g",
            "description": "de\0sc #x\0",
            "channel": "c\0h",
            "extractor_key": "Y\0outube",
            "height": 720,
            "upload_date": "20240115",
        }
        context = MetadataContext(series="s\0", group="g\0", channel_description="x\0y")
        result = self.run_pp(info, context)
        tags = {k: v for k, v in result.items() if k.startswith(("meta_", "vidl_"))}
        self.assertGreaterEqual(len(tags), 15)
        for key, value in tags.items():
            self.assertIsInstance(value, str, key)
            self.assertNotIn("\0", value, key)
        self.assertEqual(result["meta_episode_id"], "ab")
        self.assertEqual(result["meta_network"], "YouTube")
        self.assertEqual(result["meta_keywords"], "tag, x")

    def test_numbers_are_ints_not_text(self):
        context = MetadataContext(episode_number=2, season_number=3)
        result = self.run_pp(self.full_info(), context)
        self.assertEqual(result["episode_number"], 2)
        self.assertEqual(result["season_number"], 3)

    def test_a_failure_leaves_the_info_unchanged(self):
        logger = RecordingLogger()
        ydl = youtube_dl({"quiet": True, "logger": logger})
        self.addCleanup(ydl.close)
        pp = MetadataPostProcessor()
        ydl.add_post_processor(pp, when="video")
        pp.set_context(MetadataContext(series="Series", episode_number=1))
        info = FailingDescription(self.full_info())
        expected = dict(info)
        self.assertEqual(pp.run(info), ([], info))
        self.assertEqual(dict(info), expected)
        (warning,) = logger.warnings
        self.assertIn("RuntimeError: no description", warning)


class FileTestCase(IsolatedTestCase):
    """A YoutubeDL with a home and a temporary directory, and the files in
    them."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.temp_dir()
        self.temp = self.temp_dir()
        self.logger = RecordingLogger()
        self.hooks: list[dict[str, Any]] = []

    def ydl(self, **params: object) -> YoutubeDL:
        settings: dict[str, object] = {
            "quiet": True,
            "paths": {"home": self.home, "temp": self.temp},
            "logger": self.logger,
            "postprocessor_hooks": [self.hooks.append],
        }
        settings.update(params)
        ydl = youtube_dl(settings)
        self.addCleanup(ydl.close)
        return ydl

    def write(self, path: str, content: bytes | str = b"data") -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = content.encode() if isinstance(content, str) else content
        with open(path, "wb") as f:
            _ = f.write(data)
        return path

    def read(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def age(self, path: str) -> int:
        """Makes the file old, so any rewrite changes its mtime."""
        old = 1_000_000_000_000_000_000
        os.utime(path, ns=(old, old))
        return os.stat(path).st_mtime_ns

    def run_through(
        self, ydl: YoutubeDL, pp: PostProcessor, info: dict[str, Any]
    ) -> dict[str, Any]:
        return cast(
            Callable[[PostProcessor, dict[str, Any]], dict[str, Any]], ydl.run_pp
        )(pp, info)

    def statuses(self, name: str) -> list[str]:
        return [h["status"] for h in self.hooks if h["postprocessor"] == name]


class TestAtomKeys(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(AtomPostProcessor.pp_key(), "Atoms")
        self.assertEqual(NewFileAtomPP.pp_key(), "Atoms")
        self.assertEqual(AtomPostProcessor().PP_NAME, "Atoms")
        self.assertEqual(NewFileEmbedSubtitlePP.pp_key(), "EmbedSubtitle")

    def test_factories_build_the_guarded_classes(self):
        ydl = youtube_dl({"quiet": True})
        self.addCleanup(ydl.close)
        self.assertIsInstance(new_file_atom_pp(ydl), NewFileAtomPP)
        self.assertIsInstance(new_file_embed_subtitle_pp(ydl), NewFileEmbedSubtitlePP)
        subtitles = cast(Any, new_file_embed_subtitle_pp(ydl))
        self.assertFalse(subtitles._already_have_subtitle)


@needs_ffmpeg
class TestAtomPostProcessor(FileTestCase):
    def info(self, path: str, **extra: object) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": "abc",
            "ext": "mp4",
            "filepath": path,
            "__real_download": True,
            "age_limit": 18,
            "vidl_channel_description": "About the channel\nsecond line \u00e9",
            "channel_url": "https://www.youtube.com/channel/UC123",
            "channel_id": "UC123",
            "webpage_url": "https://www.youtube.com/watch?v=abc",
            "timestamp": 1705320000,
        }
        info.update(extra)
        return info

    def pp(self) -> tuple[YoutubeDL, PostProcessor]:
        ydl = self.ydl()
        pp = new_file_atom_pp(ydl)
        ydl.add_post_processor(pp, when="post_process")
        return ydl, pp

    def mp4(self, **metadata: str) -> str:
        return tiny_mp4(os.path.join(self.temp, "abc.mp4"), metadata)

    def leftovers(self, path: str) -> list[str]:
        return sorted(os.listdir(os.path.dirname(path)))

    def test_the_fixture_has_the_encoder_atom(self):
        self.assertIn("\u00a9too", tags_of(self.mp4()))

    def test_atoms_are_written(self):
        path = self.mp4(title="Title")
        before = MP4(path).info.length
        _, pp = self.pp()
        files, _ = run(pp, self.info(path))
        self.assertEqual(files, [])
        tags = tags_of(path)
        self.assertNotIn("\u00a9too", tags)
        self.assertNotIn("\u00a9enc", tags)
        self.assertEqual(tags["rtng"], [1])
        self.assertEqual(tags["sdes"], ["About the channel\nsecond line \u00e9"])
        self.assertEqual(
            freeform(path, "CHANNEL_URL"), "https://www.youtube.com/channel/UC123"
        )
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(
            freeform(path, "VIDEO_URL"), "https://www.youtube.com/watch?v=abc"
        )
        self.assertEqual(freeform(path, "UPLOAD_TIMESTAMP"), "2024-01-15T12:00:00Z")
        (url,) = tags[PREFIX + "CHANNEL_URL"]
        self.assertEqual(url.dataformat, AtomDataType.UTF8)
        self.assertEqual(self.logger.warnings, [])
        # Still a valid video of the same length
        self.assertAlmostEqual(MP4(path).info.length, before, places=2)
        streams = ffprobe(path)["streams"]
        self.assertEqual([s["codec_type"] for s in streams], ["video", "audio"])
        self.assertEqual(self.leftovers(path), ["abc.mp4"])

    def test_existing_atoms_are_preserved(self):
        path = self.mp4(title="Title", artist="Artist", show="Show")
        mp4 = MP4(path)
        assert mp4.tags is not None
        mp4.tags["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0cover", MP4Cover.FORMAT_JPEG)]
        mp4.tags[PREFIX + "OTHER"] = [MP4FreeForm(b"kept", AtomDataType.UTF8)]
        mp4.save()
        _, pp = self.pp()
        _ = run(pp, self.info(path))
        tags = tags_of(path)
        self.assertEqual(tags["\u00a9nam"], ["Title"])
        self.assertEqual(tags["\u00a9ART"], ["Artist"])
        self.assertEqual(tags["tvsh"], ["Show"])
        self.assertEqual([bytes(c) for c in tags["covr"]], [b"\xff\xd8\xff\xe0cover"])
        self.assertEqual(freeform(path, "OTHER"), "kept")

    def test_encoder_atoms_are_removed_without_other_values(self):
        path = self.mp4()
        mp4 = MP4(path)
        assert mp4.tags is not None
        mp4.tags["\u00a9enc"] = ["Encoder"]
        mp4.save()
        _, pp = self.pp()
        _ = run(pp, {"ext": "mp4", "filepath": path, "__real_download": True})
        tags = tags_of(path)
        self.assertNotIn("\u00a9too", tags)
        self.assertNotIn("\u00a9enc", tags)
        self.assertEqual([k for k in tags if k.startswith(PREFIX)], [])
        self.assertNotIn("rtng", tags)
        self.assertNotIn("sdes", tags)

    def test_rating_only_from_18(self):
        for age_limit, rated in (
            (18, True),
            (19, True),
            (17, False),
            (0, False),
            (None, False),
            (True, False),
            ("18", False),
        ):
            with self.subTest(age_limit=age_limit):
                path = self.mp4()
                _, pp = self.pp()
                _ = run(pp, self.info(path, age_limit=age_limit))
                self.assertEqual(tags_of(path).get("rtng"), [1] if rated else None)

    def test_an_existing_rating_is_removed(self):
        path = self.mp4()
        mp4 = MP4(path)
        assert mp4.tags is not None
        mp4.tags["rtng"] = [1]
        mp4.save()
        self.assertEqual(tags_of(path)["rtng"], [1])
        _, pp = self.pp()
        _ = run(pp, self.info(path, age_limit=0))
        self.assertNotIn("rtng", tags_of(path))

    def test_invalid_sources_write_no_atoms(self):
        path = self.mp4()
        _, pp = self.pp()
        info = self.info(
            path,
            age_limit=None,
            vidl_channel_description="",
            channel_url=5,
            channel_id="",
            webpage_url=None,
            timestamp="1705320000",
        )
        _ = run(pp, info)
        tags = tags_of(path)
        self.assertNotIn("\u00a9too", tags)
        for key in ("rtng", "sdes"):
            self.assertNotIn(key, tags)
        self.assertEqual([k for k in tags if k.startswith(PREFIX)], [])

    def test_freeform_atoms_are_replaced(self):
        path = self.mp4()
        mp4 = MP4(path)
        assert mp4.tags is not None
        mp4.tags[PREFIX + "CHANNEL_ID"] = [MP4FreeForm(b"old", AtomDataType.UTF8)]
        mp4.save()
        _, pp = self.pp()
        _ = run(pp, self.info(path))
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(len(tags_of(path)[PREFIX + "CHANNEL_ID"]), 1)

    def test_files_without_any_tags(self):
        source = self.write(os.path.join(self.temp, "source.mp4"), base_mp4())
        path = os.path.join(self.temp, "abc.mp4")
        ffmpeg(
            *("-i", source, "-c", "copy", "-map_metadata", "-1"),
            *("-fflags", "+bitexact", "-flags:v", "+bitexact", path),
        )
        os.remove(source)
        self.assertEqual(tags_of(path), {})
        _, pp = self.pp()
        _ = run(pp, self.info(path))
        self.assertEqual(self.logger.warnings, [])
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(tags_of(path)["rtng"], [1])

    def test_m4a_files(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.m4a"))
        self.assertIn("\u00a9too", tags_of(path))
        _, pp = self.pp()
        _ = run(pp, self.info(path, ext="m4a"))
        tags = tags_of(path)
        self.assertNotIn("\u00a9too", tags)
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(tags["rtng"], [1])

    def test_mov_files_are_tagged_too(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mov"))
        _, pp = self.pp()
        _ = run(pp, self.info(path, ext="mov"))
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(self.logger.warnings, [])
        self.assertEqual(
            [s["codec_type"] for s in ffprobe(path)["streams"]], ["video", "audio"]
        )

    def test_the_extension_is_case_insensitive(self):
        path = tiny_mp4(os.path.join(self.temp, "ABC.MP4"))
        _, pp = self.pp()
        _ = run(pp, self.info(path, ext="MP4"))
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")

    def test_other_formats_are_skipped(self):
        cases = (
            ("abc.mkv", "mkv"),
            ("abc.webm", "webm"),
            ("abc.mp3", "mp3"),
            ("abc.webm", "mp4"),  # the real file decides too
            ("abc.mp4", "webm"),  # and what yt-dlp says
            ("abc", "mp4"),
        )
        for name, ext in cases:
            with self.subTest(name=name, ext=ext):
                path = self.write(os.path.join(self.temp, name), base_mp4())
                before = self.age(path)
                self.logger.messages.clear()
                _, pp = self.pp()
                files, _ = run(pp, self.info(path, ext=ext))
                self.assertEqual(files, [])
                self.assertEqual(self.read(path), base_mp4())
                self.assertEqual(os.stat(path).st_mtime_ns, before)
                self.assertTrue(
                    any("Skipping atoms" in m for m in self.logger.messages)
                )
                self.assertEqual(self.logger.warnings, [])
                self.assertEqual(self.leftovers(path), sorted([name]))
                os.remove(path)

    def test_missing_files_are_skipped(self):
        path = os.path.join(self.temp, "missing.mp4")
        _, pp = self.pp()
        files, info = run(pp, self.info(path))
        self.assertEqual(files, [])
        self.assertEqual(info["filepath"], path)
        self.assertTrue(any("missing" in m for m in self.logger.messages))
        self.assertEqual(os.listdir(self.temp), [])
        # A directory is no file either
        os.mkdir(path)
        self.assertEqual(run(pp, self.info(path))[0], [])
        self.assertEqual(os.listdir(path), [])
        self.assertEqual(self.logger.warnings, [])

    def test_infos_without_a_file_are_skipped(self):
        _, pp = self.pp()
        for info in (
            {"__real_download": True},
            {"__real_download": True, "ext": "mp4"},
            {"__real_download": True, "filepath": "x.mp4"},
            {"__real_download": True, "ext": "mp4", "filepath": None},
            {"__real_download": True, "ext": None, "filepath": "x.mp4"},
        ):
            with self.subTest(info=info):
                self.assertEqual(run(pp, dict(info))[0], [])
        self.assertEqual(self.logger.warnings, [])

    def test_a_corrupt_file_only_warns(self):
        path = self.write(os.path.join(self.temp, "abc.mp4"), b"not an mp4 at all")
        _, pp = self.pp()
        files, info = run(pp, self.info(path))
        self.assertEqual(files, [])
        self.assertEqual(info["filepath"], path)
        (warning,) = self.logger.warnings
        self.assertTrue(warning.startswith("[Atoms] Atoms not written, "))
        self.assertEqual(self.read(path), b"not an mp4 at all")
        self.assertEqual(self.leftovers(path), ["abc.mp4"])

    def test_a_mutagen_error_only_warns(self):
        path = self.mp4(title="Title")
        original = self.read(path)
        _, pp = self.pp()
        with mock.patch("vidl.metadata.MP4", side_effect=ValueError("broken")):
            files, _ = run(pp, self.info(path))
        self.assertEqual(files, [])
        (warning,) = self.logger.warnings
        self.assertIn("ValueError: broken", warning)
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.leftovers(path), ["abc.mp4"])

    def test_a_failing_save_leaves_the_file_unchanged(self):
        path = self.mp4(title="Title")
        original = self.read(path)
        before = self.age(path)

        def failing_save(mp4: MP4, *args: object, **kwargs: object) -> None:
            # Fails halfway through, after damaging the file it saves
            with open(cast(str, mp4.filename), "r+b") as f:
                _ = f.write(b"garbage")
            raise OSError("disk full")

        _, pp = self.pp()
        with mock.patch.object(MP4, "save", failing_save):
            files, info = run(pp, self.info(path))
        self.assertEqual((files, info["filepath"]), ([], path))
        (warning,) = self.logger.warnings
        self.assertIn("OSError: disk full", warning)
        self.assertEqual(self.read(path), original)
        self.assertEqual(os.stat(path).st_mtime_ns, before)
        self.assertEqual(self.leftovers(path), ["abc.mp4"])
        self.assertIn("\u00a9too", tags_of(path))
        # And the next run works
        self.logger.warnings.clear()
        _ = run(pp, self.info(path))
        self.assertNotIn("\u00a9too", tags_of(path))
        self.assertEqual(self.logger.warnings, [])

    def test_file_system_errors_only_warn(self):
        path = self.mp4()
        original = self.read(path)
        _, pp = self.pp()
        with mock.patch("shutil.copy2", side_effect=OSError("no space")):
            files, _ = run(pp, self.info(path))
        self.assertEqual(files, [])
        (warning,) = self.logger.warnings
        self.assertIn("OSError: no space", warning)
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.leftovers(path), ["abc.mp4"])
        self.logger.warnings.clear()
        with mock.patch("os.replace", side_effect=PermissionError("denied")):
            _ = run(pp, self.info(path))
        self.assertIn("PermissionError: denied", self.logger.warnings[0])
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.leftovers(path), ["abc.mp4"])

    def test_a_failing_logger_does_not_matter(self):
        path = self.write(os.path.join(self.temp, "abc.mp4"), b"not an mp4")
        self.logger.fail = True
        _, pp = self.pp()
        self.assertEqual(run(pp, self.info(path))[0], [])

    def test_progress_is_reported_once(self):
        path = self.mp4()
        ydl, pp = self.pp()
        _ = self.run_through(ydl, pp, self.info(path))
        self.assertEqual(self.statuses("Atoms"), ["started", "finished"])
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")

    def test_the_plain_processor_works_without_the_guard(self):
        path = self.mp4()
        ydl = self.ydl()
        pp = AtomPostProcessor(ydl)
        ydl.add_post_processor(pp, when="post_process")
        info = self.info(path)
        del info["__real_download"]
        _ = self.run_through(ydl, pp, info)
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(self.statuses("Atoms"), ["started", "finished"])

    def test_existing_files_are_never_touched(self):
        for flag in (False, None, "missing"):
            with self.subTest(flag=flag):
                self.hooks.clear()
                path = self.mp4()
                original = self.read(path)
                before = self.age(path)
                info = self.info(path)
                if flag == "missing":
                    del info["__real_download"]
                else:
                    info["__real_download"] = flag
                ydl, pp = self.pp()
                files, _ = run(pp, info)
                self.assertEqual(files, [])
                self.assertEqual(self.read(path), original)
                self.assertEqual(os.stat(path).st_mtime_ns, before)
                self.assertEqual(self.leftovers(path), ["abc.mp4"])
                self.assertEqual(self.hooks, [])  # not reported as running
                self.assertIn("\u00a9too", tags_of(path))
                _ = self.run_through(ydl, pp, dict(info))
                self.assertEqual(self.read(path), original)
                self.assertEqual(os.stat(path).st_mtime_ns, before)
                self.assertEqual(self.hooks, [])
                os.remove(path)


class TestEmbedSubtitles(FileTestCase):
    def subtitles(self, directory: str, *langs: str) -> dict[str, dict[str, Any]]:
        return {
            lang: {
                "ext": "vtt",
                "url": f"https://x/{lang}.vtt",
                "filepath": self.write(os.path.join(directory, f"abc.{lang}.vtt"), VTT),
            }
            for lang in langs
        }

    def info(self, path: str, **extra: object) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": "abc",
            "ext": "mp4",
            "filepath": path,
            "__real_download": True,
        }
        info.update(extra)
        return info

    def streams(self, path: str, kind: str) -> list[dict[str, Any]]:
        return [s for s in ffprobe(path)["streams"] if s["codec_type"] == kind]

    @needs_ffmpeg
    def test_subtitles_are_embedded_in_new_downloads(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        before = float(ffprobe(path)["format"]["duration"])
        subtitles = self.subtitles(self.temp, "en", "de")
        pp = new_file_embed_subtitle_pp(self.ydl())
        info = self.info(path, requested_subtitles=subtitles)
        files, result = run(pp, info)
        # The sidecar files are for deletion, the video stays in place
        self.assertEqual(
            sorted(files), sorted(s["filepath"] for s in subtitles.values())
        )
        self.assertEqual(result["filepath"], path)
        found = self.streams(path, "subtitle")
        self.assertEqual([s["codec_name"] for s in found], ["mov_text", "mov_text"])
        self.assertEqual([s["tags"]["language"] for s in found], ["eng", "deu"])
        self.assertEqual(len(self.streams(path, "video")), 1)
        self.assertEqual(len(self.streams(path, "audio")), 1)
        after = float(ffprobe(path)["format"]["duration"])
        self.assertAlmostEqual(after, before, places=1)
        self.assertEqual(self.logger.warnings, [])
        # The remuxing left no temporary file
        self.assertEqual(
            sorted(os.listdir(self.temp)), ["abc.de.vtt", "abc.en.vtt", "abc.mp4"]
        )

    @needs_ffmpeg
    def test_the_sidecar_files_are_deleted_by_yt_dlp(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        subtitles = self.subtitles(self.temp, "en")
        ydl = self.ydl()
        pp = new_file_embed_subtitle_pp(ydl)
        info = self.info(path, requested_subtitles=subtitles)
        _ = self.run_through(ydl, pp, info)
        self.assertFalse(os.path.exists(subtitles["en"]["filepath"]))
        self.assertEqual(len(self.streams(path, "subtitle")), 1)
        self.assertEqual(self.statuses("EmbedSubtitle"), ["started", "finished"])

    @needs_ffmpeg
    def test_json_subtitles_are_skipped_with_a_warning(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        subtitles = self.subtitles(self.temp, "en")
        json_file = self.write(os.path.join(self.temp, "abc.fr.json"), "{}")
        subtitles["fr"] = {"ext": "json", "filepath": json_file}
        pp = new_file_embed_subtitle_pp(self.ydl())
        files, _ = run(pp, self.info(path, requested_subtitles=subtitles))
        self.assertEqual(files, [subtitles["en"]["filepath"]])
        (found,) = self.streams(path, "subtitle")
        self.assertEqual(found["tags"]["language"], "eng")
        self.assertEqual(self.logger.warnings, ["JSON subtitles cannot be embedded"])
        self.assertTrue(os.path.exists(json_file))

    @needs_ffmpeg
    def test_only_json_subtitles_leave_the_file_alone(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        original = self.read(path)
        json_file = self.write(os.path.join(self.temp, "abc.en.json"), "{}")
        subtitles = {"en": {"ext": "json", "filepath": json_file}}
        pp = new_file_embed_subtitle_pp(self.ydl())
        files, _ = run(pp, self.info(path, requested_subtitles=subtitles))
        self.assertEqual(files, [])
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.logger.warnings, ["JSON subtitles cannot be embedded"])

    @needs_ffmpeg
    def test_missing_subtitle_files_are_skipped(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        subtitles = self.subtitles(self.temp, "en")
        subtitles["de"] = {"ext": "vtt", "filepath": os.path.join(self.temp, "no.vtt")}
        pp = new_file_embed_subtitle_pp(self.ydl())
        files, _ = run(pp, self.info(path, requested_subtitles=subtitles))
        self.assertEqual(files, [subtitles["en"]["filepath"]])
        self.assertEqual(len(self.streams(path, "subtitle")), 1)
        self.assertEqual(len(self.logger.warnings), 1)
        self.assertIn(
            "de subtitle because the file is missing", self.logger.warnings[0]
        )

    @needs_ffmpeg
    def test_no_subtitles_is_a_no_op(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"))
        original = self.read(path)
        pp = new_file_embed_subtitle_pp(self.ydl())
        for extra in ({}, {"requested_subtitles": None}, {"requested_subtitles": {}}):
            with self.subTest(extra=extra):
                self.assertEqual(run(pp, self.info(path, **extra))[0], [])
                self.assertEqual(self.read(path), original)
        self.assertEqual(self.logger.warnings, [])

    def test_existing_files_are_untouched_and_temp_subtitles_deleted(self):
        path = self.write(os.path.join(self.home, "abc.mp4"), b"original")
        before = self.age(path)
        temp = self.subtitles(os.path.join(self.temp, "Channel"), "en", "de")
        home = self.subtitles(os.path.join(self.home, "Channel"), "fr")
        outside_dir = self.temp + "-other"
        outside = self.subtitles(outside_dir, "es")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        link = os.path.join(self.temp, "link.vtt")
        os.symlink(home["fr"]["filepath"], link)
        subtitles = {
            **temp,
            **home,
            **outside,
            "it": {"ext": "vtt", "filepath": link},
            "pt": {"ext": "vtt", "filepath": self.temp},  # the directory itself
            "ru": {"ext": "vtt", "data": "WEBVTT"},  # nothing written
            "ja": {"ext": "vtt", "filepath": 5},
        }
        ydl = self.ydl()
        pp = new_file_embed_subtitle_pp(ydl)
        for extra in ({"__real_download": False}, {"__real_download": None}, {}):
            with self.subTest(extra=extra):
                info = self.info(path, requested_subtitles=subtitles)
                del info["__real_download"]
                info.update(extra)
                files, _ = run(pp, info)
                self.assertEqual(
                    sorted(files), sorted(s["filepath"] for s in temp.values())
                )
        self.assertEqual(self.read(path), b"original")
        self.assertEqual(os.stat(path).st_mtime_ns, before)
        self.assertEqual(self.hooks, [])
        # Through yt-dlp the temporary files are deleted, nothing else
        info = self.info(path, requested_subtitles=subtitles)
        del info["__real_download"]
        info["__files_to_move"] = {s["filepath"]: None for s in temp.values()}
        result = self.run_through(ydl, pp, info)
        for subtitle in temp.values():
            self.assertFalse(os.path.exists(subtitle["filepath"]))
        for subtitle in (*home.values(), *outside.values()):
            self.assertTrue(os.path.exists(subtitle["filepath"]))
        self.assertTrue(os.path.exists(link))
        self.assertEqual(result["__files_to_move"], {})
        self.assertEqual(self.read(path), b"original")
        self.assertEqual(self.hooks, [])

    def test_odd_subtitle_data_deletes_nothing(self):
        pp = new_file_embed_subtitle_pp(self.ydl())
        path = self.write(os.path.join(self.temp, "abc.vtt"))
        for value in (None, "x", [path], [None], 5, {}, {"en": None}, {"en": [path]}):
            with self.subTest(value=value):
                info = self.info(path, requested_subtitles=value)
                del info["__real_download"]
                self.assertEqual(run(pp, info)[0], [])
        no_temp = new_file_embed_subtitle_pp(self.ydl(paths={"home": self.home}))
        info = self.info(path, requested_subtitles=self.subtitles(self.temp, "en"))
        del info["__real_download"]
        self.assertEqual(run(no_temp, info)[0], [])
        self.assertTrue(os.path.exists(path))


@needs_ffmpeg
class TestAtomsEndToEnd(FileTestCase):
    """The meta_* tags map to the expected atoms of a real mp4."""

    DESCRIPTION = "First line #two\nSecond line, with more text. " + "word " * 80

    def video_info(self) -> dict[str, Any]:
        return {
            "id": "abc",
            "title": "Title #One",
            "description": self.DESCRIPTION,
            "channel": "Chan",
            "uploader": "Uploader",
            "channel_id": "UC123",
            "channel_url": "https://www.youtube.com/channel/UC123",
            "webpage_url": "https://www.youtube.com/watch?v=abc",
            "upload_date": "20240115",
            "timestamp": 1705320000,
            "height": 1080,
            "age_limit": 18,
            "extractor_key": "Youtube",
        }

    def video_stage(self, info: dict[str, Any]) -> dict[str, Any]:
        pp = MetadataPostProcessor()
        pp.set_context(
            MetadataContext(
                series="Series",
                episode_number=3,
                group="Playlist",
                channel_description="About the channel",
            )
        )
        return pp.run(info)[1]

    def check_atoms(self, tags: dict[str, Any]) -> None:
        self.assertEqual(tags["stik"], [10])
        self.assertEqual(tags["hdvd"], [2])
        self.assertEqual(tags["\u00a9day"], ["2024-01-15"])
        self.assertEqual(tags["tven"], ["abc"])
        self.assertEqual(tags["tvsn"], [1])
        self.assertEqual(tags["tves"], [3])
        self.assertEqual(tags["tvnn"], ["YouTube"])
        self.assertEqual(tags["tvsh"], ["Chan"])
        self.assertEqual(tags["\u00a9ART"], ["Chan"])
        self.assertEqual(tags["aART"], ["Chan"])
        self.assertEqual(tags["\u00a9alb"], ["Chan"])
        self.assertEqual(tags["ldes"], [self.DESCRIPTION])
        (short,) = tags["desc"]
        self.assertLessEqual(len(short), 255)
        self.assertTrue(short.endswith("word\u2026"))
        self.assertTrue(short.startswith("First line #two Second line, with"))
        self.assertEqual(tags["keyw"], ["One, two"])
        self.assertNotIn("\u00a9cmt", tags)
        self.assertEqual(tags["\u00a9grp"], ["Playlist"])

    def test_meta_names_map_to_the_right_atoms(self):
        # What FFmpegMetadataPP does with the meta_* fields, by hand
        info = self.video_stage(self.video_info())
        source = self.write(os.path.join(self.temp, "source.mp4"), base_mp4())
        options: list[str] = []
        for key, value in info.items():
            if key.startswith("meta_"):
                options += ["-metadata", f"{key[5:]}={value}"]
        # yt-dlp's own mapping comes first (the comment is the video URL),
        # it maps the season and episode numbers, which are no meta_* fields
        defaults = [
            *("-metadata", "comment=https://www.youtube.com/watch?v=abc"),
            *("-metadata", f"season_number={info['season_number']}"),
            *("-metadata", f"episode_sort={info['episode_number']}"),
        ]
        output = os.path.join(self.temp, "abc.mp4")
        ffmpeg("-i", source, "-c", "copy", *defaults, *options, output)
        tags = tags_of(output)
        self.check_atoms(tags)
        self.assertIn("\u00a9too", tags)  # ffmpeg always writes it

    def test_the_whole_chain_with_the_real_metadata_post_processor(self):
        path = tiny_mp4(os.path.join(self.temp, "abc.mp4"), {"title": "Old"})
        info = self.video_stage(self.video_info())
        info.update(ext="mp4", filepath=path, __real_download=True)
        ydl = self.ydl()
        for pp in (new_file_metadata_pp(ydl), new_file_atom_pp(ydl)):
            info = self.run_through(ydl, pp, info)
        self.assertEqual(self.logger.warnings, [])
        self.assertEqual(self.logger.errors, [])
        tags = tags_of(path)
        self.check_atoms(tags)
        # yt-dlp's own mapping is overridden, e.g. the uploader as artist
        self.assertNotIn("Uploader", str(tags))
        self.assertEqual(tags["\u00a9nam"], ["Title #One"])
        # And the atoms ffmpeg cannot write
        self.assertNotIn("\u00a9too", tags)
        self.assertEqual(tags["rtng"], [1])
        self.assertEqual(tags["sdes"], ["About the channel"])
        self.assertEqual(freeform(path, "CHANNEL_ID"), "UC123")
        self.assertEqual(
            freeform(path, "CHANNEL_URL"), "https://www.youtube.com/channel/UC123"
        )
        self.assertEqual(
            freeform(path, "VIDEO_URL"), "https://www.youtube.com/watch?v=abc"
        )
        self.assertEqual(freeform(path, "UPLOAD_TIMESTAMP"), "2024-01-15T12:00:00Z")
        self.assertEqual(self.statuses("Metadata"), ["started", "finished"])
        self.assertEqual(self.statuses("Atoms"), ["started", "finished"])
        self.assertEqual(sorted(os.listdir(self.temp)), ["abc.mp4"])


if __name__ == "__main__":
    unittest.main()
