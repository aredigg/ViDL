# pyright: standard
import contextlib
import io
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Mapping
from typing import Any, ClassVar, cast
from unittest import mock

from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from yt_dlp import YoutubeDL

from tests.helpers import IsolatedTestCase
from vidl import retag
from vidl.episodes import PLAYLIST, UPLOADS, EpisodeIndex
from vidl.error import Error
from vidl.metadata import (
    MetadataContext,
    metadata_updates,
    new_file_atom_pp,
    new_file_metadata_pp,
)
from vidl.retag import (
    Aborted,
    Arguments,
    Change,
    Probe,
    Retagger,
    SourceError,
    Status,
    Target,
    YoutubeSource,
    candidate_ids,
    clone,
    cookie_browser,
    ffmpeg_tags,
    find_videos,
    is_current,
    normal,
    plan_changes,
    probe_file,
    target_atoms,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are needed")

ID = "dQw4w9WgXcQ"
OTHER = "abcdefghijk"
PREFIX = "----:com.apple.iTunes:"
OLD_TIME = 1_600_000_000
# The uploads of the channel, newest first: the first video is episode 1
NEWER, OLDER = "newer000001", "older000001"
UPLOADS_OF_CHANNEL = [NEWER, OTHER, ID, OLDER]  # ID is episode 2, OTHER 3
DESCRIPTION = "Line one #Gaming #fun\nLine two #gaming\n\n" + "word " * 80


def ffmpeg(*args: str) -> None:
    _ = subprocess.run(
        [cast(str, FFMPEG), "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


_base: bytes | None = None


def base_mp4() -> bytes:
    """A two second mpeg4+aac mp4 (ffmpeg here has no libx264), made once."""
    global _base
    if _base is None:
        with tempfile.TemporaryDirectory(prefix="vidl-test-") as directory:
            path = os.path.join(directory, "base.mp4")
            ffmpeg(
                *("-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=10"),
                *("-f", "lavfi", "-i", "sine=duration=2"),
                *("-c:v", "mpeg4", "-c:a", "aac", "-shortest", path),
            )
            with open(path, "rb") as f:
                _base = f.read()
    return _base


def remuxed(path: str, metadata: Mapping[str, str] | None = None) -> str:
    """The base video remuxed by ffmpeg (-c copy) as yt-dlp does, so it has the
    encoder atom. The container follows the extension."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    source = path + ".source.mp4"
    with open(source, "wb") as f:
        _ = f.write(base_mp4())
    options: list[str] = []
    for key, value in (metadata or {}).items():
        options += ["-metadata", f"{key}={value}"]
    ffmpeg("-i", source, "-c", "copy", *options, path)
    os.remove(source)
    return path


def old_file(
    path: str, tven: str | None = None, extra: Mapping[str, Any] | None = None
) -> str:
    """A file as older versions of ViDL wrote it: the title in tven, the video
    URL as comment, the date without dashes, no TV show type, and an episode
    number, a season, a grouping, a cover and a composer, none of which can be
    derived from the video."""
    remuxed(
        path,
        {
            "title": "Old title",
            "episode_id": tven or "Old title",
            "comment": f"https://www.youtube.com/watch?v={ID}",
            "date": "20240115",
            "artist": "Old artist",
        },
    )
    mp4 = MP4(path)
    assert mp4.tags is not None
    mp4.tags["tves"] = [4]
    mp4.tags["tvsn"] = [2]
    mp4.tags["©grp"] = ["Shorts"]
    mp4.tags["©wrt"] = ["Someone"]
    mp4.tags["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0cover", MP4Cover.FORMAT_JPEG)]
    for key, value in (extra or {}).items():
        mp4.tags[key] = value
    mp4.save()
    os.utime(path, (OLD_TIME, OLD_TIME))
    os.chmod(path, 0o640)
    return path


def tags_of(path: str) -> dict[str, Any]:
    tags = MP4(path).tags
    return dict(tags) if tags is not None else {}


def texts(path: str) -> dict[str, tuple[str, ...]]:
    return {atom: normal(values) for atom, values in tags_of(path).items()}


def youtube_info(video_id: str = ID, **extra: object) -> dict[str, object]:
    """What yt-dlp extracts of a YouTube video, the fields that matter."""
    info: dict[str, object] = {
        "id": video_id,
        "title": "The title",
        "description": DESCRIPTION,
        "upload_date": "20240115",
        "timestamp": 1705320000,
        "duration": 2,
        "channel": "The Channel",
        "uploader": "The Channel",
        "channel_id": "UC123",
        "channel_url": "https://www.youtube.com/channel/UC123",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "categories": ["Gaming"],
        "tags": ["a", "b"],
        "age_limit": 0,
        "height": 1080,
        "formats": [{"ext": "mp4", "height": 1080}],
    }
    info.update(extra)
    return info


class FakeSource:
    """Stands in for YouTube: the info by video id, the descriptions by channel."""

    def __init__(
        self,
        videos: Mapping[str, object] | None = None,
        channels: Mapping[str, object] | None = None,
        uploads: Mapping[str, object] | None = None,
    ) -> None:
        self.videos = dict(videos or {})
        self.channels = dict(channels or {"UC123": "About the channel"})
        # Per channel the ids newest first, or an exception, or None: no list
        if uploads is None:
            # The videos given are of the channel too: newer than those above
            extra = [v for v in self.videos if v not in (ID, OTHER)]
            uploads = {"UC123": [NEWER, *extra, OTHER, ID, OLDER]}
        self.uploads = dict(uploads)
        self.requests: list[str] = []
        self.channel_requests: list[str] = []
        self.upload_requests: list[str] = []
        self.closed = False

    def episodes(self, info: Mapping[str, object]) -> EpisodeIndex | None:
        key = str(info.get("channel_id"))
        value = self.uploads.get(key)
        if value is None:
            return None

        def load() -> list[str]:
            self.upload_requests.append(key)
            if isinstance(value, BaseException):
                raise value
            return list(cast("list[str]", value))

        return EpisodeIndex(load, True, UPLOADS)

    def video(self, video_id: str) -> Mapping[str, object]:
        self.requests.append(video_id)
        value = self.videos[video_id]
        if isinstance(value, BaseException):
            raise value
        return cast("Mapping[str, object]", value)

    def channel_description(self, info: Mapping[str, object]) -> str | None:
        key = str(info.get("channel_id"))
        self.channel_requests.append(key)
        value = self.channels.get(key)
        if isinstance(value, BaseException):
            raise value
        return cast("str | None", value)

    def close(self) -> None:
        self.closed = True


def failure(text: str) -> SourceError:
    return SourceError(Error(text))


class TestFfmpegTags(unittest.TestCase):
    def test_the_mapping_of_yt_dlp(self):
        tags = ffmpeg_tags(
            {
                "title": "T",
                "track": "Track",
                "upload_date": "20240115",
                "description": "D",
                "webpage_url": "https://u",
                "uploader": "U",
                "categories": ["Gaming", "Fun"],
                "tags": ["x"],
                "episode_number": 3,
                "season_number": 2,
                "id": "ignored",
            }
        )
        self.assertEqual(tags["title"], "Track")  # track comes first
        self.assertEqual(tags["date"], "20240115")
        self.assertEqual((tags["description"], tags["synopsis"]), ("D", "D"))
        self.assertEqual(tags["comment"], "https://u")
        self.assertEqual(tags["artist"], "U")
        self.assertEqual(tags["genre"], "Gaming, Fun")  # categories before tags
        self.assertEqual((tags["episode_sort"], tags["season_number"]), ("3", "2"))
        self.assertNotIn("episode_id", tags)

    def test_meta_fields_override_and_empty_removes(self):
        tags = ffmpeg_tags(
            {
                "title": "T",
                "webpage_url": "https://u",
                "meta_title": "Override",
                "meta_comment": "",
                "meta_show": "Show",
                "meta1_language": "ignored",  # of a stream
                "meta_number": 5,  # not a text
            }
        )
        self.assertEqual(tags["title"], "Override")
        self.assertEqual(tags["comment"], "")
        self.assertEqual(tags["show"], "Show")
        self.assertNotIn("language", tags)
        self.assertNotIn("number", tags)

    def test_nothing_without_a_value(self):
        self.assertEqual(ffmpeg_tags({}), {})
        self.assertEqual(ffmpeg_tags({"title": "", "genres": None}), {})

    def test_nul_characters_are_removed(self):
        self.assertEqual(ffmpeg_tags({"title": "a\0b"})["title"], "ab")


class TestTarget(unittest.TestCase):
    def target(self, **extra: object) -> Target:
        info = youtube_info(ID, **extra)
        context = MetadataContext(episode_number=4, group="Shorts")
        info.update(metadata_updates(info, context))
        return target_atoms(info)

    def test_the_tags_of_a_new_download(self):
        values = {atom: normal(v) for atom, v in self.target().values.items()}
        self.assertEqual(values["©nam"], ("The title",))
        for atom in ("©ART", "aART", "©alb", "tvsh"):
            self.assertEqual(values[atom], ("The Channel",))
        self.assertEqual(values["stik"], ("10",))
        self.assertEqual(values["hdvd"], ("2",))  # a height of 1080
        self.assertEqual(values["©day"], ("2024-01-15",))
        self.assertEqual(values["©gen"], ("Gaming",))
        self.assertEqual(values["tves"], ("4",))
        self.assertEqual(values["tven"], (ID,))
        self.assertEqual(values["tvsn"], ("1",))
        self.assertEqual(values["tvnn"], ("YouTube",))
        self.assertEqual(values["©grp"], ("Shorts",))
        self.assertEqual(values["ldes"], (DESCRIPTION,))
        self.assertLessEqual(len(values["desc"][0]), 255)
        self.assertEqual(values["keyw"], ("Gaming, fun",))
        self.assertEqual(values[PREFIX + "CHANNEL_ID"], ("UC123",))
        self.assertEqual(values[PREFIX + "UPLOAD_TIMESTAMP"], ("2024-01-15T12:00:00Z",))

    def test_hdvd_follows_the_height(self):
        for height, level in ((719, "0"), (720, "1"), (1080, "2"), (2160, "3")):
            with self.subTest(height=height):
                values = self.target(height=height).values
                self.assertEqual(normal(values["hdvd"]), (level,))

    def test_what_is_removed(self):
        target = self.target()
        self.assertEqual(target.remove, {"©too", "©enc", "©cmt", "rtng"})
        # An age limit of 18 sets the rating
        adult = self.target(age_limit=18)
        self.assertEqual(adult.values["rtng"], [1])
        self.assertNotIn("rtng", adult.remove)

    def test_the_comment_is_removed_not_written(self):
        self.assertNotIn("©cmt", self.target().values)

    def test_missing_information_leaves_tags_alone(self):
        info = youtube_info()
        for key in ("description", "categories", "tags", "upload_date", "timestamp"):
            del info[key]
        info.update(metadata_updates(info, MetadataContext()))
        target = target_atoms(info)
        for atom in ("ldes", "desc", "©gen", "©day", "keyw", "tves", "©grp"):
            self.assertNotIn(atom, target.values)
            self.assertNotIn(atom, target.remove)

    def test_numbers_are_numbers(self):
        values = self.target().values
        for atom in ("stik", "hdvd", "tves", "tvsn"):
            self.assertIsInstance(values[atom][0], int)

    def test_odd_numbers_are_not_written(self):
        info = {
            "meta_episode_id": ID,
            "meta_media_type": "x",
            "meta_hd_video": "999",
            "meta_episode_sort": "-1",
        }
        self.assertEqual(target_atoms(info).values.get("tven"), [ID])
        for atom in ("stik", "hdvd", "tves"):
            self.assertNotIn(atom, target_atoms(info).values)


class TestPlan(unittest.TestCase):
    def test_changes_are_the_tags_that_differ(self):
        tags = {
            "©nam": ["Old"],
            "©day": ["2024-01-15"],
            "©cmt": ["url"],
            "©too": ["Lavf"],
            "©wrt": ["x"],
        }
        target = Target(
            {"©nam": ["New"], "©day": ["2024-01-15"], "stik": [10]},
            frozenset({"©cmt", "©too", "rtng"}),
        )
        changes = plan_changes(tags, target)
        self.assertEqual(
            [(c.atom, c.old, c.new) for c in changes],
            [
                ("©nam", "Old", "New"),
                ("stik", None, "10"),
                ("©cmt", "url", None),
                ("©too", "Lavf", None),
            ],
        )

    def test_nothing_to_change(self):
        tags = {"©nam": ["Same"]}
        self.assertEqual(
            plan_changes(tags, Target({"©nam": ["Same"]}, frozenset({"©too"}))), []
        )

    def test_freeform_and_covers_compare_by_content(self):
        free = MP4FreeForm(b"UC123")
        self.assertEqual(normal([free]), ("UC123",))
        self.assertEqual(normal([MP4Cover(b"x" * 5)]), ("<cover 5 bytes>",))
        self.assertEqual(normal(None), ())
        self.assertEqual(normal([10, 3]), ("10", "3"))
        tags = {PREFIX + "CHANNEL_ID": [MP4FreeForm(b"UC123")]}
        target = Target({PREFIX + "CHANNEL_ID": [MP4FreeForm(b"UC123")]}, frozenset())
        self.assertEqual(plan_changes(tags, target), [])

    def test_changes_are_shown_short(self):
        text = str(Change(PREFIX + "VIDEO_URL", None, "x" * 200))
        self.assertTrue(text.startswith("VIDEO_URL: (none) -> '"))
        self.assertLess(len(text), 100)
        self.assertEqual(str(Change("©nam", "a\nb", None)), "©nam: 'a b' -> (none)")


class TestCandidates(unittest.TestCase):
    def test_tven_then_the_file_name(self):
        tags = {"tven": [ID]}
        self.assertEqual(candidate_ids(tags, f"/x/{OTHER}.mp4"), [ID, OTHER])

    def test_a_title_is_no_id(self):
        # Older versions wrote the title there
        tags = {"tven": ["Some long video title"]}
        self.assertEqual(candidate_ids(tags, f"/x/{ID}.mp4"), [ID])

    def test_the_same_id_once(self):
        self.assertEqual(candidate_ids({"tven": [ID]}, f"/x/{ID}.m4v"), [ID])

    def test_no_id_at_all(self):
        self.assertEqual(candidate_ids({}, "/x/holiday.mp4"), [])
        self.assertEqual(candidate_ids({"tven": ["short"]}, "/x/holiday video.mp4"), [])

    def test_the_shape_of_an_id(self):
        for text, expected in (
            ("abc-_123XYZ", True),
            ("abc 123XYZ.", False),
            ("tooshort", False),
            ("waytoolongforanid", False),
            ("åäö12345678", False),
        ):
            with self.subTest(text=text):
                found = candidate_ids({"tven": [text]}, "/x/none here.mp4")
                self.assertEqual(found == [text], expected)

    def test_whitespace_is_stripped(self):
        self.assertEqual(candidate_ids({"tven": [f" {ID}\n"]}, "/x/y z.mp4"), [ID])


class TestIsCurrent(unittest.TestCase):
    def current(self) -> dict[str, Any]:
        return {
            "tven": [ID],
            "stik": [10],
            "tvsn": [1],
            PREFIX + "VIDEO_URL": [MP4FreeForm(b"u")],
            PREFIX + "CHANNEL_ID": [MP4FreeForm(b"c")],
            PREFIX + "UPLOAD_TIMESTAMP": [MP4FreeForm(b"t")],
            PREFIX + "EPISODE_ORDER": [MP4FreeForm(b"uploads")],
        }

    def test_current(self):
        self.assertTrue(is_current(self.current()))

    def test_each_part_is_needed(self):
        for key in self.current():
            with self.subTest(missing=key):
                tags = self.current()
                del tags[key]
                self.assertFalse(is_current(tags))

    def test_old_leftovers_make_it_old(self):
        for atom in ("©too", "©enc", "©cmt"):
            with self.subTest(atom=atom):
                self.assertFalse(is_current({**self.current(), atom: ["x"]}))

    def test_the_title_in_tven_is_old(self):
        self.assertFalse(is_current({**self.current(), "tven": ["Some title"]}))

    def test_another_media_type_is_old(self):
        self.assertFalse(is_current({**self.current(), "stik": [9]}))

    def test_a_season_other_than_one_is_old(self):
        for season in ([2], [0], [2024], [], ["x"]):
            with self.subTest(season=season):
                tags = {**self.current(), "tvsn": season}
                self.assertFalse(is_current(tags))

    def test_season_one_is_current(self):
        self.assertTrue(is_current({**self.current(), "tvsn": ["1"]}))


class TestFindVideos(unittest.TestCase):
    def test_finds_mp4_and_m4v_only(self):
        with tempfile.TemporaryDirectory() as folder:
            names = [
                "a.mp4",
                "B.MP4",
                "c.m4v",
                "d.mkv",
                "e.m4a",
                "f.mp4.part",
                ".hidden.mp4",
                "._apple.mp4",
                "sub/g.mp4",
                "sub/deeper/h.m4v",
                ".vidl-x/i.mp4",
                ".git/j.mp4",
            ]
            for name in names:
                path = os.path.join(folder, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
            found = [os.path.relpath(p, folder) for p in find_videos(folder)]
            self.assertEqual(
                found, ["B.MP4", "a.mp4", "c.m4v", "sub/deeper/h.m4v", "sub/g.mp4"]
            )
            flat = [
                os.path.relpath(p, folder) for p in find_videos(folder, recursive=False)
            ]
            self.assertEqual(flat, ["B.MP4", "a.mp4", "c.m4v"])

    def test_links_to_folders_are_not_followed(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            tempfile.TemporaryDirectory() as other,
        ):
            open(os.path.join(other, "x.mp4"), "wb").close()
            os.symlink(other, os.path.join(folder, "link"))
            self.assertEqual(find_videos(folder), [])

    def test_empty_and_missing_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(find_videos(folder), [])
        self.assertEqual(find_videos("/does/not/exist"), [])


@needs_ffmpeg
class TestClone(IsolatedTestCase):
    def test_keeps_the_time_and_permissions(self):
        directory = self.temp_dir()
        source = os.path.join(directory, "a.mp4")
        with open(source, "wb") as f:
            _ = f.write(b"data" * 1000)
        os.utime(source, (OLD_TIME, OLD_TIME))
        os.chmod(source, 0o640)
        target = os.path.join(directory, "b.tmp")
        clone(source, target)
        info = os.stat(target)
        self.assertEqual(info.st_mtime, OLD_TIME)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o640)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"data" * 1000)

    def test_falls_back_when_cp_is_not_available(self):
        directory = self.temp_dir()
        source = os.path.join(directory, "a.mp4")
        with open(source, "wb") as f:
            _ = f.write(b"data")
        os.utime(source, (OLD_TIME, OLD_TIME))
        target = os.path.join(directory, "b.tmp")
        with mock.patch("vidl.retag.subprocess.run", side_effect=OSError("no cp")):
            clone(source, target)
        self.assertEqual(os.stat(target).st_mtime, OLD_TIME)

    def test_a_failed_cp_leaves_no_partial_file(self):
        directory = self.temp_dir()
        source = os.path.join(directory, "a.mp4")
        with open(source, "wb") as f:
            _ = f.write(b"data")
        target = os.path.join(directory, "b.tmp")

        def failing(command: list[str], **kwargs: Any) -> Any:
            with open(command[-1], "wb") as f:
                _ = f.write(b"par")
            return subprocess.CompletedProcess(command, 1)

        with mock.patch("vidl.retag.subprocess.run", side_effect=failing):
            clone(source, target)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"data")  # by the fallback, not the partial file

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("xattr"),
        "extended attributes of macOS",
    )
    def test_keeps_the_extended_attributes(self):
        directory = self.temp_dir()
        source = os.path.join(directory, "a.mp4")
        with open(source, "wb") as f:
            _ = f.write(b"data")
        _ = subprocess.run(
            ["xattr", "-w", "com.example.test", "kept", source], check=True
        )
        target = os.path.join(directory, "b.tmp")
        clone(source, target)
        done = subprocess.run(
            ["xattr", "-p", "com.example.test", target],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.stdout.strip(), "kept")


@needs_ffmpeg
class TestProbe(IsolatedTestCase):
    def test_streams_and_height(self):
        path = remuxed(os.path.join(self.temp_dir(), "a.mp4"))
        probe = probe_file(path)
        assert probe is not None
        self.assertEqual(probe.height, 180)
        kinds = [stream[0] for stream in probe.streams]
        self.assertEqual(sorted(kinds), ["audio", "video"])

    def test_not_a_video(self):
        path = os.path.join(self.temp_dir(), "a.mp4")
        with open(path, "wb") as f:
            _ = f.write(b"not a video")
        self.assertIsNone(probe_file(path))
        self.assertIsNone(probe_file(path + ".missing"))

    def test_without_ffprobe(self):
        with mock.patch("vidl.retag.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(probe_file("/x/a.mp4"))


class RetagTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.temp_dir()
        self.output: list[str] = []
        self.pauses: list[float] = []
        self.now = 1000.0  # the clock of the retagger, moved by pauses

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def retagger(self, source: FakeSource, **options: Any) -> Retagger:
        options.setdefault("sleep", 2.5)
        return Retagger(
            source,
            pause=self.pause,
            clock=self.clock,
            write=self.output.append,
            **options,
        )

    def path(self, name: str = f"{ID}.mp4") -> str:
        return os.path.join(self.folder, name)

    def bytes_of(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def leftovers(self) -> list[str]:
        return [n for n in os.listdir(self.folder) if n.startswith(".vidl-retag")]


@needs_ffmpeg
class TestRetagFile(RetagTestCase):
    def test_an_old_file_gets_the_tags_of_a_new_download(self):
        path = old_file(self.path())
        before = texts(path)
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        after = texts(path)
        self.assertEqual(after["tven"], (ID,))  # was the title
        self.assertEqual(after["©day"], ("2024-01-15",))
        self.assertEqual(after["stik"], ("10",))
        self.assertEqual(after["©nam"], ("The title",))
        self.assertEqual(after["sdes"], ("About the channel",))
        for atom in ("©too", "©cmt"):
            self.assertNotIn(atom, after)
        self.assertIn("©too", before)
        self.assertEqual(after[PREFIX + "CHANNEL_ID"], ("UC123",))
        self.assertEqual(source.requests, [ID])
        self.assertEqual(source.channel_requests, ["UC123"])

    def test_grouping_cover_and_other_tags_are_kept(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        after = texts(path)
        # The episode is counted over the uploads of the channel now, the old
        # number is replaced
        self.assertEqual(after["tves"], ("2",))
        self.assertEqual(after[PREFIX + "EPISODE_ORDER"], ("uploads",))
        # The season is 1 as for a download, the old one is replaced
        self.assertEqual(after["tvsn"], ("1",))
        self.assertEqual(after["©grp"], ("Shorts",))
        self.assertEqual(after["©wrt"], ("Someone",))
        self.assertEqual(after["covr"], ("<cover 9 bytes>",))

    def test_the_file_is_otherwise_as_it_was(self):
        path = old_file(self.path())
        before = os.stat(path)
        probe = probe_file(path)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        after = os.stat(path)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual(probe_file(path), probe)
        self.assertEqual(MP4(path).info.length, MP4(self.copy_of_base()).info.length)
        self.assertEqual(self.leftovers(), [])

    def copy_of_base(self) -> str:
        path = os.path.join(self.temp_dir(), "base.mp4")
        with open(path, "wb") as f:
            _ = f.write(base_mp4())
        return path

    def test_the_media_data_is_untouched(self):
        # The audio and video packets are the same, compared by ffmpeg
        path = old_file(self.path())
        original = os.path.join(self.temp_dir(), "copy.mp4")
        shutil.copy2(path, original)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)

        def packets(file: str) -> str:
            done = subprocess.run(
                [
                    cast(str, FFMPEG),
                    *("-v", "error", "-i", file),
                    *("-map", "0:a", "-map", "0:v:0", "-c", "copy"),
                    *("-f", "streamhash", "-hash", "md5", "-"),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return done.stdout

        self.assertEqual(packets(path), packets(original))

    def test_the_id_of_tven_is_used(self):
        path = old_file(self.path("some video.mp4"), tven=ID)
        source = FakeSource({ID: youtube_info()})
        self.assertEqual(self.retagger(source).process(path).status, Status.UPDATED)
        self.assertEqual(source.requests, [ID])

    def test_the_file_name_is_used_without_an_id_in_tven(self):
        path = old_file(self.path())
        source = FakeSource({ID: youtube_info()})
        _ = self.retagger(source).process(path)
        self.assertEqual(source.requests, [ID])

    def test_a_wrong_tven_falls_back_to_the_file_name(self):
        # A title that looks like an id: the video does not exist
        path = old_file(self.path(), tven=OTHER)
        source = FakeSource({OTHER: failure("Video unavailable"), ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(source.requests, [OTHER, ID])
        # Before each request but the first: the second video, the channel
        # and its uploads
        self.assertEqual(self.pauses, [2.5, 2.5, 2.5])

    def test_another_video_with_a_matching_id_form_is_not_taken(self):
        path = old_file(self.path(), tven=OTHER)
        source = FakeSource(
            {OTHER: youtube_info(OTHER, duration=900), ID: youtube_info()}
        )
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(texts(path)["tven"], (ID,))

    def test_no_id_is_skipped_without_a_request(self):
        path = old_file(self.path("holiday.mp4"))
        source = FakeSource()
        before = self.bytes_of(path)
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertIn("no video id", outcome.message)
        self.assertEqual(source.requests, [])
        self.assertEqual(self.bytes_of(path), before)

    def test_a_different_duration_is_skipped(self):
        path = old_file(self.path())
        before = self.bytes_of(path)
        source = FakeSource({ID: youtube_info(duration=754)})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertIn("12:34", outcome.message)
        self.assertIn("0:02", outcome.message)
        self.assertEqual(self.bytes_of(path), before)

    def test_the_duration_tolerance(self):
        for duration, status in (
            (2, Status.UPDATED),
            (6.9, Status.UPDATED),  # within 5 seconds
            (7.5, Status.SKIPPED),
        ):
            with self.subTest(duration=duration):
                path = old_file(self.path(f"{ID}.mp4"))
                source = FakeSource({ID: youtube_info(duration=duration)})
                self.assertEqual(self.retagger(source).process(path).status, status)
        path = old_file(self.path(f"{ID}.mp4"))
        wide = self.retagger(FakeSource({ID: youtube_info(duration=40)}), tolerance=60)
        self.assertEqual(wide.process(path).status, Status.UPDATED)

    def test_a_long_video_allows_two_percent(self):
        retagger = self.retagger(FakeSource({ID: youtube_info(duration=1019)}))
        fetch = retagger._Retagger__fetch  # pyright: ignore[reportAttributeAccessIssue]
        self.assertEqual(fetch([ID], 1000.0)["id"], ID)  # 19 s is less than 2%
        retagger = self.retagger(FakeSource({ID: youtube_info(duration=1021)}))
        fetch = retagger._Retagger__fetch  # pyright: ignore[reportAttributeAccessIssue]
        with self.assertRaises(Exception) as caught:
            _ = fetch([ID], 1000.0)
        self.assertIn("17:01", str(caught.exception))  # 21 s is more

    def test_another_video_answered_is_skipped(self):
        path = old_file(self.path())
        source = FakeSource({ID: youtube_info(OTHER)})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertIn("another video", outcome.message)

    def test_an_unknown_video_is_skipped(self):
        path = old_file(self.path())
        before = self.bytes_of(path)
        source = FakeSource({ID: failure("Private video")})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertIn("Private video", outcome.message)
        self.assertEqual(self.bytes_of(path), before)

    def test_a_current_file_is_left_alone_without_a_request(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.CURRENT)
        self.assertEqual(source.requests, [])

    def test_force_checks_a_current_file_and_does_not_rewrite_it(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        inode, data = os.stat(path).st_ino, self.bytes_of(path)
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source, force=True).process(path)
        self.assertEqual(outcome.status, Status.UNCHANGED)
        self.assertEqual(source.requests, [ID])
        self.assertEqual((os.stat(path).st_ino, self.bytes_of(path)), (inode, data))

    def test_force_applies_a_change_on_youtube(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        source = FakeSource({ID: youtube_info(title="Renamed")})
        outcome = self.retagger(source, force=True).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual([c.atom for c in outcome.changes], ["©nam"])
        self.assertEqual(texts(path)["©nam"], ("Renamed",))

    def test_a_dry_run_changes_nothing(self):
        path = old_file(self.path())
        before, mtime = self.bytes_of(path), os.stat(path).st_mtime_ns
        outcome = self.retagger(FakeSource({ID: youtube_info()}), dry_run=True).process(
            path
        )
        self.assertEqual(outcome.status, Status.WOULD_UPDATE)
        atoms = {c.atom for c in outcome.changes}
        self.assertTrue({"tven", "©day", "stik", "©cmt", "©too"} <= atoms)
        self.assertEqual(self.bytes_of(path), before)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)
        self.assertEqual(self.leftovers(), [])

    def test_the_height_is_that_of_the_file(self):
        path = old_file(self.path())
        info = youtube_info(height=2160, formats=[{"ext": "mp4", "height": 2160}])
        _ = self.retagger(FakeSource({ID: info})).process(path)
        self.assertEqual(texts(path)["hdvd"], ("0",))  # the file is 180 high

    def test_without_ffprobe_the_resolution_class_is_kept(self):
        path = old_file(self.path(), extra={"hdvd": [2]})
        source = FakeSource({ID: youtube_info(height=2160)})
        outcome = self.retagger(source, probe=lambda p: None).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(texts(path)["hdvd"], ("2",))

    def test_a_probed_height_sets_the_class(self):
        path = old_file(self.path())
        streams = probe_file(path).streams  # pyright: ignore[reportOptionalMemberAccess]
        _ = self.retagger(
            FakeSource({ID: youtube_info()}), probe=lambda p: Probe(2160, streams)
        ).process(path)
        self.assertEqual(texts(path)["hdvd"], ("3",))

    def test_the_explicit_rating(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info(age_limit=18)})).process(path)
        self.assertEqual(texts(path)["rtng"], ("1",))
        path = old_file(self.path("abcdefghijk.mp4"), extra={"rtng": [1]})
        _ = self.retagger(FakeSource({OTHER: youtube_info(OTHER)})).process(path)
        self.assertNotIn("rtng", texts(path))  # not explicit anymore

    def season_of(self, path: str) -> tuple[str, ...]:
        return texts(path)["tvsn"]

    def set_season(self, path: str, season: int) -> None:
        mp4 = MP4(path)
        assert mp4.tags is not None
        mp4.tags["tvsn"] = [season]
        mp4.save()

    def test_any_old_season_becomes_one(self):
        for season in (0, 2, 7, 2024):
            with self.subTest(season=season):
                path = old_file(self.path())
                self.set_season(path, season)
                outcome = self.retagger(FakeSource({ID: youtube_info()})).process(path)
                self.assertEqual(outcome.status, Status.UPDATED)
                self.assertEqual(self.season_of(path), ("1",))

    def test_a_season_of_the_video_wins(self):
        path = old_file(self.path())
        info = youtube_info(season_number=3)
        _ = self.retagger(FakeSource({ID: info})).process(path)
        self.assertEqual(self.season_of(path), ("3",))

    def test_a_season_of_zero_from_youtube_is_not_used(self):
        path = old_file(self.path())
        info = youtube_info(season_number=0)
        _ = self.retagger(FakeSource({ID: info})).process(path)
        self.assertEqual(self.season_of(path), ("1",))

    def test_a_file_that_is_current_except_for_its_season_is_fixed(self):
        # What an earlier run of this tool left: all the new tags, the old season
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.set_season(path, 2)
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual({c.atom for c in outcome.changes}, {"tvsn"})
        self.assertEqual(self.season_of(path), ("1",))
        self.assertEqual(source.requests, [ID])

    def test_the_season_change_is_shown_by_a_dry_run(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.set_season(path, 2)
        before = self.bytes_of(path)
        outcome = self.retagger(FakeSource({ID: youtube_info()}), dry_run=True).process(
            path
        )
        self.assertEqual(outcome.status, Status.WOULD_UPDATE)
        self.assertEqual({c.atom for c in outcome.changes}, {"tvsn"})
        self.assertEqual(self.bytes_of(path), before)
        self.assertEqual(self.season_of(path), ("2",))

    def test_a_corrected_season_is_current_afterwards(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.set_season(path, 2)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.CURRENT)
        self.assertEqual(source.requests, [])

    def test_the_season_defaults_to_one(self):
        path = old_file(self.path())
        mp4 = MP4(path)
        assert mp4.tags is not None
        del mp4.tags["tvsn"]
        mp4.save()
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(texts(path)["tvsn"], ("1",))

    def test_the_channel_description_is_asked_once_per_channel(self):
        first = old_file(self.path(f"{ID}.mp4"))
        second = old_file(self.path(f"{OTHER}.mp4"))
        source = FakeSource({ID: youtube_info(), OTHER: youtube_info(OTHER)})
        retagger = self.retagger(source)
        _ = retagger.process(first)
        _ = retagger.process(second)
        self.assertEqual(source.channel_requests, ["UC123"])
        self.assertEqual(texts(second)["sdes"], ("About the channel",))

    def test_a_failing_channel_request_keeps_the_description(self):
        path = old_file(self.path(), extra={"sdes": ["Old about"]})
        source = FakeSource(
            {ID: youtube_info()},
            {"UC123": failure("Unable to download webpage: timed out")},
        )
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(texts(path)["sdes"], ("Old about",))

    def test_no_channel_description_when_not_wanted(self):
        path = old_file(self.path(), extra={"sdes": ["Old about"]})
        source = FakeSource({ID: youtube_info()})
        _ = self.retagger(source, channel_descriptions=False).process(path)
        self.assertEqual(source.channel_requests, [])
        self.assertEqual(texts(path)["sdes"], ("Old about",))

    def test_the_m4v_extension_and_upper_case(self):
        for name in (f"{ID}.m4v", f"{OTHER}.MP4"):
            with self.subTest(name=name):
                path = old_file(self.path(name))
                video_id = name[:11]
                source = FakeSource({video_id: youtube_info(video_id)})
                self.assertEqual(
                    self.retagger(source).process(path).status, Status.UPDATED
                )

    def test_a_file_without_tags(self):
        path = remuxed(self.path())
        mp4 = MP4(path)
        mp4.delete()
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(texts(path)["tven"], (ID,))


@needs_ffmpeg
class TestEpisodes(RetagTestCase):
    """The first video of a channel is episode 1, the next 2, and so on."""

    IDS: ClassVar[list[str]] = [
        f"vid{n:08}" for n in range(1, 6)
    ]  # in order of publication

    def channel(self) -> list[str]:
        return list(reversed(self.IDS))  # YouTube lists the newest first

    def make(self, *ids: str, **extra: Any) -> dict[str, str]:
        paths: dict[str, str] = {}
        for video_id in ids:
            paths[video_id] = old_file(self.path(f"{video_id}.mp4"), extra=extra)
        return paths

    def source(self, **kwargs: Any) -> FakeSource:
        videos = {i: youtube_info(i) for i in self.IDS}
        return FakeSource(videos, uploads={"UC123": self.channel()}, **kwargs)

    def test_the_numbers_follow_the_order_of_publication(self):
        paths = self.make(*self.IDS)
        summary = self.retagger(self.source()).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 5)
        numbers = {i: texts(p)["tves"] for i, p in paths.items()}
        self.assertEqual(
            numbers,
            {
                "vid00000001": ("1",),
                "vid00000002": ("2",),
                "vid00000003": ("3",),
                "vid00000004": ("4",),
                "vid00000005": ("5",),
            },
        )

    def test_the_number_does_not_depend_on_the_files_that_are_there(self):
        # Only the third and the last video are in the folder
        paths = self.make(self.IDS[2], self.IDS[4])
        _ = self.retagger(self.source()).run(self.folder)
        self.assertEqual(texts(paths[self.IDS[2]])["tves"], ("3",))
        self.assertEqual(texts(paths[self.IDS[4]])["tves"], ("5",))

    def test_the_old_number_is_replaced(self):
        path = old_file(self.path(f"{self.IDS[0]}.mp4"))  # it has tves 4
        self.assertEqual(texts(path)["tves"], ("4",))
        _ = self.retagger(self.source()).process(path)
        self.assertEqual(texts(path)["tves"], ("1",))
        self.assertEqual(texts(path)[PREFIX + "EPISODE_ORDER"], ("uploads",))

    def test_the_list_is_fetched_once_for_each_channel(self):
        self.make(*self.IDS)
        source = self.source()
        _ = self.retagger(source).run(self.folder)
        self.assertEqual(source.upload_requests, ["UC123"])
        self.assertEqual(sum("listing the uploads" in line for line in self.output), 1)
        self.assertTrue(any("5 videos" in line for line in self.output))

    def test_a_file_that_has_the_number_already_is_not_asked_again(self):
        self.make(*self.IDS[:2])
        _ = self.retagger(self.source()).run(self.folder)
        source = self.source()
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(summary.counts[Status.CURRENT], 2)
        self.assertEqual((source.requests, source.upload_requests), ([], []))

    def test_files_with_the_old_numbering_are_found_again(self):
        # In the current format, but numbered by the position in a tab: no
        # EPISODE_ORDER, as the number was counted differently
        paths = self.make(*self.IDS[:2])
        _ = self.retagger(self.source()).run(self.folder)
        for path in paths.values():
            mp4 = MP4(path)
            assert mp4.tags is not None
            del mp4.tags[PREFIX + "EPISODE_ORDER"]
            mp4.tags["tves"] = [77]
            mp4.save()
        summary = self.retagger(self.source()).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 2)
        self.assertEqual(texts(paths[self.IDS[0]])["tves"], ("1",))
        self.assertEqual(texts(paths[self.IDS[1]])["tves"], ("2",))

    def test_a_video_that_is_not_in_the_list_keeps_its_number(self):
        # E.g. for members only
        path = old_file(self.path(f"{ID}.mp4"))
        source = FakeSource({ID: youtube_info()}, uploads={"UC123": self.channel()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        after = texts(path)
        self.assertEqual(after["tves"], ("4",))
        self.assertNotIn(PREFIX + "EPISODE_ORDER", after)  # the number is not counted

    def test_no_number_is_made_up_for_a_video_not_in_the_list(self):
        path = remuxed(self.path(f"{ID}.mp4"))
        source = FakeSource({ID: youtube_info()}, uploads={"UC123": self.channel()})
        _ = self.retagger(source).process(path)
        self.assertNotIn("tves", texts(path))

    def test_without_a_list_the_number_is_kept(self):
        path = old_file(self.path(f"{ID}.mp4"))
        source = FakeSource({ID: youtube_info()}, uploads={"UC123": None})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        self.assertEqual(texts(path)["tves"], ("4",))
        self.assertEqual(source.upload_requests, [])

    def test_a_list_that_fails_keeps_the_number_and_tags_the_rest(self):
        path = old_file(self.path(f"{ID}.mp4"))
        error = failure("Unable to download webpage: timed out")
        source = FakeSource({ID: youtube_info()}, uploads={"UC123": error})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED)
        after = texts(path)
        self.assertEqual(after["tves"], ("4",))
        self.assertEqual(after["tven"], (ID,))
        self.assertTrue(any("could not be listed" in line for line in self.output))

    def test_a_failed_list_is_not_asked_again_for_the_channel(self):
        self.make(*self.IDS[:3])
        error = failure("Unable to download webpage: timed out")
        source = FakeSource(
            {i: youtube_info(i) for i in self.IDS}, uploads={"UC123": error}
        )
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 3)
        self.assertEqual(source.upload_requests, ["UC123"])

    def test_a_bot_check_while_listing_stops_the_run(self):
        self.make(*self.IDS[:2])
        error = failure("Sign in to confirm you’re not a bot")
        source = FakeSource(
            {i: youtube_info(i) for i in self.IDS}, uploads={"UC123": error}
        )
        summary = self.retagger(source).run(self.folder)
        self.assertIn("restricts the requests", summary.aborted or "")
        self.assertEqual(summary.counts[Status.UPDATED], 0)
        for video_id in self.IDS[:2]:
            self.assertEqual(
                texts(self.path(f"{video_id}.mp4"))["tven"], ("Old title",)
            )

    def test_a_number_counted_over_a_playlist_is_kept(self):
        path = old_file(
            self.path(f"{self.IDS[0]}.mp4"),
            extra={
                "tves": [9],
                PREFIX + "EPISODE_ORDER": [MP4FreeForm(PLAYLIST.encode())],
            },
        )
        source = self.source()
        _ = self.retagger(source).process(path)
        after = texts(path)
        self.assertEqual(after["tves"], ("9",))
        self.assertEqual(after[PREFIX + "EPISODE_ORDER"], (PLAYLIST,))
        self.assertEqual(source.upload_requests, [])  # not even asked

    def test_separate_channels_are_numbered_separately(self):
        first = old_file(self.path(f"{ID}.mp4"))
        second = old_file(self.path(f"{OTHER}.mp4"))
        videos = {
            ID: youtube_info(ID),
            OTHER: youtube_info(OTHER, channel_id="UC999", channel="Another"),
        }
        source = FakeSource(
            videos,
            {"UC123": "One", "UC999": "Two"},
            {"UC123": [NEWER, ID], "UC999": [OTHER, OLDER, "third000001"]},
        )
        retagger = self.retagger(source)
        _ = retagger.process(first)
        _ = retagger.process(second)
        self.assertEqual(texts(first)["tves"], ("1",))
        self.assertEqual(texts(second)["tves"], ("3",))
        self.assertEqual(source.upload_requests, ["UC123", "UC999"])

    def test_a_dry_run_shows_the_number_and_writes_nothing(self):
        path = old_file(self.path(f"{self.IDS[1]}.mp4"))
        before = self.bytes_of(path)
        outcome = self.retagger(self.source(), dry_run=True).process(path)
        changes = {c.atom: (c.old, c.new) for c in outcome.changes}
        self.assertEqual(changes["tves"], ("4", "2"))
        self.assertEqual(changes[PREFIX + "EPISODE_ORDER"], (None, "uploads"))
        self.assertEqual(self.bytes_of(path), before)

    def test_the_tab_a_video_is_in_does_not_matter(self):
        # Shorts, streams and videos are all in the uploads, one sequence
        shorts = "short000001"
        uploads = [shorts, self.IDS[0], "stream00001", OLDER]
        videos = {
            shorts: youtube_info(shorts),
            self.IDS[0]: youtube_info(self.IDS[0]),
            "stream00001": youtube_info("stream00001"),
        }
        paths = {v: old_file(self.path(f"{v}.mp4")) for v in videos}
        retagger = self.retagger(FakeSource(videos, uploads={"UC123": uploads}))
        for path in paths.values():
            _ = retagger.process(path)
        self.assertEqual(
            {v: texts(p)["tves"] for v, p in paths.items()},
            {shorts: ("4",), self.IDS[0]: ("3",), "stream00001": ("2",)},
        )


@needs_ffmpeg
class TestSafety(RetagTestCase):
    def test_not_a_video_is_skipped(self):
        path = self.path()
        with open(path, "wb") as f:
            _ = f.write(b"this is not an mp4 file at all")
        source = FakeSource({ID: youtube_info()})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertEqual(source.requests, [])
        self.assertEqual(self.bytes_of(path), b"this is not an mp4 file at all")

    def test_a_symbolic_link_is_skipped(self):
        target = old_file(os.path.join(self.temp_dir(), f"{ID}.mp4"))
        link = self.path()
        os.symlink(target, link)
        before = self.bytes_of(target)
        outcome = self.retagger(FakeSource({ID: youtube_info()})).process(link)
        self.assertEqual(outcome.status, Status.SKIPPED)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(self.bytes_of(target), before)

    def test_a_failed_copy_leaves_the_file_and_no_temporary(self):
        path = old_file(self.path())
        before = self.bytes_of(path)

        def failing(source: str, target: str) -> None:
            with open(target, "wb") as f:
                _ = f.write(b"half")
            raise OSError("disk full")

        retagger = self.retagger(
            FakeSource({ID: youtube_info()}), copy=failing, safe=True
        )
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("disk full", outcome.message)
        self.assertEqual(self.bytes_of(path), before)
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_check_leaves_the_file_and_no_temporary(self):
        path = old_file(self.path())
        before, mtime = self.bytes_of(path), os.stat(path).st_mtime_ns
        with mock.patch.object(
            Retagger,
            "_Retagger__check",
            side_effect=RuntimeError("the tag was not written"),
        ):
            outcome = self.retagger(
                FakeSource({ID: youtube_info()}), safe=True
            ).process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertEqual(self.bytes_of(path), before)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)
        self.assertEqual(self.leftovers(), [])

    def test_a_file_that_changes_meanwhile_is_not_replaced(self):
        path = old_file(self.path())

        def copy_and_touch(source: str, target: str) -> None:
            clone(source, target)
            with open(source, "ab") as f:  # e.g. a download that is still going
                _ = f.write(b"more")

        retagger = self.retagger(
            FakeSource({ID: youtube_info()}), copy=copy_and_touch, safe=True
        )
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("changed while", outcome.message)
        self.assertTrue(self.bytes_of(path).endswith(b"more"))
        self.assertEqual(texts(path)["tven"], ("Old title",))
        self.assertEqual(self.leftovers(), [])

    def test_a_check_finds_changed_streams(self):
        path = old_file(self.path())
        streams = probe_file(path).streams  # pyright: ignore[reportOptionalMemberAccess]
        answers = iter([Probe(180, streams), Probe(180, streams[:1])])
        retagger = self.retagger(
            FakeSource({ID: youtube_info()}), probe=lambda p: next(answers), safe=True
        )
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("streams", outcome.message)
        self.assertEqual(texts(path)["tven"], ("Old title",))

    def test_changed_streams_are_reported_when_writing_in_place(self):
        path = old_file(self.path())
        streams = probe_file(path).streams  # pyright: ignore[reportOptionalMemberAccess]
        answers = iter([Probe(180, streams), Probe(180, streams[:1])])
        retagger = self.retagger(
            FakeSource({ID: youtube_info()}), probe=lambda p: next(answers)
        )
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("may be damaged", outcome.message)
        self.assertIn("streams", outcome.message)
        self.assertEqual(self.leftovers(), [])

    def test_the_check_finds_every_kind_of_difference(self):
        path = old_file(self.path())
        tags = tags_of(path)
        target = Target({"©nam": ["New"]}, frozenset({"©cmt"}))
        retagger = self.retagger(FakeSource())
        check = retagger._Retagger__check  # pyright: ignore[reportAttributeAccessIssue]
        length = MP4(path).info.length
        with self.assertRaisesRegex(RuntimeError, "©nam was not written"):
            check(path, tags, target, None, length)
        target = Target({}, frozenset({"©too"}))
        with self.assertRaisesRegex(RuntimeError, "©too was not removed"):
            check(path, tags, target, None, length)
        with self.assertRaisesRegex(RuntimeError, "was changed"):
            check(
                path, {**tags, "©wrt": ["Other"]}, Target({}, frozenset()), None, length
            )
        with self.assertRaisesRegex(RuntimeError, "appeared"):
            check(path, {}, Target({}, frozenset()), None, length)
        with self.assertRaisesRegex(RuntimeError, "duration"):
            check(path, tags, Target({}, frozenset()), None, length + 5)
        open(path, "wb").close()
        with self.assertRaisesRegex(RuntimeError, "cannot be read"):
            check(path, tags, Target({}, frozenset()), None, length)

    def test_an_unexpected_error_only_fails_that_file(self):
        path = old_file(self.path())
        source = FakeSource({ID: RuntimeError("boom")})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("RuntimeError: boom", outcome.message)

    def test_directories_and_permissions_of_the_file_stay(self):
        path = old_file(self.path())
        os.chmod(path, 0o600)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.folder)), [f"{ID}.mp4"])

    def test_a_read_only_folder_fails_without_a_change(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        path = old_file(self.path())
        before = self.bytes_of(path)
        os.chmod(self.folder, 0o500)
        self.addCleanup(os.chmod, self.folder, 0o700)
        outcome = self.retagger(FakeSource({ID: youtube_info()}), safe=True).process(
            path
        )
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertEqual(self.bytes_of(path), before)

    def test_a_read_only_folder_is_no_problem_in_place(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        path = old_file(self.path())
        os.chmod(self.folder, 0o500)
        self.addCleanup(os.chmod, self.folder, 0o700)
        outcome = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(outcome.status, Status.UPDATED, outcome.message)
        self.assertEqual(texts(path)["tven"], (ID,))


@needs_ffmpeg
class TestInPlace(RetagTestCase):
    """The default: the tags are written to the file itself, without a copy."""

    def no_copy(self, source: str, target: str) -> None:
        raise AssertionError("a copy was made")

    def test_no_copy_is_made(self):
        path = old_file(self.path())
        retagger = self.retagger(FakeSource({ID: youtube_info()}), copy=self.no_copy)
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.UPDATED, outcome.message)
        self.assertEqual(texts(path)["tven"], (ID,))

    def test_a_copy_is_made_with_safe(self):
        path = old_file(self.path())
        retagger = self.retagger(
            FakeSource({ID: youtube_info()}), copy=self.no_copy, safe=True
        )
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("a copy was made", outcome.message)
        self.assertEqual(texts(path)["tven"], ("Old title",))

    def test_the_file_stays_the_same_file(self):
        path = old_file(self.path())
        before = os.stat(path)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        after = os.stat(path)
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after.st_atime_ns, before.st_atime_ns)
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(sorted(os.listdir(self.folder)), [f"{ID}.mp4"])

    def test_a_hard_link_sees_the_new_tags(self):
        path = old_file(self.path())
        link = os.path.join(self.temp_dir(), "link.mp4")
        os.link(path, link)
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(texts(link)["tven"], (ID,))

    def test_the_result_is_the_same_as_with_a_copy(self):
        fast = old_file(self.path(f"{ID}.mp4"))
        other = os.path.join(self.temp_dir(), f"{ID}.mp4")
        _ = old_file(other)
        source = FakeSource({ID: youtube_info()})
        _ = self.retagger(source).process(fast)
        _ = self.retagger(source, safe=True).process(other)
        self.assertEqual(texts(fast), texts(other))
        self.assertEqual(probe_file(fast), probe_file(other))

    def test_the_media_data_is_untouched(self):
        path = old_file(self.path())
        before = probe_file(path)
        packets = subprocess.run(
            [
                cast(str, FFMPEG),
                *("-v", "error", "-i", path),
                *("-map", "0:a", "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        again = subprocess.run(
            [
                cast(str, FFMPEG),
                *("-v", "error", "-i", path),
                *("-map", "0:a", "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(again, packets)
        self.assertEqual(probe_file(path), before)

    def test_a_file_that_changed_before_the_write_is_left_alone(self):
        path = old_file(self.path())

        def touch(file: str) -> None:
            # After the file was read, e.g. a download that is still going
            with open(path, "ab") as f:
                _ = f.write(b"more")

        retagger = self.retagger(FakeSource({ID: youtube_info()}), probe=touch)
        outcome = retagger.process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("changed while", outcome.message)
        self.assertNotIn("damaged", outcome.message)
        self.assertTrue(self.bytes_of(path).endswith(b"more"))
        self.assertEqual(texts(path)["tven"], ("Old title",))

    def test_a_failure_before_the_write_is_no_damage(self):
        path = old_file(self.path())
        before = self.bytes_of(path)
        with mock.patch.object(
            Retagger, "_Retagger__apply", side_effect=RuntimeError("no")
        ):
            outcome = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertNotIn("damaged", outcome.message)
        self.assertEqual(self.bytes_of(path), before)

    def test_a_failed_check_says_the_file_may_be_damaged(self):
        path = old_file(self.path())
        mtime = os.stat(path).st_mtime_ns
        with mock.patch.object(
            Retagger,
            "_Retagger__check",
            side_effect=RuntimeError("the tag was not written"),
        ):
            outcome = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn(
            "the file may be damaged: the tag was not written", outcome.message
        )
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)  # still restored

    def test_a_failed_write_says_the_file_may_be_damaged(self):
        path = old_file(self.path())
        mtime = os.stat(path).st_mtime_ns
        with mock.patch.object(MP4, "save", side_effect=OSError("disk full")):
            outcome = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertIn("may be damaged", outcome.message)
        self.assertIn("disk full", outcome.message)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_a_second_run_finds_the_file_current(self):
        path = old_file(self.path())
        _ = self.retagger(FakeSource({ID: youtube_info()})).process(path)
        source = FakeSource({ID: youtube_info()})
        self.assertEqual(self.retagger(source).process(path).status, Status.CURRENT)
        self.assertEqual(source.requests, [])

    def test_an_interrupt_waits_for_the_write(self):
        path = old_file(self.path())
        original = MP4.save
        previous = signal.getsignal(signal.SIGINT)

        def interrupted(mp4: MP4, *args: Any, **kwargs: Any) -> Any:
            os.kill(os.getpid(), signal.SIGINT)
            return original(mp4, *args, **kwargs)  # pyright: ignore

        retagger = self.retagger(FakeSource({ID: youtube_info()}))
        with (
            mock.patch.object(MP4, "save", interrupted),
            self.assertRaises(KeyboardInterrupt),
        ):
            _ = retagger.process(path)
        # The write was done, and the handler of Ctrl-C is back
        self.assertEqual(texts(path)["tven"], (ID,))
        self.assertEqual(signal.getsignal(signal.SIGINT), previous)
        self.assertEqual(self.leftovers(), [])

    def test_the_interrupt_handler_is_restored_after_a_failure(self):
        previous = signal.getsignal(signal.SIGINT)
        with (
            self.assertRaises(ValueError),
            retag._interrupt_after(),  # pyright: ignore[reportPrivateUsage]
        ):
            raise ValueError
        self.assertEqual(signal.getsignal(signal.SIGINT), previous)

    def test_no_signals_outside_the_main_thread(self):
        done: list[str] = []

        def work() -> None:
            with retag._interrupt_after():  # pyright: ignore[reportPrivateUsage]
                done.append("ran")

        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertEqual(done, ["ran"])


@needs_ffmpeg
class TestRun(RetagTestCase):
    def make(self, *names: str) -> None:
        for name in names:
            _ = old_file(self.path(name))

    def source(self, *ids: str) -> FakeSource:
        return FakeSource({video_id: youtube_info(video_id) for video_id in ids})

    def test_all_files_are_done_with_a_pause_between_requests(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        summary = self.retagger(self.source(ID, OTHER)).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 2)
        self.assertIsNone(summary.aborted)
        # Before each request but the first: the channel and its uploads (once
        # for both files), and the second video
        self.assertEqual(self.pauses, [2.5, 2.5, 2.5])
        lines = [line for line in self.output if line.startswith("[")]
        self.assertEqual(len(lines), 2)
        self.assertIn("UPDATED", lines[0])
        self.assertTrue(lines[0].startswith("[1/2]"))

    def test_no_pause_for_files_that_need_no_request(self):
        self.make(f"{ID}.mp4", "holiday.mp4")
        _ = self.retagger(self.source(ID)).run(self.folder)
        # A file without an id makes no request, so there is no pause for it
        self.assertEqual(self.pauses, [2.5, 2.5])  # the channel and its uploads

    def slow_source(self, took: float, *ids: str) -> FakeSource:
        """A source whose answers take time on the clock of the retagger."""
        retag = self

        class Slow(FakeSource):
            def video(self, video_id: str) -> Mapping[str, object]:
                retag.now += took
                return super().video(video_id)

        return Slow({i: youtube_info(i) for i in ids})

    def test_the_time_a_request_took_counts_for_the_pause(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        source = self.slow_source(2.0, ID, OTHER)
        _ = self.retagger(source, sleep=3.0).run(self.folder)
        # Only what is left of the interval is waited: 1 s after a video that
        # took 2 s (before the channel), then the full interval before its
        # uploads and before the second video
        self.assertEqual(self.pauses, [1.0, 3.0, 3.0])

    def test_no_pause_when_the_interval_has_passed(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        source = self.slow_source(5.0, ID, OTHER)
        _ = self.retagger(source, sleep=3.0).run(self.folder)
        # The video took longer than the interval, so the channel follows at
        # once; only the uploads and the second video wait for the interval
        self.assertEqual(self.pauses, [3.0, 3.0])

    def test_the_work_on_a_file_counts_too(self):
        retag = self
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")

        def probe(_: str) -> None:
            retag.now += 10.0  # a long copy or probe

        source = self.source(ID, OTHER)
        _ = self.retagger(source, probe=probe).run(self.folder)
        # The second video starts more than an interval after the first
        self.assertEqual(sorted(source.requests), sorted([ID, OTHER]))
        self.assertEqual(self.pauses, [2.5, 2.5])  # the channel and its uploads

    def test_a_clock_that_goes_back_does_not_make_a_long_pause(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        retag = self

        class Back(FakeSource):
            def video(self, video_id: str) -> Mapping[str, object]:
                retag.now -= 500.0
                return super().video(video_id)

        source = Back({i: youtube_info(i) for i in (ID, OTHER)})
        _ = self.retagger(source, sleep=3.0).run(self.folder)
        self.assertTrue(self.pauses)
        self.assertTrue(all(p <= 3.0 for p in self.pauses))

    def test_the_first_request_is_not_delayed(self):
        self.make(f"{ID}.mp4")
        source = self.slow_source(0.0, ID)
        _ = self.retagger(source, sleep=3.0).run(self.folder)
        self.assertEqual(source.requests, [ID])
        self.assertEqual(self.pauses[:1], [3.0])  # before the channel, not the video
        self.assertEqual(self.now, 1000.0 + sum(self.pauses))

    def test_subfolders_and_no_recursive(self):
        self.make("top.mp4")
        os.makedirs(self.path("sub"))
        _ = old_file(self.path(f"sub/{ID}.mp4"))
        source = self.source(ID)
        summary = self.retagger(source).run(self.folder, recursive=False)
        self.assertEqual(summary.counts[Status.SKIPPED], 1)  # top.mp4, no id
        self.assertEqual(source.requests, [])
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 1)

    def test_a_dry_run_lists_the_changes(self):
        self.make(f"{ID}.mp4")
        summary = self.retagger(self.source(ID), dry_run=True).run(self.folder)
        self.assertEqual(summary.counts[Status.WOULD_UPDATE], 1)
        text = "\n".join(self.output)
        self.assertIn("WOULD UPDATE", text)
        self.assertIn("tven: 'Old title' -> 'dQw4w9WgXcQ'", text)

    def test_the_changes_are_shown_when_verbose(self):
        self.make(f"{ID}.mp4")
        _ = self.retagger(self.source(ID), verbose=True).run(self.folder)
        self.assertTrue(any("tven:" in line for line in self.output))
        self.output.clear()
        _ = self.retagger(self.source(ID)).run(self.folder)  # now current
        self.assertFalse(any("tven:" in line for line in self.output))

    def test_a_bot_check_stops_the_run_at_once(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        source = FakeSource(
            {
                OTHER: failure("Sign in to confirm you’re not a bot"),
                ID: youtube_info(ID),
            }
        )
        summary = self.retagger(source).run(self.folder)
        self.assertIn("restricts the requests", summary.aborted or "")
        self.assertEqual(source.requests, [OTHER])  # the files are done in order
        self.assertEqual(texts(self.path(f"{ID}.mp4"))["tven"], ("Old title",))

    def test_a_rate_limit_stops_the_run(self):
        self.make(f"{ID}.mp4")
        source = FakeSource({ID: failure("HTTP Error 429: Too Many Requests")})
        summary = self.retagger(source).run(self.folder)
        self.assertIn("restricts", summary.aborted or "")

    def test_repeated_restrictions_stop_the_run(self):
        ids = [f"vid{n:08}" for n in range(8)]
        self.make(*[f"{i}.mp4" for i in ids])
        source = FakeSource({i: failure("Video unavailable") for i in ids})
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(len(source.requests), Retagger.MAX_RESTRICTED)
        self.assertIn("in a row", summary.aborted or "")
        self.assertEqual(summary.counts[Status.SKIPPED], Retagger.MAX_RESTRICTED - 1)

    def test_a_good_answer_resets_the_count_of_restrictions(self):
        ids = [f"vid{n:08}" for n in range(9)]
        self.make(*[f"{i}.mp4" for i in ids])
        videos: dict[str, object] = {i: failure("Video unavailable") for i in ids}
        videos[ids[3]] = youtube_info(ids[3])
        videos[ids[7]] = youtube_info(ids[7])
        source = FakeSource(videos)
        summary = self.retagger(source).run(self.folder)
        self.assertIsNone(summary.aborted)
        self.assertEqual(summary.counts[Status.UPDATED], 2)

    def test_deleted_and_private_videos_do_not_stop_the_run(self):
        ids = [f"vid{n:08}" for n in range(8)]
        self.make(*[f"{i}.mp4" for i in ids])
        source = FakeSource({i: failure("Private video") for i in ids})
        summary = self.retagger(source).run(self.folder)
        self.assertIsNone(summary.aborted)
        self.assertEqual(summary.counts[Status.SKIPPED], 8)

    def test_a_network_that_is_down_stops_the_run(self):
        ids = [f"vid{n:08}" for n in range(6)]
        self.make(*[f"{i}.mp4" for i in ids])
        error = failure("Unable to download webpage: timed out")
        source = FakeSource({i: error for i in ids})
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(len(source.requests), Retagger.MAX_NETWORK)
        self.assertIn("network", summary.aborted or "")

    def test_the_limit_counts_files_asked_about(self):
        ids = [f"vid{n:08}" for n in range(5)]
        self.make(*[f"{i}.mp4" for i in ids])
        source = FakeSource({i: youtube_info(i) for i in ids})
        summary = self.retagger(source, limit=2).run(self.folder)
        self.assertEqual(summary.counts[Status.UPDATED], 2)
        self.assertTrue(summary.limited)
        self.assertEqual(len(source.requests), 2)

    def test_current_files_do_not_count_for_the_limit(self):
        ids = [f"vid{n:08}" for n in range(4)]
        self.make(*[f"{i}.mp4" for i in ids])
        source = FakeSource({i: youtube_info(i) for i in ids})
        _ = self.retagger(source, limit=1).run(self.folder)  # the first is done
        source = FakeSource({i: youtube_info(i) for i in ids})
        summary = self.retagger(source, limit=1).run(self.folder)
        self.assertEqual(summary.counts[Status.CURRENT], 1)
        self.assertEqual(summary.counts[Status.UPDATED], 1)
        self.assertEqual(len(source.requests), 1)

    def test_an_interrupted_run_can_be_repeated(self):
        ids = [f"vid{n:08}" for n in range(4)]
        self.make(*[f"{i}.mp4" for i in ids])
        videos: dict[str, object] = {i: youtube_info(i) for i in ids}
        videos[ids[2]] = failure("HTTP Error 429: Too Many Requests")
        first = self.retagger(FakeSource(videos)).run(self.folder)
        self.assertIsNotNone(first.aborted)
        second_source = FakeSource({i: youtube_info(i) for i in ids})
        second = self.retagger(second_source).run(self.folder)
        self.assertEqual(second.counts[Status.CURRENT], 2)
        self.assertEqual(second.counts[Status.UPDATED], 2)
        self.assertEqual(second_source.requests, ids[2:])

    def test_a_failing_file_does_not_stop_the_others(self):
        self.make(f"{ID}.mp4", f"{OTHER}.mp4")
        source = FakeSource({ID: RuntimeError("boom"), OTHER: youtube_info(OTHER)})
        summary = self.retagger(source).run(self.folder)
        self.assertEqual(summary.counts[Status.FAILED], 1)
        self.assertEqual(summary.counts[Status.UPDATED], 1)

    def test_an_empty_folder(self):
        summary = self.retagger(FakeSource()).run(self.folder)
        self.assertEqual(sum(summary.counts.values()), 0)

    def test_aborting_raises_from_the_file_only(self):
        path = old_file(self.path())
        source = FakeSource({ID: failure("Sign in to confirm you’re not a bot")})
        with self.assertRaises(Aborted):
            _ = self.retagger(source).process(path)


@needs_ffmpeg
class TestSameAsNewDownloads(RetagTestCase):
    """The tags of a retagged file are those of a new download: the video goes
    through the real post-processors of a download (ffmpeg and mutagen) on one
    copy and through the retagger on another."""

    def download_tags(
        self, info: dict[str, object], context: MetadataContext
    ) -> dict[str, tuple[str, ...]]:
        path = remuxed(os.path.join(self.temp_dir(), "download.mp4"))
        video = dict(info)
        video.update(filepath=path, ext="mp4", __real_download=True)
        video.update(metadata_updates(video, context))
        ydl = YoutubeDL({"quiet": True, "logger": mock.Mock()})
        self.addCleanup(ydl.close)
        _ = cast(Any, new_file_metadata_pp(ydl)).run(video)
        _ = cast(Any, new_file_atom_pp(ydl)).run(video)
        return texts(path)

    def retag_tags(
        self,
        info: dict[str, object],
        episode: int,
        group: str,
        season: int | None = None,
    ) -> dict[str, tuple[str, ...]]:
        path = self.path()
        extra: dict[str, Any] = {"tves": [99], "©grp": [group]}
        old_file(path, extra=extra)
        mp4 = MP4(path)
        assert mp4.tags is not None
        for atom in ("covr", "©wrt", "tvsn"):
            mp4.tags.pop(atom, None)
        if season is not None:
            mp4.tags["tvsn"] = [season]
        mp4.save()
        source = FakeSource({ID: info}, {"UC123": "About the channel"})
        outcome = self.retagger(source).process(path)
        self.assertEqual(outcome.status, Status.UPDATED, outcome.message)
        return texts(path)

    def compare(self, info: dict[str, object], adult: bool = False) -> None:
        context = MetadataContext(
            episode_number=2,  # the position of ID in UPLOADS_OF_CHANNEL
            episode_order=UPLOADS,
            group="Shorts",
            channel_description="About the channel",
        )
        expected = self.download_tags({**info, "height": 180}, context)
        actual = self.retag_tags(info, 4, "Shorts")
        self.assertEqual(sorted(actual), sorted(expected))
        for atom in expected:
            self.assertEqual(actual[atom], expected[atom], atom)

    def test_a_typical_video(self):
        self.compare(youtube_info())

    def test_a_video_for_adults(self):
        self.compare(youtube_info(age_limit=18))

    def test_a_video_without_description_and_categories(self):
        info = youtube_info()
        for key in ("description", "categories"):
            del info[key]
        self.compare(info)

    def test_unicode_and_long_texts(self):
        self.compare(
            youtube_info(
                title="Smått — ünïcode ♪ 日本語",
                description="日本語の説明 #日本 #テスト\n" + "é" * 600,
                channel="Kanal Ñ",
                uploader="Kanal Ñ",
            )
        )

    def test_a_video_without_a_timestamp(self):
        info = youtube_info()
        del info["timestamp"]
        self.compare(info)

    def test_genres_and_tags_as_yt_dlp_maps_them(self):
        info = youtube_info(categories=None, genres=["Music"])
        self.compare(info)
        info = youtube_info(categories=[], tags=["x", "y"])
        self.compare(info)


class TestYoutubeSource(unittest.TestCase):
    class FakeYdl:
        def __init__(self, result: object) -> None:
            self.result = result
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.closed = False

        def extract_info(self, url: str, **kwargs: object) -> object:
            self.calls.append((url, dict(kwargs)))
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result

        def close(self) -> None:
            self.closed = True

    def source(
        self, result: object, browser: str | None = None
    ) -> tuple[YoutubeSource, FakeYdl, dict[str, object]]:
        ydl = self.FakeYdl(result)
        settings: dict[str, object] = {}

        def factory(options: dict[str, object]) -> object:
            settings.update(options)
            return ydl

        return YoutubeSource(browser, factory=factory), ydl, settings

    def test_a_video_is_extracted_without_downloading(self):
        source, ydl, settings = self.source({"id": ID})
        self.assertEqual(source.video(ID), {"id": ID})
        ((url, kwargs),) = ydl.calls
        self.assertEqual(url, f"https://www.youtube.com/watch?v={ID}")
        self.assertEqual(kwargs, {"download": False, "process": False})
        self.assertTrue(settings["skip_download"])
        self.assertTrue(settings["quiet"])
        self.assertNotIn("cookiesfrombrowser", settings)

    def test_the_browser_cookies(self):
        _, _, settings = self.source({}, browser="firefox")
        self.assertEqual(settings["cookiesfrombrowser"], ("firefox", None, None, None))

    def test_errors_become_source_errors(self):
        from yt_dlp.utils import DownloadError

        source, _, _ = self.source(DownloadError("ERROR: [youtube] abc: Private video"))
        with self.assertRaises(SourceError) as caught:
            source.video(ID)
        self.assertIs(caught.exception.error.category, Error.Category.PRIVATE)

    def test_odd_answers(self):
        for result in (None, [], "text"):
            with self.subTest(result=result):
                source, _, _ = self.source(result)
                with self.assertRaises(SourceError):
                    source.video(ID)

    def test_the_channel_description(self):
        source, ydl, _ = self.source({"description": "  About \n"})
        info = {"channel_url": "https://www.youtube.com/channel/UC123"}
        self.assertEqual(source.channel_description(info), "About")
        self.assertEqual(ydl.calls[0][0], "https://www.youtube.com/channel/UC123")
        self.assertEqual(ydl.calls[0][1], {"download": False, "process": False})

    def test_no_channel_description(self):
        for result in (
            {},
            {"description": ""},
            {"description": "  "},
            {"description": 5},
        ):
            with self.subTest(result=result):
                source, _, _ = self.source(result)
                self.assertIsNone(
                    source.channel_description({"channel_url": "https://x/c"})
                )

    def test_no_request_without_a_channel_url(self):
        for info in (
            {},
            {"channel_url": None},
            {"channel_url": "ftp://x"},
            {"channel_url": "file:///x"},
        ):
            with self.subTest(info=info):
                source, ydl, _ = self.source({"description": "x"})
                self.assertIsNone(source.channel_description(info))
                self.assertEqual(ydl.calls, [])

    def test_close(self):
        source, ydl, _ = self.source({})
        source.close()
        self.assertTrue(ydl.closed)

    def test_real_yt_dlp_accepts_the_settings(self):
        source = YoutubeSource()
        source.close()


class TestMain(IsolatedTestCase):
    def run_main(
        self, *argv: str, source: FakeSource | None = None
    ) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        made: list[str | None] = []

        def make(browser: str | None) -> FakeSource:
            made.append(browser)
            return source or FakeSource()

        self.made = made
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = retag.main(list(argv), source=make)
        return status, out.getvalue(), err.getvalue()

    def test_wrong_arguments(self):
        folder = self.temp_dir()
        cases = (
            (["/does/not/exist"], "is not a folder"),
            ([folder, "--config", "/does/not/exist.ini"], "does not exist"),
            ([folder, "--sleep", "-1"], "must not be negative"),
            ([folder, "--tolerance", "-1"], "must not be negative"),
            ([folder, "--limit", "0"], "at least 1"),
        )
        for argv, text in cases:
            with self.subTest(argv=argv):
                status, _, errors = self.run_main(
                    *argv, "--cookies-from-browser", "none"
                )
                self.assertEqual(status, 2)
                self.assertIn(text, errors)

    def test_an_empty_folder(self):
        status, out, _ = self.run_main(
            self.temp_dir(), "--cookies-from-browser", "none"
        )
        self.assertEqual(status, 0)
        self.assertIn("no mp4 or m4v files found", out)

    def test_help_and_the_defaults(self):
        arguments = retag.parser().parse_args(["x"], namespace=Arguments())
        self.assertEqual(
            (
                arguments.dry_run,
                arguments.force,
                arguments.safe,
                arguments.sleep,
                arguments.tolerance,
                arguments.limit,
            ),
            (False, False, False, 3.0, 5.0, None),
        )
        arguments = retag.parser().parse_args(["x", "--safe"], namespace=Arguments())
        self.assertTrue(arguments.safe)

    @needs_ffmpeg
    def test_a_dry_run_end_to_end(self):
        folder = self.temp_dir()
        path = old_file(os.path.join(folder, f"{ID}.mp4"))
        with open(path, "rb") as f:
            before = f.read()
        source = FakeSource({ID: youtube_info()})
        status, out, _ = self.run_main(
            folder,
            "-n",
            "--sleep",
            "0",
            "--cookies-from-browser",
            "none",
            source=source,
        )
        self.assertEqual(status, 0)
        self.assertIn("dry run", out)
        self.assertIn("Done: 1 would update", out)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertTrue(source.closed)

    @needs_ffmpeg
    def test_a_real_run_end_to_end(self):
        folder = self.temp_dir()
        path = old_file(os.path.join(folder, f"{ID}.mp4"))
        source = FakeSource({ID: youtube_info()})
        status, out, _ = self.run_main(
            folder, "--sleep", "0", "--cookies-from-browser", "none", source=source
        )
        self.assertEqual(status, 0)
        self.assertIn("Done: 1 updated", out)
        self.assertEqual(texts(path)["tven"], (ID,))

    @needs_ffmpeg
    def test_a_failed_file_gives_status_1(self):
        folder = self.temp_dir()
        _ = old_file(os.path.join(folder, f"{ID}.mp4"))
        source = FakeSource({ID: RuntimeError("boom")})
        status, out, _ = self.run_main(
            folder, "--sleep", "0", "--cookies-from-browser", "none", source=source
        )
        self.assertEqual(status, 1)
        self.assertIn("1 failed", out)

    @needs_ffmpeg
    def test_a_stopped_run_gives_status_1(self):
        folder = self.temp_dir()
        _ = old_file(os.path.join(folder, f"{ID}.mp4"))
        source = FakeSource({ID: failure("HTTP Error 429: Too Many Requests")})
        status, _, errors = self.run_main(
            folder, "--sleep", "0", "--cookies-from-browser", "none", source=source
        )
        self.assertEqual(status, 1)
        self.assertIn("STOPPED", errors)

    def test_an_interrupt_is_reported(self):
        folder = self.temp_dir()
        with mock.patch.object(Retagger, "run", side_effect=KeyboardInterrupt):
            status, _, errors = self.run_main(folder, "--cookies-from-browser", "none")
        self.assertEqual(status, 130)
        self.assertIn("Interrupted", errors)

    def test_the_source_is_closed_after_an_error(self):
        source = FakeSource()
        with (
            mock.patch.object(Retagger, "run", side_effect=RuntimeError("x")),
            self.assertRaises(RuntimeError),
        ):
            _ = self.run_main(
                self.temp_dir(), "--cookies-from-browser", "none", source=source
            )
        self.assertTrue(source.closed)

    def test_the_limit_is_mentioned(self):
        with mock.patch.object(Retagger, "run") as run:
            run.return_value = retag.Summary(limited=True)
            _, out, _ = self.run_main(self.temp_dir(), "--cookies-from-browser", "none")
        self.assertIn("Stopped at the limit", out)


class TestCookieBrowser(IsolatedTestCase):
    def arguments(self, **values: Any) -> Arguments:
        arguments = Arguments()
        for key, value in values.items():
            setattr(arguments, key, value)
        return arguments

    def in_folder(self) -> str:
        folder = self.temp_dir()
        previous = os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, previous)
        return folder

    def test_the_option_wins(self):
        self.in_folder()
        self.assertEqual(
            cookie_browser(self.arguments(cookies_from_browser=" firefox ")), "firefox"
        )

    def test_none_switches_it_off(self):
        for value in ("none", "None", ""):
            with self.subTest(value=value):
                self.assertIsNone(
                    cookie_browser(self.arguments(cookies_from_browser=value))
                )

    def test_the_configuration_is_read(self):
        folder = self.in_folder()
        path = os.path.join(folder, "other.ini")
        with open(path, "w", encoding="utf-8") as f:
            _ = f.write('[General]\ncookie_browser = "chrome"\n')
        self.assertEqual(cookie_browser(self.arguments(config=path)), "chrome")

    def test_config_ini_in_the_folder_is_found(self):
        folder = self.in_folder()
        with open(os.path.join(folder, "config.ini"), "w", encoding="utf-8") as f:
            _ = f.write('[General]\ncookie_browser = "safari"\n')
        self.assertEqual(cookie_browser(self.arguments()), "safari")

    def test_no_configuration_means_no_cookies_and_writes_nothing(self):
        folder = self.in_folder()
        self.assertIsNone(cookie_browser(self.arguments()))
        self.assertEqual(os.listdir(folder), [])  # no default configuration is written

    def test_a_configuration_is_never_written(self):
        folder = self.in_folder()
        path = os.path.join(folder, "config.ini")
        with open(path, "w", encoding="utf-8") as f:
            _ = f.write("[General]\ncookie_browser = None\n# my comment\n")
        _ = cookie_browser(self.arguments())
        with open(path, encoding="utf-8") as f:
            self.assertIn("# my comment", f.read())


if __name__ == "__main__":
    unittest.main()
