# pyright: standard
import threading
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from vidl.episodes import (
    PLAYLIST,
    UPLOADS,
    EpisodeIndex,
    IncompleteList,
    complete_ids,
    entry_ids,
    ids_of,
    index_for,
    rank,
    uploads_url,
)

CHANNEL = "UCuAXFkgsw1L7xaCfnd5JJOw"
UPLOADS_ID = "UUuAXFkgsw1L7xaCfnd5JJOw"
IDS = [f"video{n:06}" for n in range(1, 6)]  # published in this order


def entries(ids: Sequence[str]) -> list[dict[str, str]]:
    return [{"id": i, "url": f"https://www.youtube.com/watch?v={i}"} for i in ids]


def playlist(
    playlist_id: str, ids: Sequence[str], key: str = "YoutubeTab", **extra: Any
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "_type": "playlist",
        "id": playlist_id,
        "extractor_key": key,
        "entries": entries(ids),
    }
    info.update(extra)
    return info


class Lister:
    """Gives the ids of a playlist address, and records the addresses."""

    def __init__(self, ids: Sequence[str] = ()) -> None:
        self.ids = list(ids)
        self.urls: list[str] = []

    def __call__(self, url: str) -> list[str]:
        self.urls.append(url)
        return list(self.ids)


class TestRank(unittest.TestCase):
    def test_the_first_video_is_1_and_the_next_2(self):
        newest_first = list(reversed(IDS))
        self.assertEqual([rank(newest_first, i, True) for i in IDS], [1, 2, 3, 4, 5])

    def test_in_the_order_of_the_list(self):
        self.assertEqual([rank(IDS, i, False) for i in IDS], [1, 2, 3, 4, 5])

    def test_not_in_the_list(self):
        self.assertIsNone(rank(IDS, "other", True))
        self.assertIsNone(rank([], "x", False))

    def test_a_single_video(self):
        self.assertEqual(rank(["a"], "a", True), 1)
        self.assertEqual(rank(["a"], "a", False), 1)

    def test_a_new_video_does_not_change_the_numbers(self):
        before = ["c", "b", "a"]  # a is the first
        after = ["d", *before]
        for video in before:
            self.assertEqual(rank(before, video, True), rank(after, video, True))

    def test_a_removed_video_moves_the_ones_after_it(self):
        before = ["c", "b", "a"]
        self.assertEqual(rank(before, "c", True), 3)
        self.assertEqual(rank(["c", "a"], "c", True), 2)


class TestUploadsUrl(unittest.TestCase):
    def test_a_channel(self):
        self.assertEqual(
            uploads_url(CHANNEL), f"https://www.youtube.com/playlist?list={UPLOADS_ID}"
        )

    def test_not_a_channel(self):
        for text in (
            "",
            "@handle",
            UPLOADS_ID,
            "UCshort",
            CHANNEL + "x",
            "PL" + CHANNEL[2:],
        ):
            with self.subTest(text=text):
                self.assertIsNone(uploads_url(text))


class TestEntries(unittest.TestCase):
    def test_ids_in_order_without_the_ones_missing(self):
        raw: list[object] = [
            {"id": "a"},
            {"url": "x"},
            None,
            "text",
            {"id": ""},
            {"id": 5},
            {"id": "b"},
        ]
        self.assertEqual(ids_of(raw), ["a", "b"])

    def test_generators_are_read_once(self):
        info = {"entries": (e for e in entries(IDS))}
        self.assertEqual(entry_ids(info), IDS)
        self.assertEqual(entry_ids(info), [])

    def test_odd_entries(self):
        for value in (None, "text", b"bytes", {"id": "a"}, 5):
            with self.subTest(value=value):
                self.assertEqual(entry_ids({"entries": value}), [])
        self.assertEqual(entry_ids({}), [])

    def test_a_complete_list(self):
        info = playlist("UU1", IDS, playlist_count=5)
        self.assertEqual(complete_ids(info), IDS)

    def test_an_unknown_count_is_taken_as_complete(self):
        self.assertEqual(complete_ids(playlist("UU1", IDS)), IDS)
        self.assertEqual(complete_ids(playlist("UU1", IDS, playlist_count=None)), IDS)

    def test_an_interrupted_list_is_refused(self):
        with self.assertRaises(IncompleteList) as caught:
            _ = complete_ids(playlist("UU1", IDS[:3], playlist_count=5))
        self.assertIn("3 of 5", str(caught.exception))

    def test_more_than_the_count_is_fine(self):
        # A video was added while the list was read
        self.assertEqual(complete_ids(playlist("UU1", IDS, playlist_count=4)), IDS)


class TestEpisodeIndex(unittest.TestCase):
    def test_numbers_and_the_order_name(self):
        index = EpisodeIndex(lambda: list(reversed(IDS)), True, UPLOADS)
        self.assertEqual(index.order, UPLOADS)
        self.assertEqual([index.number(i) for i in IDS], [1, 2, 3, 4, 5])
        self.assertIsNone(index.number("other"))

    def test_the_list_is_loaded_when_a_number_is_asked_for_and_once(self):
        calls: list[int] = []

        def loader() -> list[str]:
            calls.append(1)
            return IDS

        index = EpisodeIndex(loader, False, PLAYLIST)
        self.assertEqual(calls, [])
        self.assertIsNone(index.size)
        for video in IDS:
            _ = index.number(video)
        self.assertEqual(calls, [1])
        self.assertEqual(index.size, 5)

    def test_a_failing_list_raises_once_and_is_not_asked_again(self):
        calls: list[int] = []

        def loader() -> list[str]:
            calls.append(1)
            raise OSError("down")

        index = EpisodeIndex(loader, True, UPLOADS)
        with self.assertRaises(OSError):
            _ = index.number("a")
        self.assertIsNone(index.number("a"))
        self.assertIsNone(index.number("b"))
        self.assertEqual(calls, [1])
        self.assertIsNone(index.size)

    def test_threads_load_it_once(self):
        calls: list[int] = []
        release = threading.Event()

        def loader() -> list[str]:
            calls.append(1)
            _ = release.wait(5)
            return IDS

        index = EpisodeIndex(loader, False, PLAYLIST)
        results: list[int | None] = []
        threads = [
            threading.Thread(target=lambda: results.append(index.number(IDS[2])))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(results, [3] * 8)
        self.assertEqual(calls, [1])


class TestIndexFor(unittest.TestCase):
    def test_a_channel_is_numbered_by_its_uploads(self):
        lister = Lister(list(reversed(IDS)))
        for playlist_id in ("@handle", CHANNEL):
            with self.subTest(id=playlist_id):
                info = playlist(playlist_id, ["tab"], channel_id=CHANNEL)
                index = index_for(info, lister)
                assert index is not None
                self.assertEqual(index.order, UPLOADS)
                self.assertEqual([index.number(i) for i in IDS], [1, 2, 3, 4, 5])
        self.assertEqual(
            lister.urls[0], f"https://www.youtube.com/playlist?list={UPLOADS_ID}"
        )

    def test_the_uploads_are_only_listed_when_a_number_is_asked_for(self):
        lister = Lister(IDS)
        _ = index_for(playlist(CHANNEL, ["tab"], channel_id=CHANNEL), lister)
        self.assertEqual(lister.urls, [])

    def test_a_tab_of_a_channel_is_numbered_by_the_uploads_too(self):
        # A tab (here the shorts) has the id of the channel as well, and its own
        # position in the tab is not the number
        shorts = ["s3", "s2", "s1"]
        lister = Lister(["s3", "v2", "s2", "v1", "s1"])
        index = index_for(playlist(CHANNEL, shorts, channel_id=CHANNEL), lister)
        assert index is not None
        self.assertEqual(
            [index.number(i) for i in ("s1", "v1", "s2", "v2", "s3")], [1, 2, 3, 4, 5]
        )

    def test_a_channel_without_a_usable_id_is_not_numbered(self):
        for channel_id in (None, "", "UCshort", 5):
            with self.subTest(channel_id=channel_id):
                info = playlist("@handle", IDS, channel_id=channel_id)
                self.assertIsNone(index_for(info, Lister()))

    def test_a_playlist_is_numbered_in_its_own_order(self):
        lister = Lister()
        index = index_for(playlist("PLabc123", IDS), lister)
        assert index is not None
        self.assertEqual(index.order, PLAYLIST)
        self.assertEqual([index.number(i) for i in IDS], [1, 2, 3, 4, 5])
        self.assertEqual(lister.urls, [])  # the entries are those of the info

    def test_the_uploads_of_a_channel_as_a_playlist_are_newest_first(self):
        index = index_for(playlist(UPLOADS_ID, list(reversed(IDS))), Lister())
        assert index is not None
        self.assertEqual(index.order, UPLOADS)
        self.assertEqual([index.number(i) for i in IDS], [1, 2, 3, 4, 5])

    def test_other_playlists_made_of_uploads_are_newest_first_too(self):
        # Videos only, shorts only, streams only
        index = index_for(playlist("UULF" + CHANNEL[2:], list(reversed(IDS))), Lister())
        assert index is not None
        self.assertEqual(index.order, PLAYLIST)  # not all the uploads
        self.assertEqual([index.number(i) for i in IDS], [1, 2, 3, 4, 5])

    def test_the_given_ids_are_used_instead_of_the_entries(self):
        info = playlist("PLabc", [])
        index = index_for(info, Lister(), ids=IDS)
        assert index is not None
        self.assertEqual(index.number(IDS[1]), 2)

    def test_what_is_not_numbered(self):
        cases: list[dict[str, Any]] = [
            {"_type": "video", "id": "x", "extractor_key": "Youtube"},
            playlist("PLabc", IDS, key="Vimeo"),
            playlist("PLabc", IDS, key=""),
            {"_type": "playlist", "id": "PLabc", "entries": []},
            playlist("", IDS),
            playlist("PLabc", IDS, key="Youtube"),  # numbered: a YouTube extractor
        ]
        results = [index_for(info, Lister()) is None for info in cases]
        self.assertEqual(results, [True, True, True, True, True, False])

    def test_the_youtube_extractors(self):
        for key in ("Youtube", "YoutubeTab", "YoutubeMusicPlaylist"):
            with self.subTest(key=key):
                self.assertIsNotNone(
                    index_for(playlist("PLabc", IDS, key=key), Lister())
                )

    def test_odd_infos(self):
        odd: Mapping[str, object] = {}
        self.assertIsNone(index_for(odd, Lister()))
        self.assertIsNone(
            index_for(
                {"_type": "playlist", "id": 5, "extractor_key": "Youtube"}, Lister()
            )
        )


if __name__ == "__main__":
    unittest.main()
