"""Detects and handles rotated browser cookies.

YouTube rotates the cookies of a logged in session as a security measure, and
a browser that is in use rotates them under the feet of yt-dlp, which read
them once when its downloader was created. yt-dlp notices it after each request
and only warns: "The provided YouTube account cookies are no longer valid.
They have likely been rotated in the browser as a security measure"
(extractor/youtube/_base.py). The warning arrives through the logger, so
Logger passes it to a CookieWatch, and the channel then lets the watch load the
current cookies of the browser into the running downloader. yt-dlp never writes
cookies back to the browser.

Only a short fingerprint of the authentication cookies is ever logged (a hash
of their names and values), never a value. It shows in the debug log whether
the browser had other cookies than the downloader.
"""

import hashlib
import time
from collections.abc import Callable, Iterable
from enum import Enum
from http.cookiejar import Cookie, CookieJar
from threading import Lock
from typing import Any, cast

from yt_dlp.cookies import load_cookies

from .debug import Debug

# The cookies a Google session is made of, and the ones YouTube checks
AUTH_COOKIES: frozenset[str] = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "LOGIN_INFO",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "SIDCC",
    }
)
DOMAINS: tuple[str, ...] = ("youtube.com", "google.com")

Loader = Callable[..., CookieJar]
# yt-dlp's loader, typed here as it is used
LOAD_COOKIES: Loader = cast(Loader, load_cookies)


def fingerprint(cookies: Iterable[Cookie]) -> str:
    """A short hash of the authentication cookies and how many there are, e.g.
    "5 cookies, 3fa9c1d2". Names and values are hashed together, so a rotated
    value gives another fingerprint, and nothing can be read from it."""
    selected = sorted(
        (cookie.domain.lstrip("."), cookie.name, cookie.value or "")
        for cookie in cookies
        if cookie.name in AUTH_COOKIES
        and cookie.domain.lstrip(".").lower().endswith(DOMAINS)
    )
    digest = hashlib.sha256()
    for part in selected:
        digest.update("\0".join(part).encode("utf-8", "replace") + b"\n")
    count = len(selected)
    return f"{count} cookie{'s' if count != 1 else ''}, {digest.hexdigest()[:8]}"


class Refresh(Enum):
    NOT_NEEDED = "not needed"  # no rotation was noticed
    TOO_SOON = "too soon"  # refreshed shortly before, and it is still noticed
    REFRESHED = "refreshed"  # the browser had other cookies, now in use
    UNCHANGED = "unchanged"  # the browser has the same cookies: log in again
    FAILED = "failed"  # the browser cookies could not be read


class CookieWatch:
    """Tracks the cookies of one downloader, see the module description."""

    MIN_INTERVAL: float = 300.0  # seconds between two refreshes

    def __init__(
        self,
        slot: int,
        browser: str | None,
        clock: Callable[[], float] = time.time,
        loader: Loader = LOAD_COOKIES,
    ) -> None:
        self.__slot = slot
        self.__browser = browser
        self.__clock = clock
        self.__loader = loader
        self.__lock = Lock()
        self.__rotated = False
        self.__last: float | None = None  # the time of the last refresh

    # Called from the logger, on any request that noticed the rotation
    def note_rotation(self) -> None:
        with self.__lock:
            first = not self.__rotated
            self.__rotated = True
        if first:
            Debug.print(self.__slot, "Cookies: rotation noticed by yt-dlp")

    def rotated(self) -> bool:
        with self.__lock:
            return self.__rotated

    # For the debug log: the fingerprint of the cookies of the downloader
    def log_state(self, processor: object, label: str) -> None:
        if self.__browser is None:
            return
        try:
            jar = cast(CookieJar, cast(Any, processor).cookiejar)  # pyright: ignore[reportExplicitAny]
            Debug.print(self.__slot, f"Cookies {label}: {fingerprint(jar)}")
        except Exception as e:  # noqa: BLE001 - only a diagnostic
            name = type(e).__name__
            Debug.print(self.__slot, f"Cookies {label}: unavailable ({name})")

    def refresh(self, processor: object) -> Refresh:
        """Loads the current cookies of the browser into the downloader, when a
        rotation was noticed and no refresh was done within MIN_INTERVAL.
        Cookies of the browser replace those with the same name, others stay,
        such as the consent cookie yt-dlp sets itself."""
        if self.__browser is None:
            return Refresh.NOT_NEEDED
        with self.__lock:
            if not self.__rotated:
                return Refresh.NOT_NEEDED
            now = self.__clock()
            if self.__last is not None and 0 <= now - self.__last < self.MIN_INTERVAL:
                return Refresh.TOO_SOON
            self.__last = now
            self.__rotated = False
        try:
            jar = cast(CookieJar, cast(Any, processor).cookiejar)  # pyright: ignore[reportExplicitAny]
            before = fingerprint(jar)
            fresh = self.__loader(None, (self.__browser, None, None, None), processor)
            after = fingerprint(fresh)
            for cookie in list(fresh):
                jar.set_cookie(cookie)
        except Exception as e:  # noqa: BLE001 - a failed refresh must not stop the download
            Debug.print(self.__slot, f"Cookies: refresh failed ({type(e).__name__})")
            return Refresh.FAILED
        if before == after:
            Debug.print(
                self.__slot,
                f"Cookies: the browser has the same cookies ({after}), "
                + "they are no longer valid there either",
            )
            return Refresh.UNCHANGED
        Debug.print(self.__slot, f"Cookies: refreshed ({before} -> {after})")
        return Refresh.REFRESHED
