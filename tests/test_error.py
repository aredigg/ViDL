# pyright: standard
import io
import socket
import sys
import unittest
import urllib.error
from typing import Any, cast

from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import (
    HTTPError,
    IncompleteRead,
    ProxyError,
    SSLError,
    TransportError,
)
from yt_dlp.utils import (
    ContentTooShortError,
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    PostProcessingError,
    UnsupportedError,
    UserNotLive,
    YoutubeDLError,
    bug_reports_message,
)

from vidl.error import Error
from vidl.unicode import Unicode

Category = Error.Category
Kind = Error.Kind

# Hints as appended by yt-dlp, see extractor/common.py (_login_hint) and
# extractor/youtube/_base.py (_youtube_login_hint)
COOKIES_HINT = (
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
    "  for how to manually pass cookies"
)
YOUTUBE_HINT = (
    f"{COOKIES_HINT}. Also see  "
    "https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies  "
    "for tips on effectively exporting YouTube cookies"
)
# extractor/youtube/_video.py, when YouTube answers "try again later"
RATE_LIMIT = (
    "This content isn't available, try again later. The current session has been "
    "rate-limited by YouTube for up to an hour. It is recommended to use `-t sleep` "
    "to add a delay between video requests to avoid exceeding the rate limit. For "
    "more information, refer to  https://github.com/yt-dlp/yt-dlp/wiki/Extractors"
    "#this-content-isnt-available-try-again-later"
)
# The runtime value is the extractor name, the typeshed stub says InfoExtractor
YOUTUBE: Any = "youtube"
URLOPEN = "<urlopen error [Errno 8] nodename nor servname provided, or not known>"


def http_error(status: int) -> HTTPError:
    return HTTPError(Response(io.BytesIO(), "https://example.com", {}, status=status))


def download_error(exception: BaseException, message: str | None = None):
    """A DownloadError as raised by yt-dlp while handling exception."""
    try:
        raise exception
    except BaseException:  # noqa: BLE001
        text = f"ERROR: {exception}" if message is None else message
        return DownloadError(text, cast(Any, sys.exc_info()))


def raised_from(exception: BaseException, cause: BaseException) -> BaseException:
    """The exception, raised while handling cause like yt-dlp does."""
    try:
        try:
            raise cause
        except BaseException:  # noqa: BLE001
            raise exception
    except BaseException as e:  # noqa: BLE001
        return e


class TestCategory(unittest.TestCase):
    def test_kinds(self):
        kinds = {
            Kind.RESTRICTION: [
                Category.BOT_CHECK,
                Category.RATE_LIMITED,
                Category.UNAVAILABLE,
            ],
            Kind.PERMANENT: [
                Category.REMOVED,
                Category.PRIVATE,
                Category.MEMBERS_ONLY,
                Category.AGE_RESTRICTED,
                Category.GEO_BLOCKED,
                Category.COPYRIGHT,
                Category.DRM,
                Category.UNSUPPORTED,
                Category.NOT_FOUND,
                Category.FORMAT_UNAVAILABLE,
            ],
            Kind.TRANSIENT: [
                Category.FORBIDDEN,
                Category.SERVER_ERROR,
                Category.NETWORK,
                Category.POSTPROCESSING,
                Category.UPCOMING,
                Category.LIVE_OFFLINE,
            ],
            Kind.UNKNOWN: [Category.INTERNAL, Category.UNKNOWN],
        }
        self.assertEqual(sum(len(c) for c in kinds.values()), len(Category))
        for kind, categories in kinds.items():
            for category in categories:
                with self.subTest(category=category):
                    self.assertIs(category.kind, kind)

    def test_values_are_short_summaries(self):
        for category in Category:
            with self.subTest(category=category):
                self.assertTrue(category.value)
                self.assertLessEqual(Unicode.len(category.value), Error.SUMMARY_MAX)

    def test_restriction(self):
        self.assertTrue(Error("Video unavailable").restriction)
        self.assertIs(Error("Video unavailable").kind, Kind.RESTRICTION)
        self.assertFalse(Error("Private video").restriction)
        self.assertIs(Error("Private video").kind, Kind.PERMANENT)


class TestStripLevel(unittest.TestCase):
    def test_strip_level(self):
        cases = [
            ("ERROR: x", "x"),
            ("WARNING: x", "x"),
            ("ERROR: ERROR: x", "x"),
            ("WARNING: ERROR: x", "x"),
            ("  ERROR:   x", "x"),
            ("ERROR: \r[download] x", "[download] x"),
            ("\x1b[0;31mERROR:\x1b[0m x", "x"),
            ("ERROR:", ""),
            ("  x", "x"),
            ("error: x", "error: x"),
            ("ERRORS: x", "ERRORS: x"),
            ("ERROR:x", "ERROR:x"),
            ("x ERROR: y", "x ERROR: y"),
            ("", ""),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(Error.strip_level(text), expected)


class TestSplitProvider(unittest.TestCase):
    def test_split_provider(self):
        cases = [
            ("[youtube] x", ("youtube", "x")),
            ("[youtube:tab] x", ("youtube:tab", "x")),
            ("[download]   x", ("download", "x")),
            ("[youtube]", ("youtube", "")),
            ("[youtube] [jsc:deno] x", ("youtube", "x")),
            ("[youtube] [info] [debug] x", ("youtube", "x")),
            ("[youtube] [Errno 2] x", ("youtube", "[Errno 2] x")),
            ("[Errno 104] Connection reset", (None, "[Errno 104] Connection reset")),
            ("[youtube]x", (None, "[youtube]x")),
            ("[1abc] x", (None, "[1abc] x")),
            ("[] x", (None, "[] x")),
            ("[a b] x", (None, "[a b] x")),
            ("[" + "a" * 40 + "] x", ("a" * 40, "x")),
            ("[" + "a" * 41 + "] x", (None, "[" + "a" * 41 + "] x")),
            ("x [youtube] y", (None, "x [youtube] y")),
            ("", (None, "")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(Error.split_provider(text), expected)

    def test_only_the_first_tag_is_removed_on_request(self):
        self.assertEqual(
            Error.split_provider("[youtube] [jsc:deno] x", all_tags=False),
            ("youtube", "[jsc:deno] x"),
        )


class TestSplitIdentity(unittest.TestCase):
    def test_split_identity(self):
        cases = [
            ("abc123: Video unavailable", ("abc123", "Video unavailable")),
            ("dQw4w9WgXcQ: Private video", ("dQw4w9WgXcQ", "Private video")),
            ("abcdefghijk: Private video", ("abcdefghijk", "Private video")),
            ("@SomeChannel: x", ("@SomeChannel", "x")),
            ("UCuAXFkgsw1L7xaCfnd5JJOw: x", ("UCuAXFkgsw1L7xaCfnd5JJOw", "x")),
            ("some_id: x", ("some_id", "x")),
            ("a-b: x", ("a-b", "x")),
            ("abc123:  x", ("abc123", "x")),
            ("abc: Private video", (None, "abc: Private video")),
            ("Postprocessing: Conversion failed!", None),
            ("Destination: x.mp4", None),
            ("Unsupported: x", None),
            ("ERROR: x", None),
            ("yt-dlp: x", None),
            ("User-Agent: x", None),
            ("HTTP Error 429: Too Many Requests", None),
            ("Got error: HTTP Error 403: Forbidden", None),
            ("Unable to download webpage: HTTP Error 404: Not Found", None),
            ("https://example.com/a1: x", None),
            ("https://example.com:8080/v1: x", None),
            ("12:30: x", None),
            ("1:02:03: x", None),
            ("Premieres at 12:30: x", None),
            ("a/b1: x", None),
            ("(abc1): x", None),
            ("abc123: ", None),
            ("abc123:", None),
            ("abc123 x", None),
            (": x", None),
            ("a" * 64 + "1: x", None),
            ("a" * 63 + "1: x", ("a" * 63 + "1", "x")),
            ("", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    Error.split_identity(text),
                    (None, text) if expected is None else expected,
                )

    def test_known_ids(self):
        cases = [
            ("abc: Private video", ["abc"], ("abc", "Private video")),
            ("Some Title: x", ["Some Title"], ("Some Title", "x")),
            ("Some Title: x", ["Some"], (None, "Some Title: x")),
            ("Postprocessing: x", ["Postprocessing"], ("Postprocessing", "x")),
            (": x", [""], (None, ": x")),
            ("abc: x", [""], (None, "abc: x")),
        ]
        for text, known_ids, expected in cases:
            with self.subTest(text=text, known_ids=known_ids):
                self.assertEqual(Error.split_identity(text, known_ids), expected)

    def test_known_ids_may_be_a_generator(self):
        known_ids = (i for i in ["x", "abc"])
        self.assertEqual(Error.split_identity("abc: y", known_ids), ("abc", "y"))

    def test_only_the_first_colon_is_considered(self):
        self.assertEqual(
            Error.split_identity("Unable: abc123: x"), (None, "Unable: abc123: x")
        )


class TestErrorText(unittest.TestCase):
    def assertError(
        self,
        error: Error,
        category: Error.Category,
        summary: str | None = None,
        provider: str | None = None,
        identity: str | None = None,
        message: str | None = None,
    ):
        self.assertIs(error.category, category)
        self.assertEqual(error.summary, summary or category.value)
        self.assertEqual(error.provider, provider)
        self.assertEqual(error.identity, identity)
        if message is not None:
            self.assertEqual(error.message, message)

    def test_categories(self):
        # text, category, summary (None: the category value)
        cases: list[tuple[str, Error.Category, str | None]] = [
            (
                (
                    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm your age. This video "
                    f"may be inappropriate for some users. {YOUTUBE_HINT}"
                ),
                Category.AGE_RESTRICTED,
                None,
            ),
            ("Age-restricted; requires authentication", Category.AGE_RESTRICTED, None),
            (
                "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. "
                + YOUTUBE_HINT,
                Category.BOT_CHECK,
                None,
            ),
            ("Sign in to confirm you're not a bot", Category.BOT_CHECK, None),
            (
                (
                    "Video unavailable. YouTube is requiring a captcha challenge before "
                    "playback"
                ),
                Category.BOT_CHECK,
                None,
            ),
            ("HTTP Error 429: Too Many Requests", Category.RATE_LIMITED, None),
            (
                (
                    "[youtube:tab] @chan: Unable to download API page: HTTP Error 429: "
                    "Too Many Requests"
                ),
                Category.RATE_LIMITED,
                None,
            ),
            (f"[youtube] dQw4w9WgXcQ: {RATE_LIMIT}", Category.RATE_LIMITED, None),
            (
                (
                    "Join this channel to get access to members-only content like this "
                    "video, and other exclusive perks."
                ),
                Category.MEMBERS_ONLY,
                None,
            ),
            (
                (
                    "This video is available to this channel’s members on level: Tier 1 "
                    "(or any higher level)."
                ),
                Category.MEMBERS_ONLY,
                None,
            ),
            (
                "Private video. Sign in if you've been granted access to this video. "
                + YOUTUBE_HINT,
                Category.PRIVATE,
                None,
            ),
            ("This video is private", Category.PRIVATE, None),
            (
                "Video unavailable. This video has been removed by the uploader",
                Category.REMOVED,
                None,
            ),
            (
                (
                    "Video unavailable. This video is no longer available because the "
                    "YouTube account associated with this video has been terminated."
                ),
                Category.REMOVED,
                None,
            ),
            (
                (
                    "This video has been removed for violating YouTube's policy on hate "
                    "speech."
                ),
                Category.REMOVED,
                None,
            ),
            (
                (
                    "Video unavailable. This video is no longer available due to a "
                    "copyright claim by Example Corp"
                ),
                Category.COPYRIGHT,
                None,
            ),
            (
                (
                    "This video contains content from SME, who has blocked it in your "
                    "country on copyright grounds"
                ),
                Category.COPYRIGHT,
                None,
            ),
            (
                "This video is not available from your location due to geo restriction",
                Category.GEO_BLOCKED,
                None,
            ),
            (
                (
                    "ERROR: The uploader has not made this video available in your "
                    "country\nThis video is available in United States.\nYou might want "
                    "to use a VPN or a proxy server (with --proxy) to workaround."
                ),
                Category.GEO_BLOCKED,
                None,
            ),
            ("[youtube] abc123: This video is DRM protected", Category.DRM, None),
            ("Premieres in 3 hours", Category.UPCOMING, "Premieres in 3 hours"),
            (
                "This live event will begin in 2 days.",
                Category.UPCOMING,
                "Starts in 2 days",
            ),
            (
                "This live event will begin in a few moments.",
                Category.UPCOMING,
                "Starts in a few moments",
            ),
            ("This video is upcoming", Category.UPCOMING, None),
            ("The channel is not currently live", Category.LIVE_OFFLINE, None),
            ("The channel is offline", Category.LIVE_OFFLINE, None),
            ("Video unavailable", Category.UNAVAILABLE, None),
            (
                "Video unavailable. This content isn’t available.",
                Category.UNAVAILABLE,
                None,
            ),
            ("This content isn't available.", Category.UNAVAILABLE, None),
            (
                (
                    "Requested format is not available. Use --list-formats for a list of "
                    "available formats"
                ),
                Category.FORMAT_UNAVAILABLE,
                None,
            ),
            ("No video formats found!", Category.FORMAT_UNAVAILABLE, None),
            (
                "ERROR: \r[download] Got error: HTTP Error 403: Forbidden",
                Category.FORBIDDEN,
                None,
            ),
            (
                "ERROR: unable to download video data: HTTP Error 403: Forbidden",
                Category.FORBIDDEN,
                None,
            ),
            (
                "Got HTTP Error 403 caused by Cloudflare anti-bot challenge",
                Category.FORBIDDEN,
                None,
            ),
            (
                "Unable to download webpage: HTTP Error 404: Not Found",
                Category.NOT_FOUND,
                "Not found (HTTP 404)",
            ),
            ("HTTP Error 410: Gone", Category.NOT_FOUND, "Not found (HTTP 410)"),
            ("This video does not exist.", Category.NOT_FOUND, None),
            (
                "Unable to download API page: HTTP Error 503: Service Unavailable",
                Category.SERVER_ERROR,
                "Server error (HTTP 503)",
            ),
            (
                (
                    f"ERROR: [generic] Unable to download webpage: {URLOPEN} (caused by "
                    f"TransportError('{URLOPEN}'))"
                ),
                Category.NETWORK,
                None,
            ),
            (
                "Unable to download webpage: The read operation timed out",
                Category.NETWORK,
                None,
            ),
            ("fragment 3 not found, unable to continue", Category.NETWORK, None),
            ("[Errno 104] Connection reset by peer", Category.NETWORK, None),
            ("Did not get any data blocks", Category.NETWORK, None),
            ("Postprocessing: Conversion failed!", Category.POSTPROCESSING, None),
            (
                (
                    "Postprocessing: ffprobe and ffmpeg not found. Please install or "
                    "provide the path using --ffmpeg-location"
                ),
                Category.POSTPROCESSING,
                None,
            ),
            (
                "[generic] Unsupported URL: https://example.com/watch?v=1",
                Category.UNSUPPORTED,
                None,
            ),
            (
                (
                    "[youtube] dQw4w9WgXcQ: Unable to extract yt initial data"
                    f"{bug_reports_message()}"
                ),
                Category.UNKNOWN,
                "Unable to extract yt initial data",
            ),
            ("This video is not available.", Category.UNKNOWN, None),
        ]
        for text, category, summary in cases:
            with self.subTest(text=text):
                error = Error(text)
                self.assertIs(error.category, category)
                if summary is not None:
                    self.assertEqual(error.summary, summary)
                elif category is not Category.UNKNOWN:
                    self.assertEqual(error.summary, category.value)
                self.assertTrue(error.summary)
                self.assertLessEqual(Unicode.len(error.summary), Error.SUMMARY_MAX)
                self.assertTrue(error.message)

    def test_prefixes(self):
        # text, provider, identity, message
        cases: list[tuple[str, str | None, str | None, str]] = [
            ("ERROR: [youtube] abc123: Video unavailable", "youtube", "abc123", ""),
            (
                "ERROR: ERROR: [youtube] abc123: Video unavailable",
                "youtube",
                "abc123",
                "",
            ),
            ("[youtube] abc123: Video unavailable", "youtube", "abc123", ""),
            ("abc123: Video unavailable", None, "abc123", ""),
            ("Video unavailable", None, None, ""),
            ("[youtube] Video unavailable", "youtube", None, ""),
            ("[youtube] abc: Video unavailable", "youtube", None, "abc: "),
            ("WARNING: [youtube] abc123: Video unavailable", "youtube", "abc123", ""),
            (
                "[youtube:tab] [extra] @chan: Video unavailable",
                "youtube:tab",
                "@chan",
                "",
            ),
        ]
        for text, provider, identity, message in cases:
            with self.subTest(text=text):
                error = Error(text)
                self.assertError(
                    error,
                    Category.UNAVAILABLE,
                    provider=provider,
                    identity=identity,
                    message=f"{message}Video unavailable",
                )

    def test_identity_from_youtube_age_gate(self):
        error = Error(
            "[youtube] dQw4w9WgXcQ: Sign in to confirm your age. This video may be "
            "inappropriate for some users."
        )
        self.assertError(
            error,
            Category.AGE_RESTRICTED,
            provider="youtube",
            identity="dQw4w9WgXcQ",
            message="Sign in to confirm your age. This video may be inappropriate "
            "for some users.",
        )

    def test_body_is_never_split(self):
        cases = [
            "HTTP Error 429: Too Many Requests",
            "Postprocessing: Conversion failed!",
            "Unable to download webpage: HTTP Error 404: Not Found",
            "[Errno 104] Connection reset by peer",
            "Premieres at 12:30: see you",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ: Unsupported",
        ]
        for text in cases:
            with self.subTest(text=text):
                error = Error(text)
                self.assertIsNone(error.provider)
                self.assertIsNone(error.identity)
                self.assertEqual(error.message, text)

    def test_nested_got_error(self):
        error = Error("ERROR: \r[download] Got error: HTTP Error 403: Forbidden")
        self.assertError(
            error,
            Category.FORBIDDEN,
            provider="download",
            message="HTTP Error 403: Forbidden",
        )
        self.assertEqual(
            error.raw, "ERROR: \r[download] Got error: HTTP Error 403: Forbidden"
        )

    def test_url_with_colons_and_times_stay_in_the_message(self):
        error = Error(
            "[youtube] abc123: Unable to download webpage: https://example.com:8080/a?"
            "t=12:30 at 04:00 UTC"
        )
        self.assertEqual(error.identity, "abc123")
        self.assertEqual(
            error.message,
            "Unable to download webpage: https://example.com:8080/a?t=12:30 at 04:00 UTC",
        )
        self.assertIs(error.category, Category.NETWORK)

    def test_errno(self):
        error = Error(f"ERROR: [generic] Unable to download webpage: {URLOPEN}")
        self.assertError(
            error,
            Category.NETWORK,
            provider="generic",
            message=f"Unable to download webpage: {URLOPEN}",
        )

    def test_bug_report_boilerplate_is_removed(self):
        for before in (";", ".", ","):
            text = (
                f"[youtube] abc123: Unable to extract nsig{bug_reports_message(before)}"
            )
            with self.subTest(before=before):
                message = Error(text).message
                self.assertTrue(message.startswith("Unable to extract nsig"), message)
                self.assertNotIn("report", message)
                self.assertNotIn("yt-dlp -U", message)

    def test_cookie_hints_are_removed(self):
        error = Error(
            "ERROR: [youtube] abc123: Sign in to confirm you’re not a bot. "
            + YOUTUBE_HINT
        )
        self.assertEqual(error.message, "Sign in to confirm you’re not a bot.")

    def test_rate_limit_hints_are_removed(self):
        self.assertEqual(
            Error(RATE_LIMIT).message,
            "This content isn't available, try again later. The current session has "
            "been rate-limited by YouTube for up to an hour.",
        )

    def test_summaries_leave_out_urls(self):
        cases = [
            (
                "Premieres in 3 hours at https://www.youtube.com/watch?v=x: soon",
                "Premieres in 3 hours",
            ),
            ("Odd failure at https://example.com/a:b today", "Odd failure"),
            ("https://example.com/only", "Unknown error"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                error = Error(text)
                self.assertNotIn("://", error.summary)
                self.assertTrue(error.summary)
                if expected is not None:
                    self.assertEqual(error.summary, expected)
        # The message keeps the URL, e.g. for the debug log
        self.assertIn("https://example.com/a:b", Error(cases[1][0]).message)

    def test_display_includes_the_identity(self):
        self.assertEqual(
            Error("[youtube] abc123: Private video").display, "abc123: Private video"
        )
        self.assertEqual(Error("Private video").display, "Private video")

    def test_caused_by_is_removed(self):
        error = Error(
            "[youtube] abc123: Unable to download API page: HTTP Error 429: Too Many "
            "Requests (caused by <HTTPError 429: Too Many Requests>)"
        )
        self.assertEqual(
            error.message,
            "Unable to download API page: HTTP Error 429: Too Many Requests",
        )
        nested = Error("Unable (caused by TransportError('a (b) c')) at the end")
        self.assertEqual(nested.message, "Unable at the end")
        unbalanced = Error("Unable (caused by TransportError('a (b c'))")
        self.assertEqual(unbalanced.message, "Unable")

    def test_cleanup_falls_back_to_the_stripped_text(self):
        text = "ERROR: Use --cookies-from-browser or --cookies for the authentication."
        error = Error(text)
        self.assertEqual(
            error.message,
            "Use --cookies-from-browser or --cookies for the authentication.",
        )
        self.assertTrue(error.summary)
        self.assertEqual(Error("ERROR: ").message, "ERROR:")

    def test_multi_line(self):
        error = Error(
            "ERROR: [youtube] abc123: Video unavailable\n\nERROR: second  problem\r\n"
        )
        self.assertEqual(error.message, "Video unavailable second problem")
        self.assertIs(error.category, Category.UNAVAILABLE)
        unknown = Error("First line of trouble\nsecond line")
        self.assertEqual(unknown.message, "First line of trouble second line")
        self.assertEqual(unknown.summary, "First line of trouble")

    def test_unknown_summary_is_the_first_sentence(self):
        error = Error("Something odd happened. More details follow here.")
        self.assertIs(error.category, Category.UNKNOWN)
        self.assertEqual(error.summary, "Something odd happened")
        self.assertEqual(
            error.message, "Something odd happened. More details follow here."
        )

    def test_known_ids(self):
        error = Error("ERROR: [youtube] abc: Private video", known_ids=["abc"])
        self.assertError(error, Category.PRIVATE, provider="youtube", identity="abc")
        self.assertEqual(error.message, "Private video")

    def test_empty(self):
        error = Error("")
        self.assertError(error, Category.UNKNOWN, message="")
        self.assertEqual(error.raw, "")
        self.assertEqual(error.summary, "Unknown error")

    def test_ansi(self):
        error = Error(
            "\x1b[0;31mERROR:\x1b[0m [youtube] abc123: \x1b[1mVideo unavailable\x1b[0m"
        )
        self.assertEqual(error.raw, "ERROR: [youtube] abc123: Video unavailable")
        self.assertError(
            error,
            Category.UNAVAILABLE,
            provider="youtube",
            identity="abc123",
            message="Video unavailable",
        )

    def test_summary_is_capped_at_a_word_boundary(self):
        message = (
            "Something very unusual went wrong while talking to the remote "
            "server about this particular item"
        )
        summary = Error(message).summary
        self.assertLessEqual(Unicode.len(summary), Error.SUMMARY_MAX)
        self.assertTrue(summary.endswith(Error.ELLIPSIS))
        stem = summary.removesuffix(Error.ELLIPSIS)
        self.assertTrue(message.startswith(stem))
        self.assertEqual(message[len(stem)], " ")

    def test_summary_of_wide_characters_is_capped(self):
        summary = Error("日本語のエラー" * 20).summary
        self.assertLessEqual(Unicode.len(summary), Error.SUMMARY_MAX)
        self.assertTrue(summary.endswith(Error.ELLIPSIS))
        long_word = Error("x" * 100).summary
        self.assertEqual(long_word, "x" * (Error.SUMMARY_MAX - 1) + Error.ELLIPSIS)

    def test_short_summary_is_not_capped(self):
        message = "a" * Error.SUMMARY_MAX
        self.assertEqual(Error(message).summary, message)

    def test_long_upcoming_summary_is_capped(self):
        error = Error("Premieres in " + "very " * 20 + "long time.")
        self.assertIs(error.category, Category.UPCOMING)
        self.assertLessEqual(Unicode.len(error.summary), Error.SUMMARY_MAX)
        self.assertTrue(error.summary.startswith("Premieres in very"))


class TestFromException(unittest.TestCase):
    def test_extractor_error_in_download_error(self):
        error = Error.from_exception(
            download_error(
                ExtractorError(
                    "Private video. Sign in if you've been granted access to this "
                    "video. " + YOUTUBE_HINT,
                    video_id="abc",
                    ie=YOUTUBE,
                    expected=True,
                )
            )
        )
        self.assertIs(error.category, Category.PRIVATE)
        self.assertEqual(error.provider, "youtube")
        self.assertEqual(error.identity, "abc")
        self.assertEqual(
            error.message,
            "Private video. Sign in if you've been granted access to this video.",
        )
        self.assertTrue(error.raw.startswith("ERROR: [youtube] abc: Private video."))

    def test_extractor_error_text_is_not_split(self):
        error = Error.from_exception(
            ExtractorError("Destination_1: x", video_id="vid", expected=True)
        )
        self.assertEqual(error.identity, "vid")
        self.assertEqual(error.message, "Destination_1: x")

    def test_unexpected_extractor_error_without_bug_report(self):
        error = Error.from_exception(
            download_error(
                ExtractorError("Unable to extract yt initial data", video_id="vid")
            )
        )
        self.assertIs(error.category, Category.UNKNOWN)
        self.assertEqual(error.message, "Unable to extract yt initial data")
        self.assertEqual(error.summary, "Unable to extract yt initial data")
        self.assertIn("please report this issue", error.raw)

    def test_http_cause_wins_over_text(self):
        cases = [
            (429, Category.RATE_LIMITED, "Rate limited"),
            (403, Category.FORBIDDEN, "HTTP 403 Forbidden"),
            (404, Category.NOT_FOUND, "Not found (HTTP 404)"),
            (410, Category.NOT_FOUND, "Not found (HTTP 410)"),
            (500, Category.SERVER_ERROR, "Server error (HTTP 500)"),
            (503, Category.SERVER_ERROR, "Server error (HTTP 503)"),
        ]
        for status, category, summary in cases:
            with self.subTest(status=status):
                cause = http_error(status)
                extractor = raised_from(
                    ExtractorError(
                        f"Unable to download API page: {cause}",
                        cause=cause,
                        video_id="vid",
                        ie=YOUTUBE,
                    ),
                    cause,
                )
                error = Error.from_exception(download_error(extractor))
                self.assertIs(error.category, category)
                self.assertEqual(error.summary, summary)
                self.assertEqual(error.identity, "vid")
                self.assertEqual(error.provider, "youtube")
                self.assertEqual(error.message, f"Unable to download API page: {cause}")
                # The text alone points elsewhere, the status still wins
                error = Error.from_exception(
                    ExtractorError("Video unavailable", cause=cause, expected=True)
                )
                self.assertIs(error.category, category)

    def test_http_errors(self):
        cases: list[tuple[BaseException, Error.Category]] = [
            (http_error(429), Category.RATE_LIMITED),
            (http_error(403), Category.FORBIDDEN),
            (http_error(404), Category.NOT_FOUND),
            (http_error(502), Category.SERVER_ERROR),
            (http_error(400), Category.UNKNOWN),
            (
                urllib.error.HTTPError(
                    "https://example.com", 404, "Not Found", cast(Any, None), None
                ),
                Category.NOT_FOUND,
            ),
            (
                urllib.error.HTTPError(
                    "https://example.com", 503, "Unavailable", cast(Any, None), None
                ),
                Category.SERVER_ERROR,
            ),
        ]
        for exception, category in cases:
            with self.subTest(exception=exception):
                self.assertIs(Error.from_exception(exception).category, category)
        self.assertEqual(
            Error.from_exception(http_error(400)).summary, "HTTP Error 400: Bad Request"
        )

    def test_network_errors(self):
        cases: list[BaseException] = [
            TransportError(cause=ConnectionResetError(54, "Connection reset by peer")),
            IncompleteRead(100, 200),
            SSLError("EOF occurred in violation of protocol"),
            ProxyError("Unable to connect to proxy"),
            ContentTooShortError(100, 200),
            TimeoutError(),
            ConnectionResetError(54, "Connection reset by peer"),
            socket.gaierror(8, "nodename nor servname provided, or not known"),
            download_error(TimeoutError("timed out")),
        ]
        for exception in cases:
            with self.subTest(exception=exception):
                error = Error.from_exception(exception)
                self.assertIs(error.category, Category.NETWORK)
                self.assertEqual(error.summary, "Network error")
                self.assertTrue(error.message)
                self.assertTrue(error.raw)
        self.assertEqual(Error.from_exception(TimeoutError()).message, "TimeoutError")

    def test_downloader_error_uses_the_download_error_text(self):
        error = Error.from_exception(
            download_error(
                http_error(403),
                "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            )
        )
        self.assertIs(error.category, Category.FORBIDDEN)
        self.assertEqual(
            error.message, "unable to download video data: HTTP Error 403: Forbidden"
        )

    def test_geo_restricted(self):
        exception = GeoRestrictedError(
            "The uploader has not made this video available in your country",
            countries=cast(Any, ["US", "CA"]),
        )
        error = Error.from_exception(download_error(exception))
        self.assertIs(error.category, Category.GEO_BLOCKED)
        self.assertEqual(error.summary, "Geo blocked (only US, CA)")
        self.assertEqual(
            error.message,
            "The uploader has not made this video available in your country",
        )
        many = GeoRestrictedError(
            "Blocked", countries=cast(Any, ["US", "CA", "DE", "FR", "GB", "NO"])
        )
        self.assertEqual(Error.from_exception(many).summary, "Geo blocked")
        # The type wins over a text without any hint
        self.assertIs(Error.from_exception(many).category, Category.GEO_BLOCKED)

    def test_typed_errors(self):
        cases: list[tuple[BaseException, Error.Category, str]] = [
            (
                UserNotLive(video_id="@chan"),
                Category.LIVE_OFFLINE,
                "The channel is not currently live",
            ),
            (
                UnsupportedError("https://example.com/x"),
                Category.UNSUPPORTED,
                "Unsupported URL: https://example.com/x",
            ),
            (
                download_error(
                    PostProcessingError("Conversion failed!"),
                    "ERROR: Postprocessing: Conversion failed!",
                ),
                Category.POSTPROCESSING,
                "Postprocessing: Conversion failed!",
            ),
            (
                PostProcessingError("Something odd"),
                Category.POSTPROCESSING,
                "Something odd",
            ),
        ]
        for exception, category, message in cases:
            with self.subTest(exception=exception):
                error = Error.from_exception(exception)
                self.assertIs(error.category, category)
                self.assertEqual(error.message, message)
        self.assertEqual(Error.from_exception(cases[0][0]).identity, "@chan")

    def test_download_error_without_exception(self):
        for exc_info in (None, (None, None, None), "junk", (1,)):
            with self.subTest(exc_info=exc_info):
                error = Error.from_exception(
                    DownloadError(
                        "ERROR: [youtube] abc123: Video unavailable",
                        cast(Any, exc_info),
                    )
                )
                self.assertIs(error.category, Category.UNAVAILABLE)
                self.assertEqual(error.provider, "youtube")
                self.assertEqual(error.identity, "abc123")
                self.assertEqual(error.message, "Video unavailable")

    def test_known_ids(self):
        error = Error.from_exception(
            DownloadError("ERROR: [youtube] Some Title: Private video"),
            known_ids=iter(["Some Title"]),
        )
        self.assertEqual(error.identity, "Some Title")
        self.assertIs(error.category, Category.PRIVATE)

    def test_text_of_the_cause_is_used(self):
        cause = ExtractorError("Sign in to confirm you’re not a bot", expected=True)
        error = Error.from_exception(
            ExtractorError("Unable to extract player response", cause=cause)
        )
        self.assertIs(error.category, Category.BOT_CHECK)
        self.assertEqual(error.message, "Unable to extract player response")

    def test_cause_cycle(self):
        first = ExtractorError("loop a", expected=True)
        second = ExtractorError("loop b", cause=first, expected=True)
        first.cause = second
        error = Error.from_exception(first)
        self.assertIs(error.category, Category.UNKNOWN)
        self.assertEqual(error.message, "loop a")
        context = RuntimeError("a")
        other = RuntimeError("b")
        context.__context__ = other
        other.__context__ = context
        self.assertIs(Error.from_exception(context).category, Category.INTERNAL)

    def test_chain_depth_is_limited(self):
        # Not ExtractorError, its message nests the repr of its cause
        def chain(length: int) -> BaseException:
            exception: BaseException = TimeoutError("timed out")
            for index in range(length):
                wrapper = YoutubeDLError(f"wrapper {index}")
                wrapper.__cause__ = exception
                exception = wrapper
            return exception

        self.assertIs(
            Error.from_exception(chain(Error.CHAIN_MAX - 1)).category, Category.NETWORK
        )
        self.assertIs(Error.from_exception(chain(50)).category, Category.UNKNOWN)

    def test_broken_str(self):
        class Broken(Exception):
            def __str__(self) -> str:
                raise ValueError("broken")

        error = Error.from_exception(Broken())
        self.assertIs(error.category, Category.INTERNAL)
        self.assertEqual(error.summary, "Internal error (Broken)")
        self.assertEqual(error.message, "Broken")
        self.assertEqual(error.raw, "Broken")
        wrapped = Error.from_exception(download_error(Broken(), "ERROR: broken"))
        self.assertIs(wrapped.category, Category.INTERNAL)

    def test_internal_errors(self):
        error = Error.from_exception(RuntimeError("boom"))
        self.assertIs(error.category, Category.INTERNAL)
        self.assertIs(error.kind, Kind.UNKNOWN)
        self.assertEqual(error.summary, "Internal error (RuntimeError)")
        self.assertEqual(error.message, "RuntimeError: boom")
        self.assertEqual(error.raw, "boom")
        # Text never classifies a bug
        self.assertIs(
            Error.from_exception(ValueError("HTTP Error 429")).category,
            Category.INTERNAL,
        )
        wrapped = Error.from_exception(download_error(KeyError("formats")))
        self.assertIs(wrapped.category, Category.INTERNAL)
        self.assertEqual(wrapped.message, "KeyError: 'formats'")

    def test_os_error_is_not_internal(self):
        error = Error.from_exception(OSError(28, "No space left on device"))
        self.assertIs(error.category, Category.UNKNOWN)
        self.assertEqual(error.summary, "[Errno 28] No space left on device")


if __name__ == "__main__":
    unittest.main()
