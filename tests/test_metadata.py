# pyright: standard
import copy
import os
import unittest
from collections.abc import Callable
from threading import Event, Thread
from typing import Any, ClassVar, cast

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP

from tests.helpers import IsolatedTestCase
from vidl.config import Config
from vidl.metadata import (
    MetadataContext,
    MetadataPostProcessor,
    NewFileEmbedThumbnailPP,
    NewFileMetadataPP,
    channel_thumbnail,
    episode_number,
    is_new_download,
    new_file_embed_thumbnail_pp,
    new_file_metadata_pp,
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
        self.fail = fail

    def debug(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

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

    def test_empty_context_changes_nothing(self):
        info = self.video_info()
        expected = copy.deepcopy(info)
        self.assertEqual(self.run_pp(MetadataContext(), info), expected)

    def test_episode_number_overwrites_and_sets_episode(self):
        info = self.run_pp(
            MetadataContext(episode_number=7), self.video_info(episode_number=99)
        )
        self.assertEqual(info["episode_number"], 7)
        self.assertEqual(info["episode"], "Episode title")
        self.assertNotIn("series", info)
        self.assertNotIn("season_number", info)

    def test_existing_episode_is_kept(self):
        info = self.run_pp(
            MetadataContext(episode_number=7), self.video_info(episode="Real episode")
        )
        self.assertEqual(info["episode"], "Real episode")
        info = self.run_pp(
            MetadataContext(episode_number=7), self.video_info(episode="")
        )
        self.assertEqual(info["episode"], "Episode title")

    def test_episode_needs_a_title(self):
        info = self.video_info()
        del info["title"]
        info = self.run_pp(MetadataContext(episode_number=2), info)
        self.assertEqual(info["episode_number"], 2)
        self.assertNotIn("episode", info)

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

    def test_season_number(self):
        info = self.run_pp(
            MetadataContext(season_number=2), self.video_info(season_number=5)
        )
        self.assertEqual(info["season_number"], 2)
        info = self.run_pp(MetadataContext(), self.video_info(season_number=5))
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

    def process(self, context: MetadataContext | None, **params: object):
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
        self.assertEqual(seen["episode"], "Episode title")
        self.assertEqual(seen["series"], "Channel")
        self.assertNotIn("season_number", seen)
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


if __name__ == "__main__":
    unittest.main()
