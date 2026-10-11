import http.client
import re
import socket
import ssl
import string
import urllib.error
from collections import deque
from collections.abc import Iterable
from enum import Enum
from typing import cast

from yt_dlp.networking.exceptions import HTTPError as NetworkHTTPError
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import (
    ContentTooShortError,
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    PostProcessingError,
    UnsupportedError,
    UserNotLive,
    YoutubeDLError,
)

from .ansi import ANSI
from .unicode import Unicode


class Error:
    """A yt-dlp error, parsed into its parts and classified for scheduling."""

    class Kind(Enum):
        RESTRICTION = "restriction"
        PERMANENT = "permanent"
        TRANSIENT = "transient"
        UNKNOWN = "unknown"

    class Category(Enum):
        # The value is the default summary text
        BOT_CHECK = "Bot check, sign-in required"
        RATE_LIMITED = "Rate limited"
        UNAVAILABLE = "Video unavailable"
        REMOVED = "Removed"
        PRIVATE = "Private video"
        MEMBERS_ONLY = "Members only"
        AGE_RESTRICTED = "Age restricted"
        GEO_BLOCKED = "Geo blocked"
        COPYRIGHT = "Copyright claim"
        DRM = "DRM protected"
        UNSUPPORTED = "Unsupported URL"
        NOT_FOUND = "Not found"
        FORMAT_UNAVAILABLE = "Format not available"
        FORBIDDEN = "HTTP 403 Forbidden"
        SERVER_ERROR = "Server error"
        NETWORK = "Network error"
        POSTPROCESSING = "Post-processing failed"
        UPCOMING = "Upcoming"
        LIVE_OFFLINE = "Not live"
        INTERNAL = "Internal error"
        UNKNOWN = "Unknown error"

        @property
        def kind(self) -> "Error.Kind":
            category = Error.Category
            if self in (
                category.BOT_CHECK,
                category.RATE_LIMITED,
                category.UNAVAILABLE,
            ):
                return Error.Kind.RESTRICTION
            if self in (
                category.REMOVED,
                category.PRIVATE,
                category.MEMBERS_ONLY,
                category.AGE_RESTRICTED,
                category.GEO_BLOCKED,
                category.COPYRIGHT,
                category.DRM,
                category.UNSUPPORTED,
                category.NOT_FOUND,
                category.FORMAT_UNAVAILABLE,
            ):
                return Error.Kind.PERMANENT
            if self in (
                category.FORBIDDEN,
                category.SERVER_ERROR,
                category.NETWORK,
                category.POSTPROCESSING,
                category.UPCOMING,
                category.LIVE_OFFLINE,
            ):
                return Error.Kind.TRANSIENT
            return Error.Kind.UNKNOWN

    SUMMARY_MAX: int = 60
    ELLIPSIS: str = "…"
    # Maximum number of exceptions inspected when walking a cause chain
    CHAIN_MAX: int = 8

    LEVEL_PREFIX: re.Pattern[str] = re.compile(r"\s*(?:ERROR|WARNING):(?:\s+|\Z)")
    PROVIDER_TAG: re.Pattern[str] = re.compile(
        r"\s*\[([A-Za-z][\w:.+-]{0,39})\](?:\s+|\Z)", re.ASCII
    )
    # The lookbehind avoids quadratic scans over long words
    URL: re.Pattern[str] = re.compile(r"(?<![\w+.-])[A-Za-z][\w+.-]*://\S+")
    TIME: re.Pattern[str] = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
    YOUTUBE_ID: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]{11}")
    # Characters that never appear in an id but do in prose/markup before a ": "
    IDENTITY_FORBIDDEN: frozenset[str] = frozenset("\"'`()[]{}<>,;=:/\\")
    IDENTITY_DENY: frozenset[str] = frozenset(
        {
            "also",
            "age-restricted",
            "cause",
            "caused by",
            "certificate",
            "code",
            "content-type",
            "debug",
            "deprecated",
            "deprecation",
            "description",
            "destination",
            "detail",
            "details",
            "download",
            "downloading",
            "e-mail",
            "errno",
            "error",
            "errors",
            "exception",
            "extracting",
            "failed",
            "failure",
            "ffmpeg",
            "ffprobe",
            "fragment",
            "got error",
            "hint",
            "http",
            "https",
            "info",
            "information",
            "interrupted",
            "ipv4",
            "ipv6",
            "members-only",
            "message",
            "note",
            "notes",
            "notice",
            "permissions",
            "po-token",
            "po_token",
            "post-processing",
            "postprocessing",
            "reason",
            "request",
            "requested",
            "response",
            "restriction",
            "retrying",
            "see",
            "sorry",
            "status",
            "tip",
            "traceback",
            "unable",
            "unavailable",
            "unsupported",
            "url",
            "user-agent",
            "utf-8",
            "warning",
            "warnings",
            "x-forwarded-for",
            "youtube-dl",
            "yt-dlp",
        }
    )

    # Cleanup of the message body
    NESTED_PREFIX: re.Pattern[str] = re.compile(
        r"^[ \t]*(?:(?:ERROR|WARNING):|Got error:)(?:[ \t]+|$)", re.MULTILINE
    )
    BUG_REPORT: re.Pattern[str] = re.compile(
        r"\s*[;,]?\s*please report this issue on\b.*?"
        + r"(?:latest version using\s+yt-dlp\s+-U\b|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    CAUSED_BY: str = "(caused by "
    # Sentences with command line hints, which are of no use in a terminal frontend
    HINT_LEADS: tuple[str, ...] = (
        r"Use\s+--",
        r"(?:Also\s+)?see\s+[A-Za-z][\w+.-]*://\S+",
        r"For more information,?\s+(?:refer to|see)\b",
        r"It is recommended to use\b",
        r"You might want to use a VPN\b",
    )
    HINT: re.Pattern[str] = re.compile(
        r"(?:^|(?<=[.!?;,]))[ \t]*(?:"
        + "|".join(HINT_LEADS)
        + r").*?(?:[.!?](?=\s|$)|$)",
        re.IGNORECASE | re.MULTILINE,
    )
    SENTENCE_END: re.Pattern[str] = re.compile(r"[.!?](?=\s)")
    # URLs are of no use in a short summary, nor a word leading to one
    SUMMARY_URL: re.Pattern[str] = re.compile(
        r"(?:\s+(?:at|on|from|see|via|visit))?\s*(?<![\w+.-])[A-Za-z][\w+.-]*://\S+",
        re.IGNORECASE,
    )

    # Details for summaries
    HTTP_STATUS: re.Pattern[str] = re.compile(r"\bHTTP Error (\d{3})\b", re.IGNORECASE)
    PREMIERES_IN: re.Pattern[str] = re.compile(
        r"\bPremieres?\s+in\s+(.+?)(?=[.!?](?:\s|$)|$)", re.IGNORECASE
    )
    STARTS_IN: re.Pattern[str] = re.compile(
        r"\b(?:will\s+(?:begin|start)|begins|starts)\s+in\s+(.+?)(?=[.!?](?:\s|$)|$)",
        re.IGNORECASE,
    )

    # Ordered, the first match wins, so specific patterns come before generic
    # ones. Matched case-insensitively anywhere in the cleaned message.
    CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], Category], ...] = tuple(
        (re.compile("|".join(patterns), re.IGNORECASE), category)
        for patterns, category in (
            (
                (
                    r"Sign in to confirm your age",
                    r"confirm your age",
                    r"age[- ]restricted",
                    r"inappropriate for some users",
                ),
                Category.AGE_RESTRICTED,
            ),
            (
                (
                    r"Sign in to confirm you(?:['’]re| are) not a bot",
                    r"not a bot\b",
                    r"suspected as a bot",
                    r"captcha",
                    r"Sign in to confirm",
                ),
                Category.BOT_CHECK,
            ),
            (
                (r"HTTP Error 429\b", r"Too Many Requests", r"rate[- ]limited"),
                Category.RATE_LIMITED,
            ),
            ((r"HTTP Error 403\b",), Category.FORBIDDEN),
            ((r"HTTP Error (?:404|410)\b",), Category.NOT_FOUND),
            ((r"HTTP Error 5\d\d\b",), Category.SERVER_ERROR),
            (
                (
                    r"members[- ]only",
                    r"Join this channel to get access",
                    r"available to this channel['’]s members",
                ),
                Category.MEMBERS_ONLY,
            ),
            (
                (r"Private video", r"This (?:video|playlist) is private"),
                Category.PRIVATE,
            ),
            ((r"copyright",), Category.COPYRIGHT),
            (
                (
                    r"removed by the uploader",
                    r"account associated with this video has been terminated",
                    r"(?:video|channel|account) (?:has been|was) (?:removed|terminated)",
                    r"no longer available",
                ),
                Category.REMOVED,
            ),
            (
                (
                    r"not available in your (?:country|region)",
                    r"made this video available in your country",
                    r"blocked it in your country",
                    r"geo[- ]?restrict",
                    r"from your location",
                ),
                Category.GEO_BLOCKED,
            ),
            ((r"DRM[- ]protected",), Category.DRM),
            (
                (
                    r"Premieres? in\b",
                    r"Premiere will begin",
                    r"will begin in\b",
                    r"live event will begin",
                    r"\bscheduled\b",
                    r"is upcoming",
                ),
                Category.UPCOMING,
            ),
            ((r"is offline", r"not currently live"), Category.LIVE_OFFLINE),
            # After the specific "content isn't available" variants above
            ((r"try again later",), Category.RATE_LIMITED),
            (
                (
                    r"Video unavailable",
                    r"This content isn['’]t available",
                    r"content is not available",
                ),
                Category.UNAVAILABLE,
            ),
            (
                (r"Requested format is not available", r"No video formats found"),
                Category.FORMAT_UNAVAILABLE,
            ),
            ((r"Unsupported URL",), Category.UNSUPPORTED),
            (
                (r"Post-?processing", r"ffmpeg", r"ffprobe", r"Conversion failed"),
                Category.POSTPROCESSING,
            ),
            (
                (
                    r"Unable to download (?:webpage|API page|video data)",
                    r"urlopen error",
                    r"timed out",
                    r"Connection (?:reset|refused|aborted)",
                    r"Remote end closed connection",
                    r"Name or service not known",
                    r"nodename nor servname",
                    r"Temporary failure in name resolution",
                    r"getaddrinfo failed",
                    r"Network is unreachable",
                    r"No route to host",
                    r"IncompleteRead",
                    r"\bSSL",
                    r"Did not get any data blocks",
                    r"fragment (?:\d+ )?not found",
                ),
                Category.NETWORK,
            ),
            # HTTP reason phrases, case sensitive so "ffmpeg not found" is no match
            ((r"\b(?-i:Forbidden)\b",), Category.FORBIDDEN),
            ((r"does not exist", r"\b(?-i:Not Found)\b"), Category.NOT_FOUND),
        )
    )

    # Exception types that identify a category regardless of the text
    NETWORK_TYPES: tuple[type[BaseException], ...] = (
        ContentTooShortError,
        TransportError,
        TimeoutError,
        ConnectionError,
        socket.gaierror,
        ssl.SSLError,
        urllib.error.URLError,
        http.client.HTTPException,
    )

    def __init__(self, text: str, known_ids: Iterable[str] = ()) -> None:
        raw = ANSI.remove(text)
        provider, identity, message, first_line = Error.__clean(
            raw, Error.__ids(known_ids), heuristic=True
        )
        category, status = Error.__classify(message)
        self.raw: str = raw
        self.provider: str | None = provider
        self.identity: str | None = identity
        self.message: str = message
        self.category: Error.Category = category
        self.summary: str = Error.__summarize(category, message, first_line, status)

    @staticmethod
    def from_exception(e: BaseException, known_ids: Iterable[str] = ()) -> "Error":
        known = Error.__ids(known_ids)
        chain = Error.__chain(e)
        primary = e
        if isinstance(e, DownloadError):
            primary = Error.__exc_info_value(e) or e

        video_id = Error.__first_attribute(chain, "video_id")
        ie = Error.__first_attribute(chain, "ie")
        if video_id is not None:
            known = known | {video_id}

        top_text = Error.__text_of(e)
        error = Error(top_text, known)
        if not error.raw.strip():
            error.raw = type(e).__name__
            error.message = error.raw

        if Error.__is_internal(primary):
            name = type(primary).__name__
            text = " ".join(Error.__text_of(primary).split())
            error.provider = ie
            error.identity = video_id
            error.message = f"{name}: {text}" if text else name
            error.category = Error.Category.INTERNAL
            error.summary = Error.__cap(f"Internal error ({name})")
            return error

        body = Error.__text_of(primary)
        heuristic = True
        orig_msg = Error.__attribute(primary, "orig_msg")
        if isinstance(primary, ExtractorError) and isinstance(orig_msg, str):
            # The raw message, without the "[ie] id: " prefix or bug report text
            body, heuristic = orig_msg, False
        elif primary is not e:
            # The DownloadError text carries context, e.g. "Postprocessing: "
            body = top_text
        provider, identity, message, first_line = Error.__clean(
            ANSI.remove(body), known, heuristic=heuristic
        )
        if not message:
            message, first_line = error.message, error.message
        error.provider = ie or provider
        error.identity = video_id or identity

        type_category, status, countries = Error.__type_category(chain)
        text_category, text_status = Error.__classify(message)
        if text_category is Error.Category.UNKNOWN:
            for link in chain:
                link_text = Error.__text_of(link)
                link_orig = Error.__attribute(link, "orig_msg")
                if isinstance(link, ExtractorError) and isinstance(link_orig, str):
                    link_text = link_orig
                _, _, link_message, _ = Error.__clean(
                    ANSI.remove(link_text), known, heuristic=False
                )
                text_category, text_status = Error.__classify(link_message)
                if text_category is not Error.Category.UNKNOWN:
                    break
        if type_category is not None:
            category = type_category
        else:
            category, status = text_category, text_status

        error.message = message
        error.category = category
        error.summary = Error.__summarize(
            category, message, first_line, status, countries
        )
        return error

    @property
    def kind(self) -> "Error.Kind":
        return self.category.kind

    @property
    def restriction(self) -> bool:
        return self.kind is Error.Kind.RESTRICTION

    @property
    def display(self) -> str:
        """The summary with the identity, short enough for the channels file."""
        return f"{self.identity}: {self.summary}" if self.identity else self.summary

    @staticmethod
    def strip_level(text: str) -> str:
        text = ANSI.remove(text)
        while match := Error.LEVEL_PREFIX.match(text):
            text = text[match.end() :]
        return text.lstrip()

    @staticmethod
    def split_provider(text: str, *, all_tags: bool = True) -> tuple[str | None, str]:
        """Splits leading "[tag]" groups off the text.

        The provider is the first tag. With all_tags, immediately following
        tags are also removed from the returned text.
        """
        match = Error.PROVIDER_TAG.match(text)
        if match is None:
            return None, text
        provider, end = match.group(1), match.end()
        while all_tags and (match := Error.PROVIDER_TAG.match(text, end)):
            end = match.end()
        return provider, text[end:]

    @staticmethod
    def split_identity(
        text: str, known_ids: Iterable[str] = ()
    ) -> tuple[str | None, str]:
        return Error.__split_identity(text, Error.__ids(known_ids), heuristic=True)

    @staticmethod
    def __split_identity(
        text: str, known: frozenset[str], heuristic: bool
    ) -> tuple[str | None, str]:
        masked = Error.__mask(text)
        index = masked.find(": ")
        if index <= 0:
            return None, text
        prefix = text[:index]
        rest = text[index + 2 :].lstrip()
        if not rest:
            return None, text
        if prefix in known:
            return prefix, rest
        # A prefix overlapping a URL or a time is never an identity
        if heuristic and masked[:index] == prefix and Error.__looks_like_id(prefix):
            return prefix, rest
        return None, text

    @staticmethod
    def __mask(text: str) -> str:
        def fill(match: re.Match[str]) -> str:
            return "\0" * len(match.group())

        return Error.TIME.sub(fill, Error.URL.sub(fill, text))

    @staticmethod
    def __looks_like_id(token: str) -> bool:
        if not 1 <= len(token) <= 64:
            return False
        if any(c.isspace() or c in Error.IDENTITY_FORBIDDEN for c in token):
            return False
        if token.casefold() in Error.IDENTITY_DENY:
            return False
        return (
            Error.YOUTUBE_ID.fullmatch(token) is not None
            or token.startswith("@")
            or any(c in string.digits or c in "_-" for c in token)
        )

    @staticmethod
    def __ids(known_ids: Iterable[str]) -> frozenset[str]:
        return frozenset(i for i in known_ids if i)

    @staticmethod
    def __clean(
        raw: str, known: frozenset[str], heuristic: bool
    ) -> tuple[str | None, str | None, str, str]:
        """Returns provider, identity, message and the first line of the message."""
        text = Error.strip_level(raw)
        provider, text = Error.split_provider(text)
        identity, text = Error.__split_identity(text, known, heuristic)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        while (stripped := Error.NESTED_PREFIX.sub("", text)) != text:
            text = stripped
        text = Error.BUG_REPORT.sub("", text)
        text = Error.__remove_caused_by(text)
        text = Error.HINT.sub("", text)
        lines = [" ".join(line.split()) for line in text.split("\n")]
        lines = [line for line in lines if line]
        message = " ".join(lines)
        if not message:
            # Cleanup removed everything, so fall back to the stripped raw text
            message = (
                " ".join(Error.strip_level(raw).split()) or " ".join(raw.split()) or raw
            )
            return provider, identity, message, message
        return provider, identity, message, lines[0]

    @staticmethod
    def __remove_caused_by(text: str) -> str:
        start = text.find(Error.CAUSED_BY)
        if start < 0:
            return text
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    return text[:start].rstrip() + text[index + 1 :]
        return text[:start].rstrip()

    @staticmethod
    def __classify(message: str) -> tuple["Error.Category", int | None]:
        for pattern, category in Error.CATEGORY_PATTERNS:
            if pattern.search(message):
                status = None
                if category in (Error.Category.SERVER_ERROR, Error.Category.NOT_FOUND):
                    for match in Error.HTTP_STATUS.finditer(message):
                        code = int(match.group(1))
                        if Error.__status_category(code) is category:
                            status = code
                            break
                return category, status
        return Error.Category.UNKNOWN, None

    @staticmethod
    def __status_category(status: int | None) -> "Error.Category | None":
        if status is None:
            return None
        if status == 429:
            return Error.Category.RATE_LIMITED
        if status == 403:
            return Error.Category.FORBIDDEN
        if status in (404, 410):
            return Error.Category.NOT_FOUND
        if 500 <= status <= 599:
            return Error.Category.SERVER_ERROR
        return None

    @staticmethod
    def __type_category(
        chain: list[BaseException],
    ) -> tuple["Error.Category | None", int | None, list[str] | None]:
        for exc in chain:
            if isinstance(exc, (NetworkHTTPError, urllib.error.HTTPError)):
                name = "status" if isinstance(exc, NetworkHTTPError) else "code"
                status = Error.__int(Error.__attribute(exc, name))
                if (category := Error.__status_category(status)) is not None:
                    return category, status, None
            elif isinstance(exc, GeoRestrictedError):
                countries = Error.__attribute(exc, "countries")
                codes: list[str] | None = None
                if isinstance(countries, (list, tuple)):
                    items = cast("Iterable[object]", countries)
                    codes = [c for c in items if isinstance(c, str)]
                return Error.Category.GEO_BLOCKED, None, codes
            elif isinstance(exc, UserNotLive):
                return Error.Category.LIVE_OFFLINE, None, None
            elif isinstance(exc, UnsupportedError):
                return Error.Category.UNSUPPORTED, None, None
            elif isinstance(exc, PostProcessingError):
                return Error.Category.POSTPROCESSING, None, None
            elif isinstance(exc, Error.NETWORK_TYPES):
                return Error.Category.NETWORK, None, None
        return None, None, None

    @staticmethod
    def __is_internal(e: BaseException) -> bool:
        """Whether the exception is not from yt-dlp and not a network/OS error."""
        if isinstance(e, (YoutubeDLError, OSError, http.client.HTTPException)):
            return False
        module = type(e).__module__
        return not (module == "yt_dlp" or module.startswith("yt_dlp."))

    @staticmethod
    def __chain(e: BaseException) -> list[BaseException]:
        chain: list[BaseException] = []
        seen: set[int] = set()
        queue: deque[BaseException] = deque([e])
        while queue and len(chain) < Error.CHAIN_MAX:
            current = queue.popleft()
            if id(current) in seen:
                continue
            seen.add(id(current))
            chain.append(current)
            queue.extend(Error.__links(current))
        return chain

    @staticmethod
    def __links(e: BaseException) -> list[BaseException]:
        links: list[BaseException] = []
        cause = Error.__attribute(e, "cause")
        if isinstance(cause, BaseException):
            links.append(cause)
        if e.__cause__ is not None:
            links.append(e.__cause__)
        if e.__context__ is not None and not e.__suppress_context__:
            links.append(e.__context__)
        if (value := Error.__exc_info_value(e)) is not None:
            links.append(value)
        return links

    @staticmethod
    def __exc_info_value(e: BaseException) -> BaseException | None:
        info = Error.__attribute(e, "exc_info")
        if isinstance(info, tuple):
            items = cast("tuple[object, ...]", info)
            if len(items) >= 2 and isinstance(items[1], BaseException):
                return items[1]
        return None

    @staticmethod
    def __first_attribute(chain: list[BaseException], name: str) -> str | None:
        for exc in chain:
            if isinstance(exc, ExtractorError):
                value = Error.__attribute(exc, name)
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def __attribute(obj: object, name: str) -> object:
        try:
            value: object = getattr(obj, name, None)
        except Exception:  # noqa: BLE001 - attributes of foreign objects may raise
            return None
        return value

    @staticmethod
    def __int(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    @staticmethod
    def __text_of(e: BaseException) -> str:
        try:
            return str(e)
        except Exception:  # noqa: BLE001 - a broken __str__ must not escape
            return ""

    @staticmethod
    def __summarize(
        category: "Error.Category",
        message: str,
        first_line: str,
        status: int | None,
        countries: list[str] | None = None,
    ) -> str:
        summary = category.value
        match category:
            case Error.Category.UNKNOWN:
                summary = Error.__first_sentence(first_line) or category.value
            case Error.Category.SERVER_ERROR | Error.Category.NOT_FOUND if status:
                summary = f"{category.value} (HTTP {status})"
            case Error.Category.UPCOMING:
                if found := Error.PREMIERES_IN.search(message):
                    summary = f"Premieres in {found.group(1).strip(' ,;:')}"
                elif found := Error.STARTS_IN.search(message):
                    summary = f"Starts in {found.group(1).strip(' ,;:')}"
            case Error.Category.GEO_BLOCKED if countries:
                listed = ", ".join(countries)
                if len(countries) <= 5 and len(listed) <= 20:
                    summary = f"{category.value} (only {listed})"
            case _:
                pass
        if url := Error.SUMMARY_URL.search(summary):
            # What follows a URL rarely makes sense without it
            summary = summary[: url.start()]
        summary = " ".join(summary.split()).rstrip(" ,;:-") or category.value
        return Error.__cap(summary)

    @staticmethod
    def __first_sentence(line: str) -> str:
        if match := Error.SENTENCE_END.search(line):
            line = line[: match.end()]
        line = line.strip()
        if line.endswith(".") and not line.endswith(".."):
            line = line[:-1].rstrip()
        return line

    @staticmethod
    def __cap(text: str) -> str:
        """Caps the text to SUMMARY_MAX display chars, cutting at a word if possible."""
        if Unicode.len(text) <= Error.SUMMARY_MAX:
            return text
        budget = Error.SUMMARY_MAX - Unicode.len(Error.ELLIPSIS)
        width, end = 0, 0
        for index, char in enumerate(text):
            char_width = Unicode.len(char)
            if width + char_width > budget:
                break
            width += char_width
            end = index + 1
        cut = text[:end]
        if end < len(text) and not text[end].isspace():
            space = cut.rfind(" ")
            if space >= len(cut) // 2:
                cut = cut[:space]
        cut = cut.rstrip(" ,;:-") or text[:end]
        return cut + Error.ELLIPSIS
