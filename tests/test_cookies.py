# pyright: standard
import unittest
from http.cookiejar import Cookie, CookieJar
from typing import Any
from unittest import mock

from vidl.cookies import AUTH_COOKIES, CookieWatch, Refresh, fingerprint
from vidl.error import Error
from vidl.logger import Logger


def cookie(name: str, value: str, domain: str = ".youtube.com") -> Cookie:
    return Cookie(
        0, name, value, None, False, domain, True, domain.startswith("."),
        "/", True, True, None, False, None, None, {},
    )  # fmt: skip


def jar(*cookies: Cookie) -> CookieJar:
    result = CookieJar()
    for item in cookies:
        result.set_cookie(item)
    return result


class FakeProcessor:
    def __init__(self, cookies: CookieJar) -> None:
        self.cookiejar = cookies


class Loader:
    """A replacement for yt-dlp's load_cookies."""

    def __init__(self, result: CookieJar | BaseException) -> None:
        self.result = result
        self.calls: list[Any] = []

    def __call__(self, cookie_file: Any, browser: Any, processor: Any) -> CookieJar:
        self.calls.append((cookie_file, browser))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestFingerprint(unittest.TestCase):
    def test_describes_count_and_hash_of_the_auth_cookies(self):
        text = fingerprint(jar(cookie("SID", "a"), cookie("__Secure-1PSIDTS", "b")))
        self.assertRegex(text, r"^2 cookies, [0-9a-f]{8}$")
        self.assertRegex(fingerprint(jar(cookie("SID", "a"))), r"^1 cookie, ")
        self.assertRegex(fingerprint(jar()), r"^0 cookies, ")

    def test_a_rotated_value_changes_it(self):
        before = fingerprint(jar(cookie("__Secure-1PSIDTS", "old")))
        after = fingerprint(jar(cookie("__Secure-1PSIDTS", "new")))
        self.assertNotEqual(before, after)
        self.assertEqual(before, fingerprint(jar(cookie("__Secure-1PSIDTS", "old"))))

    def test_order_does_not_matter(self):
        a = fingerprint(jar(cookie("SID", "1"), cookie("HSID", "2")))
        b = fingerprint(jar(cookie("HSID", "2"), cookie("SID", "1")))
        self.assertEqual(a, b)

    def test_other_cookies_and_domains_are_left_out(self):
        base = fingerprint(jar(cookie("SID", "a")))
        extra = jar(
            cookie("SID", "a"),
            cookie("PREF", "tz=Oslo"),
            cookie("SOCS", "CAI"),
            cookie("SID", "other", domain=".example.com"),
        )
        self.assertEqual(fingerprint(extra), base)
        self.assertEqual(
            fingerprint(jar(cookie("SID", "a", domain="www.google.com"))).split(", ")[
                0
            ],
            "1 cookie",
        )

    def test_values_are_never_part_of_the_text(self):
        secret = "s3cr3t-value-123"
        text = fingerprint(jar(cookie("SID", secret), cookie("LOGIN_INFO", secret)))
        self.assertNotIn(secret, text)
        self.assertLess(len(text), 40)

    def test_the_names_are_those_youtube_checks(self):
        for name in ("SID", "SAPISID", "LOGIN_INFO", "__Secure-3PAPISID", "SIDCC"):
            self.assertIn(name, AUTH_COOKIES)


class TestCookieWatch(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = mock.patch("vidl.cookies.Debug.print")
        self.debug_print = patcher.start()
        self.addCleanup(patcher.stop)

    def watch(self, loader: Loader, browser: str | None = "safari") -> CookieWatch:
        return CookieWatch(3, browser, clock=self.clock, loader=loader)

    def logged(self) -> str:
        return "\n".join(call.args[1] for call in self.debug_print.call_args_list)

    def test_nothing_happens_without_a_rotation(self):
        loader = Loader(jar())
        watch = self.watch(loader)
        self.assertFalse(watch.rotated())
        self.assertIs(watch.refresh(FakeProcessor(jar())), Refresh.NOT_NEEDED)
        self.assertEqual(loader.calls, [])

    def test_rotation_is_noted_once_in_the_log(self):
        watch = self.watch(Loader(jar()))
        for _ in range(5):
            watch.note_rotation()
        self.assertTrue(watch.rotated())
        self.assertEqual(self.logged().count("rotation noticed"), 1)

    def test_refresh_loads_the_cookies_of_the_browser(self):
        old = jar(cookie("__Secure-1PSIDTS", "old"), cookie("SOCS", "CAI"))
        new = jar(cookie("__Secure-1PSIDTS", "new"))
        loader = Loader(new)
        watch = self.watch(loader)
        watch.note_rotation()
        processor = FakeProcessor(old)
        self.assertIs(watch.refresh(processor), Refresh.REFRESHED)
        self.assertEqual(loader.calls, [(None, ("safari", None, None, None))])
        values = {c.name: c.value for c in processor.cookiejar}
        # Replaced where the browser has it, the others stay
        self.assertEqual(values, {"__Secure-1PSIDTS": "new", "SOCS": "CAI"})
        self.assertFalse(watch.rotated())
        self.assertIn("refreshed", self.logged())

    def test_the_same_cookies_in_the_browser_mean_it_is_expired(self):
        same = jar(cookie("SID", "a"))
        watch = self.watch(Loader(jar(cookie("SID", "a"))))
        watch.note_rotation()
        self.assertIs(watch.refresh(FakeProcessor(same)), Refresh.UNCHANGED)
        self.assertIn("no longer valid there either", self.logged())

    def test_a_failing_browser_changes_nothing(self):
        old = jar(cookie("SID", "a"))
        watch = self.watch(Loader(OSError("locked")))
        watch.note_rotation()
        processor = FakeProcessor(old)
        self.assertIs(watch.refresh(processor), Refresh.FAILED)
        self.assertEqual(
            [(c.name, c.value) for c in processor.cookiejar], [("SID", "a")]
        )
        self.assertIn("refresh failed (OSError)", self.logged())
        self.assertNotIn("locked", self.logged())

    def test_a_missing_cookiejar_is_a_failure_not_a_crash(self):
        watch = self.watch(Loader(jar()))
        watch.note_rotation()
        self.assertIs(watch.refresh(object()), Refresh.FAILED)

    def test_refreshes_are_limited_in_time(self):
        loader = Loader(jar(cookie("SID", "new")))
        watch = self.watch(loader)
        processor = FakeProcessor(jar(cookie("SID", "old")))
        watch.note_rotation()
        self.assertIs(watch.refresh(processor), Refresh.REFRESHED)
        watch.note_rotation()  # still rotated right after
        self.assertIs(watch.refresh(processor), Refresh.TOO_SOON)
        self.clock.now += CookieWatch.MIN_INTERVAL - 1
        self.assertIs(watch.refresh(processor), Refresh.TOO_SOON)
        self.assertTrue(watch.rotated())  # still pending
        self.assertEqual(len(loader.calls), 1)
        self.clock.now += 1
        self.assertIsNot(watch.refresh(processor), Refresh.TOO_SOON)
        self.assertEqual(len(loader.calls), 2)

    def test_a_clock_set_back_does_not_block_refreshes(self):
        watch = self.watch(Loader(jar(cookie("SID", "new"))))
        processor = FakeProcessor(jar(cookie("SID", "old")))
        watch.note_rotation()
        watch.refresh(processor)
        self.clock.now -= 3600
        watch.note_rotation()
        self.assertIsNot(watch.refresh(processor), Refresh.TOO_SOON)

    def test_without_a_browser_nothing_is_done(self):
        loader = Loader(jar())
        watch = self.watch(loader, browser=None)
        watch.note_rotation()
        self.assertIs(watch.refresh(FakeProcessor(jar())), Refresh.NOT_NEEDED)
        watch.log_state(FakeProcessor(jar()), "loaded")
        self.assertEqual(loader.calls, [])

    def test_log_state_shows_the_fingerprint_only(self):
        watch = self.watch(Loader(jar()))
        processor = FakeProcessor(jar(cookie("SID", "very-secret")))
        watch.log_state(processor, "loaded")
        self.assertRegex(self.logged(), r"Cookies loaded: 1 cookie, [0-9a-f]{8}")
        self.assertNotIn("very-secret", self.logged())

    def test_log_state_survives_a_failing_load(self):
        class Broken:
            @property
            def cookiejar(self) -> CookieJar:
                raise RuntimeError("cannot read the browser")

        self.watch(Loader(jar())).log_state(Broken(), "loaded")
        self.assertIn("unavailable (RuntimeError)", self.logged())


class TestRotationText(unittest.TestCase):
    TEXT = (
        "The provided YouTube account cookies are no longer valid. They have likely "
        "been rotated in the browser as a security measure. For tips on how to "
        "effectively export YouTube cookies, refer to  https://github.com/yt-dlp/"
    )

    def test_is_cookie_rotation(self):
        for text, expected in (
            (self.TEXT, True),
            ("WARNING: [youtube] " + self.TEXT, True),
            ("\x1b[0;33mWARNING:\x1b[0m " + self.TEXT, True),
            ("The cookies have been rotated", True),
            ("Sign in to confirm you're not a bot", False),
            ("Unable to download webpage", False),
            ("", False),
        ):
            with self.subTest(text=text[:40]):
                self.assertIs(Error.is_cookie_rotation(text), expected)

    def test_the_logger_passes_only_the_warning_on(self):
        seen: list[str] = []
        with mock.patch("vidl.logger.Debug.print"):
            logger = Logger(1, lambda message: None, lambda: seen.append("rotated"))
            logger.warning("WARNING: [youtube] " + self.TEXT)
            logger.info(self.TEXT)  # only warnings
            logger.error("ERROR: " + self.TEXT)
            logger.warning("WARNING: something else")
        self.assertEqual(seen, ["rotated"])

    def test_the_logger_without_a_callback(self):
        with mock.patch("vidl.logger.Debug.print"):
            Logger(1, lambda message: None).warning(self.TEXT)


if __name__ == "__main__":
    unittest.main()
