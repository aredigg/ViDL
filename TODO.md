# TODO List


Some of the suggestions are not relevant or necessary.

== Created by OpenAI Sol ==

## Critical defects

- [ ] **`slot.py` — Catch worker exceptions and restore slot state in `finally`.**  
  An unhandled exception kills the slot thread, leaves its channel active, and can
  deadlock the coordinator. Report the exception to the view/coordinator and always
  clear `self.__channel`.

- [ ] **`coordinator.py` — Always shut down the view, including early returns.**  
  Missing channel files and empty channel lists return before `View.halt()`. The
  non-daemon view thread can keep the process alive and leave the terminal in cbreak
  and alternate-screen mode. Put shutdown in a `finally` block.

## Channel and playlist processing

- [ ] **`channel.py` — Aggregate child-channel success correctly.**  
  A playlist currently returns success even when every child fails, causing the
  parent’s last-download date to be updated. Track whether at least one child
  succeeded, or distinguish “playlist successfully inspected” from “media
  downloaded.”

- [ ] **`channel.py` — Decide how a partially successful playlist should be recorded.**  
  Define separate outcomes such as `DOWNLOADED`, `DEFERRED`, `REJECTED`,
  `NO_NEW_ITEMS`, and `ERROR`. A Boolean is becoming too ambiguous for this flow.

- [ ] **`channel.py` — Validate fallback URLs before downloading.**  
  `original_url` can be empty. Fall back to `webpage_url`, and reject the item with a
  clear error if both are missing.

- [ ] **`channel.py` — Guard timestamp conversion.**  
  Invalid or extreme persisted timestamps can make `datetime.fromtimestamp()` raise
  `OverflowError`, `OSError`, or `ValueError`.

## Configuration and persistence

- [ ] **`config.py` — Resolve relative paths against the configuration file.**  
  When a custom configuration path is supplied, channel, archive, output, and debug
  paths still effectively depend on the process working directory. Use the config
  file’s parent directory as the base.

- [ ] **`config.py` — Validate settings after loading.**  
  Validate at least:
  - `slots >= 0`
  - `sleep_interval >= 0`
  - resolution and duration values are nonnegative
  - output/config paths have the expected type
  - channel and archive filenames are nonempty

- [ ] **`channel.py` — Make channel-file writes atomic.**  
  Writing directly to the live state file risks truncation after a crash. Write to a
  temporary sibling, flush and optionally `fsync`, then use `os.replace()`.

- [ ] **`channel.py` — Synchronize saves with worker updates.**  
  The coordinator serializes channels while slot threads mutate dates, errors, and
  names. Protect state with a lock or have workers send state changes back to the
  coordinator for single-threaded persistence.

- [ ] **`channel.py` — Use proper delimited-file parsing.**  
  Names and errors containing semicolons or newlines corrupt the current format.
  Use Python’s `csv` module with `delimiter=";"`, including quoting and explicit
  encoding.

## Slot and coordinator lifecycle

- [ ] **`slot.py` — Manage `YoutubeDL` as a context manager.**  
  Ensure resources are cleaned after each channel:
  ```python
  with self.__setup() as processor:
      channel.download(
          self.__index,
          processor,
          self.__view_queue,
      )
  ```

- [ ] **`slot.py` / `hook.py` — Define how cancellation exceptions are handled.**  
  `DownloadCancelled` may escape yt-dlp and kill the worker. Catch it explicitly,
  treat it as a normal halt, and avoid presenting it as a download failure.

- [ ] **`coordinator.py` — Avoid saving the complete channel file every second while busy.**  
  Mark state dirty and save on completion, at a lower periodic interval, and during
  shutdown.

- [ ] **`coordinator.py` — Surface worker failures centrally.**  
  A slot can become non-alive, but `ready()` merely returns false. Detect dead slot
  threads and stop with a meaningful error rather than waiting indefinitely.

- [ ] **`main.py` — Catch unexpected exceptions while still restoring the terminal.**  
  Cleanup should happen before printing the final status. Consider printing
  `"Done."` only on success and a different message for nonzero exit status.

## Item selection and filtering

- [ ] **`item.py` — Implement `minimum_duration`.**  
  The configuration option exists but is never checked. Decide whether unknown
  duration (`0`) is accepted, deferred, or rejected.

- [ ] **`item.py` — Clarify whether unknown resolution should pass the minimum-resolution filter.**  
  Current behavior accepts `height == 0`. This may be deliberate for live or
  incompletely processed metadata, but it should normally produce `DEFERRED` rather
  than being treated as accepted.

- [ ] **`item.py` — Replace Boolean filtering with a reasoned decision result.**  
  `valid_format()` cannot distinguish vertical media, low resolution, unknown
  format, and short duration. Return a decision and reason, for example:
  ```python
  class Decision(Enum):
      ACCEPT = "accept"
      DEFER = "defer"
      REJECT = "reject"
  ```

- [ ] **`item.py` — Base displayed media details on the selected/requested formats.**  
  `enumerate_best_format()` chooses one MP4 format independently from yt-dlp’s
  actual selector. It can display video-only audio information or a format that
  will not be downloaded. Prefer `requested_formats`, `requested_downloads`, or the
  final hook information.

- [ ] **`item.py` — Improve best-format ordering if it remains necessary.**  
  The key ignores bitrate, codec preference, and whether the format contains video
  or audio. It can choose a nonrepresentative format among equal-resolution options.

- [ ] **`item.py` — Harden `get_item()` against `None` and malformed extractor data.**  
  It uses `info.get(...)` after partially accounting for `info is None`.

## View and terminal behavior

- [ ] **`view.py` — Render on a fixed cadence instead of only when the queue is empty.**  
  Under a continuous stream of progress messages, the queue may never become empty,
  so nothing is rendered. Drain a bounded batch, then redraw based on monotonic time.

- [ ] **`view.py` — Avoid `KeyError` for unknown postprocessors.**
  ```python
  label = processes.get(item.processor, item.processor or "Unknown")
  ```

- [ ] **`view.py` — Clamp countdown timers at zero or transition state when expired.**  
  `timer()` uses an absolute-value formatter, so after the deadline it starts
  counting upward again.

- [ ] **`view.py` — Always clear or print long lines.**  
  Many methods skip printing when content is too long, leaving stale text on screen.
  Let `Terminal.print()` trim it, or clear the full interior line before drawing.

- [ ] **`view.py` — Implement terminal-resize reflow or explicitly document fixed startup sizing.**  
  The size is read only at startup. Slots can become clipped or misplaced after a
  resize.

- [ ] **`view.py` — Handle display-slot overflow.**  
  More worker slots than visible grid cells receive no position and are silently
  invisible. Either cap worker count, add paging, or reflow.

- [ ] **`view.py` — Remove or display `__top_name_last_dl`.**  
  It is assigned but never rendered.

- [ ] **`terminal.py` — Restore terminal state robustly.**  
  Put terminal restoration in a `finally` path so failure while writing the
  alternate-screen escape cannot leave cbreak enabled.

- [ ] **`terminal.py` — Decide and document platform support.**  
  `termios` and `tty` make the application POSIX-only, and redirected stdin can
  make `tcgetattr()` fail. Detect non-TTY execution and offer a plain-output mode.

## ANSI and Unicode

- [ ] **`ansi.py` — Rewrite `trim()` as token-aware trimming.**  
  Escape positions are recorded in the original string but reinserted into the
  escape-stripped string, so positions after escape sequences are incorrect.
  Tokenize ANSI sequences and visible grapheme clusters while accumulating width.

- [ ] **`ansi.py` / `unicode.py` — Use an established terminal-width implementation.**  
  The current implementation miscounts joined emoji, regional flags, keycaps, and
  many grapheme sequences. A library such as `wcwidth`, combined with grapheme
  segmentation where necessary, will be more reliable.

- [ ] **`ansi.py` — Include OSC sequences if `remove()` can receive title or notification codes.**  
  The current regex removes CSI sequences but not OSC sequences generated by
  `title_bar()` and `notify()`.

- [ ] **`ansi.py` — Validate color strings without `assert`.**  
  Assertions can be disabled. Raise `ValueError` for invalid length or non-hex
  characters.

- [ ] **`ansi.py` — Avoid shadowing built-ins.**  
  Rename parameters and methods such as `hex`, `len`, and `print` where practical.
  This is not a runtime defect, but it makes debugging and type checking harder.

## Logger and messages

- [ ] **`logger.py` — Implement or remove `__temp_writer()`.**  
  The debug configuration currently has no effect. Use the configured debug file,
  a lock, UTF-8, and normal logging rotation if persistent logs are desired.

- [ ] **`logger.py` — Preserve nested provider/target details in error logs.**  
  The second parse extracts `target`, but the temporary log omits it.

- [ ] **`logger.py` — Review truthiness checks on parsed values.**  
  An empty valid value is treated the same as no match by expressions such as
  `if match := ...`. Use `is not None` if an empty target remains meaningful.

- [ ] **`message.py` — Make `Message.body` consistent with its kind.**  
  The broad optional union requires runtime `isinstance()` checks everywhere.
  Consider separate typed message classes or a discriminated union.

- [ ] **`message.py` — Remove obsolete message-design comments or turn them into tracked design documentation.**

## Planned reject archive and resolution hold

- [ ] **Filtering/persistence — Introduce explicit permanent and temporary outcomes.**  
  At minimum distinguish:
  - downloaded
  - permanent reject
  - temporary defer
  - already archived
  - extraction error
  - no new media

- [ ] **Reject archive — Store stable extractor identity, not only URLs.**  
  Record the extractor key and media ID in a format compatible with your lookup
  strategy. URLs can change, redirect, or contain temporary parameters.

- [ ] **Reject archive — Record the rejection reason and rule version.**  
  Examples: vertical, below minimum duration, age restriction, unsupported format,
  or resolution hold expired. A rule version lets you reconsider old rejects after
  configuration changes.

- [ ] **Reject archive — Do not archive transient extraction failures as rejects.**  
  Network failures, unavailable metadata, incomplete formats, and rate limits should
  remain retryable.

- [ ] **Resolution hold — Persist `first_seen`, target resolution, best observed resolution, and deadline.**  
  Do not calculate the one-week hold solely from the upload timestamp; timestamps
  may be absent or inaccurate. `first_seen` provides deterministic retry behavior.

- [ ] **Resolution hold — Recheck deferred videos until the deadline.**  
  If the target resolution appears, download immediately. After the deadline,
  explicitly decide whether to accept the best available format or permanently
  reject it.

- [ ] **Resolution hold — Keep deferred items out of the permanent download/reject archives.**  
  Otherwise the normal archive logic can prevent the scheduled retry.

- [ ] **Resolution hold — Treat unknown format metadata as deferred rather than low resolution.**  
  A zero height may mean extraction has not yet provided formats, not that the video
  is low resolution.

- [ ] **State storage — Consider SQLite for scheduling and reject state.**  
  The standard-library `sqlite3` module provides transactions, atomic updates, and
  queries for due retries. This becomes safer than adding several concurrently
  rewritten semicolon-delimited files.

## Tests and tooling

- [ ] **Project-wide — Add a compile check immediately.**  
  It would catch both invalid `except` clauses:
  ```bash
  python -m compileall .
  ```

- [ ] **Project-wide — Add Ruff or equivalent static checks.**  
  Enable checks for syntax, unused state, shadowed built-ins, ambiguous exception
  handling, and excessive complexity.

- [ ] **`coordinator.py` — Add a regression test for “all slots busy, then one completes.”**  
  This should prove that the scheduler does not remain stuck with `channel=None`.

- [ ] **`channel.py` — Test yt-dlp-style return code `0` as success.**

- [ ] **`channel.py` — Test repeated playlist extraction.**  
  Ensure subchannels are not duplicated and mixed child outcomes are represented
  correctly.

- [ ] **Lifecycle — Test missing and empty channel files.**  
  Assert that the view thread exits and terminal cleanup executes.

- [ ] **Lifecycle — Test exceptions from extraction, download, logger, and hooks.**  
  The channel must become inactive, the slot must become reusable or fail visibly,
  and shutdown must still complete.

- [ ] **Persistence — Test configuration and channel-state round trips.**  
  Include Unicode, semicolons, newlines, `None`, quoted numeric strings, and errors.

- [ ] **Filtering — Add table-driven tests for vertical, duration, cutoff, unknown metadata, permanent reject, and one-week resolution defer behavior.**

The first work I would do is: fix the two syntax errors, repair coordinator scheduling, guarantee thread/terminal cleanup, correct yt-dlp return-code handling, and add exception-safe state restoration. Those issues can currently prevent startup, falsely mark successful downloads as failures, or hang the entire application.

== Created by Anthropic Opus 5 ==

# Code Review — Checklist

## 🔴 Critical (won't run / crashes threads)

- [ ] **`view.py`** — the whole `__run` loop has no top-level `try/except`. One unhandled exception silently kills rendering while downloads continue. Wrap the message dispatch in `try/except Exception` and surface the error into a slot/status line.

## 🟠 Logic bugs

- [ ] **`channel.py`** — for playlists `__extract` returns `True` unconditionally, so the parent's download date is bumped even if every sub-channel failed. Return `any(...)`/`all(...)` of the sub results (confirm intent — see questions).
- [ ] **`channel.py`** — unbounded recursion depth for nested playlists (playlist → playlist → …). Add a `max_sub_level` guard from `Config`.
- [ ] **`coordinator.py`** — `Channel` state is mutated from slot threads while the coordinator serialises it → torn writes. Add a lock around `write()`/mutators, or snapshot under a lock before saving.
- [ ] **`view.py`** — `Logger` emits `SleepMessage` with `provider` = extractor name (e.g. `youtube`), but `__update_sleep` only handles `"download"`, `"channel"`, `"sub_channel"`, so yt-dlp's own sleep intervals never show. Either broaden the check or normalise providers in `Logger`.
- [ ] **`view.py`** — `"sub_channel"` provider is handled but **nothing ever sends it**; `Channel` always sends `"channel"`, so sub-channel top-name reset is unreachable. Send the real provider from `Channel.__extract` based on `__sub_level`.
- [ ] **`view.py`** — `__assign_position` silently drops slots when `rows * columns` is exhausted (already flagged by your TODO). At minimum log/mark overflow slots so they aren't invisible.
- [ ] **`util.py`** — `format_seconds(include_seconds=True, two_parts=True)` with `hr > 0` falls through the `if` block and returns rounded `HH:MM`, dropping seconds. Verify intent; if unintended, return `f"{hr:02}:{mn:02}:{sc:02}"`.

## 🟡 Robustness / data integrity

- [ ] **`channel.py`** — the channels file is hand-rolled CSV: any `;` in a title corrupts the row, and `write()` silently returns `""` (dropping the channel from the file = **data loss**). Switch to `csv.reader/writer` with proper quoting, or escape `;`.
- [ ] **`channel.py`** — `None` fields are written as the literal string `"None"` and read back as the string `"None"` (hence the `url == "None"` check). Write empty fields and convert `"" -> None` on load.
- [ ] **`channel.py`** — errors from yt-dlp reach the View but are never written back to `__last_error`; the channels file only ever records internal errors. Wire `Logger.error` → slot → channel.
- [ ] **`config.py`** — `save()` catches `FileExistsError` (never raised by `open(..., "w")`) but not `PermissionError`/`IsADirectoryError`/`OSError`. Catch `OSError`.
- [ ] **`config.py`** — no validation: `Paths.output` defaults to `None` and is passed straight to yt-dlp. Fail fast with a clear message if required settings are missing.
- [ ] **`config.py`** — `initialize()` sets `Paths.config = os.path.curdir` but `Config.ini` may be an absolute path elsewhere; derive `path` from `os.path.dirname(os.path.abspath(config_ini))`.
- [ ] **`main.py`** — no argument parsing/validation. Use `argparse` (`--config`, `--slots`, `--debug`) and validate the config path exists before `Coordinator()` starts threads.
- [ ] **`main.py`** — the error `print` may land inside/after the alternate screen depending on teardown order. Buffer errors and print them after `View.join()`.
- [ ] **`logger.py`** — `__temp_writer` is a no-op and `Config.settings["Debug"]` is never used. Implement a real file logger gated on `Debug.active`, or delete the dead code.
- [ ] **`ansi.py`** — `assert len(hex) == 6` disappears under `python -O`. Raise `ValueError`.
- [ ] **`terminal.py` / `view.py`** — `input_queue` is created and threaded through three classes but never read. Either implement key handling (`q` to quit, arrows to page slots) — you already have cbreak mode — or remove it.
- [ ] **Signals** — `KeyboardInterrupt` is only caught in the coordinator loop; a `Ctrl+C` during `slot.join()` leaves the terminal in the alternate screen. Install a `SIGINT`/`SIGTERM` handler that sets a shared halt event.

## 🔵 Refactoring

- [ ] **`view.py`** — `View.Slot` is ~350 lines mixing state, layout and rendering. Extract a `SlotRenderer` (pure: state → list of `(row, col, text)`) so rendering becomes unit-testable without a TTY.
- [ ] **`view.py`** — replace the `isinstance` ladder in `__run` with a dispatch dict `{(Msg.INIT, InitMessage): handler, ...}` or `functools.singledispatchmethod`.
- [ ] **`channel.py`** — `Channel` currently does persistence, traversal, filtering, downloading, sleeping *and* view messaging. Split into `ChannelRecord` (data + serialisation) and `ChannelJob` (extract/download/report).
- [ ] **`channel.py`** — `set_epoch_cutoff` both mutates and returns; rename to `epoch_cutoff()` as a cached property.
- [ ] **Typing** — add type hints and run `mypy`/`pyright`; `Config.settings` as a nested `dict[str, dict[str, Any]]` defeats all checking. Consider a typed `dataclass` config with `tomllib` instead of a hand-rolled INI parser (stdlib `configparser` also handles this).
- [ ] **Dead code** — `View.Slot.__progress_meter_long`, `get_id`, `get_top`, `__top_name_last_dl` (stored, never rendered), `View.ready()`, `InitMessage` extra fields, `Config.print()`. Remove or use.
- [ ] **Tests** — no test suite. Highest value/lowest effort: `Config.interpret`, `ANSI.trim`/`ANSI.len`, `Unicode.len`, `Util.format_seconds`, `Channel.load_channels`/`save_channels` round-trip, `Item.enumerate_best_format`.
- [ ] **Packaging** — add `pyproject.toml` with pinned `yt-dlp`, entry point `vidl = vidl.main:main`, plus `ruff` + `mypy` in CI.

## 🟢 Planned features

- [ ] **Archive rejects** — record filtered-out items so they aren't re-extracted every pass. yt-dlp exposes `YoutubeDL.record_download_archive(info_dict)`; call it from `Channel.__extract` in the `else` branch where you currently only `__set_error("No formats or outside cutoff")`. Recommend a *separate* reject file (`Config["Channels"]["rejected"]`) so a policy change (e.g. lowering `minimum_resolution`) can be undone by deleting one file — the main archive stays authoritative for successful downloads.

## ❓ Questions before I'd change these

- [ ] **`channel.py`** — `sleep_time = min(3600, int(time()) - process_time)` sleeps for *as long as the download took*. Deliberate rate-limiting/politeness heuristic, or was a fixed `Config["Download"]["sleep_interval"]` intended?
  Politeness

- [ ] **`view.py`** — `set_item` displaying `count - index + 1` (reverse playlist numbering) — deliberate "newest = #1"?
  Deliberate, the newest items comes first and will have highest index (aka as an episode number)

- [ ] **`coordinator.py`** — `run()` never exits on its own (infinite scheduling loop). Intended as a long-running daemon, or should it terminate after one full pass over all channels?
  long-running, but keyboard control implementation to let user exit

- [ ] **`channel.py`** — should a playlist parent's `last_download_date` update when *any* child succeeded, when *all* did, or unconditionally (current behaviour)?
  i have not decided, it is only supposed to be used for cases where the channels are single streams, but no plans for other cases

- [ ] **`slot.py`** — is `Slot.processor_lock` guarding `YoutubeDL()` construction because of the `cookiesfrombrowser` Safari cookie DB, or something else? That determines whether it can be narrowed.
  Plugins attachment for yt-dlp fails without, don't think cookies are a problem

== Created by Anthropic Opus 5 ==

# Code suggestions

## coordinator.py

**Periodic dirty save + dead-slot detection + keyboard exit:**

```python
def __poll_input(self):
    try:
        key = self.__input_queue.get_nowait()
    except Empty:
        return
    if key in ("q", "Q", "\x03", "\x04"):
        self.halt()


def halt(self):
    self.__running = False
    self.__halt_event.set()
```

The coordinator needs its own `self.__halt_event = Event()` so `wait(1)` is interruptible by `q`.

## terminal.py

**Restore under `finally`, tolerate non-TTY, batch the frame:**

```python
def __enter__(self):
    self.__interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not self.__interactive:
        return self
    self.__fd = sys.stdin.fileno()
    try:
        self.__old_termios = termios.tcgetattr(self.__fd)
        tty.setcbreak(self.__fd)
    except termios.error:
        self.__interactive = False
        return self
    sys.stdout.write(ANSI.Alternate.Enter)
    sys.stdout.flush()
    self.__reader = Thread(target=self.__read_input, daemon=True, name="Terminal-input")
    self.__reader.start()
    return self


def __exit__(self, exc_type, exc, tb):
    if not self.__interactive:
        return
    try:
        sys.stdout.write(ANSI.Alternate.Leave)
        sys.stdout.flush()
    finally:
        if self.__old_termios is not None and self.__fd is not None:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old_termios)
```

```python
def print(self, string, row, col):
    if not self.__interactive:
        return
    if not (1 <= row <= self.__height and 1 <= col <= self.__width):
        return
    remaining = self.__width - col + 1
    if ANSI.len(string) > remaining:
        string = ANSI.trim(string, remaining)
    self.__buffer.append(ANSI.at(string, row, col))


def flush(self):
    if not self.__buffer:
        return
    self.__buffer.append(ANSI.at())
    sys.stdout.write("".join(self.__buffer))
    sys.stdout.flush()
    self.__buffer.clear()
```

One `write` + one `flush` per frame instead of one per cell — that alone removes most of the visible tearing.

```python
def __read_input(self):
    while not self.__halt.is_set():
        if select.select([sys.stdin], [], [], 0.2)[0]:
            if char := sys.stdin.read(1):
                self.__input_queue.put(char)
```

Platform: keep `termios`/`tty` imports at module level but document POSIX-only in the README, and let the non-interactive branch above degrade to silent/no-op rendering so `vidl > log.txt` works.

---

## ansi.py

**Token-aware `trim`, OSC-aware `remove`:**

```python
ESCAPE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\)|[@-Z\\-_])"
)


@staticmethod
def remove(string):
    return ANSI.ESCAPE.sub("", string)


@staticmethod
def trim(string, length):
    out, width, pos = [], 0, 0
    while pos < len(string):
        if match := ANSI.ESCAPE.match(string, pos):
            out.append(match.group())
            pos = match.end()
            continue
        char_width = Unicode.len(string[pos])
        if width + char_width > length:
            break
        out.append(string[pos])
        width += char_width
        pos += 1
    return "".join(out)
```

Single left-to-right pass: O(n), and escapes never get re-inserted at wrong offsets.

**No `assert`, no shadowed builtins:**

```python
@staticmethod
def __get_rgb(hex_string):
    value = hex_string.removeprefix("#")
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"Invalid hex colour: {hex_string!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
```

Rename `ANSI.len` → `ANSI.width`, `ANSI.print` → `ANSI.at`, `Unicode.len` → `Unicode.width`. Mechanical, and it makes the `len()`/`ANSI.len()` bug class impossible to write.

---

## logger.py

Keep the nested target in the record:

```python
self.__write(
    logging.ERROR,
    f"{self.__slot_index} | {provider:>20.20} | {target or '-'} | {provider_message}",
)
```

Truthiness: `if match := self.__parse_prefix(...)` discards a legitimately empty target. Use explicit `is not None`:

```python
match = self.__parse_prefix("Extracting URL: ", message)
if match is not None:
    ...
```

Add the error sink from the channel section:

```python
def __init__(self, view_queue, slot_index, on_error=None) -> None:
    ...
    self.__on_error = on_error
```

and call `self.__on_error(provider_message)` in `error()` so the channels file records real yt-dlp failures instead of only internal ones.

---

## message.py

Annotate the body as non-optional except for `HALT`, and drop the runtime `isinstance` ladder in favour of `singledispatchmethod` (above). Then:

```python
@dataclass(frozen=True, slots=True)
class Message:
    kind: Msg
    body: ProviderMessage | None
```

`frozen=True` documents that messages cross a thread boundary and must not be mutated by the consumer. Delete the trailing design comments — move them to `docs/design.md` if they still describe intent.


---

## util.py

`format_seconds(include_seconds=True, two_parts=True)` with `hr > 0` falls through and returns rounded `HH:MM`, silently dropping the seconds the caller asked for:

```python
@staticmethod
def format_seconds(seconds, include_seconds=True, two_parts=True):
    seconds = abs(int(seconds))
    hr, remainder = divmod(seconds, 3600)
    mn, sc = divmod(remainder, 60)
    if include_seconds:
        if two_parts and hr == 0:
            return f"{mn:02}:{sc:02}"
        return f"{hr:02}:{mn:02}:{sc:02}"
    if sc > 0:
        mn, hr = (mn + 1, hr) if mn < 59 else (0, hr + 1)
    return f"{hr:02}:{mn:02}"
```

Confirm the intent for `two_parts=True, hr > 0` — I assumed "show `HH:MM:SS` rather than lose precision".

---

## Tooling

```toml
[project.scripts]
vidl = "vidl.main:main"

[project]
dependencies = ["yt-dlp>=2025.1.1", "wcwidth>=0.2.13", "grapheme>=0.6.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.6", "mypy>=1.11"]

[tool.ruff.lint]
select = ["E", "F", "W", "B", "A", "BLE", "C90", "RUF"]

[tool.mypy]
strict = true
```

```bash
python -m compileall -q vidl
ruff check .
mypy vidl
pytest
```

Highest value tests, in order: `Config.interpret` round-trip · `ANSI.trim`/`ANSI.width` · `Unicode.width` · `Util.format_seconds` · `Channel.load_channels`/`save_channels` round-trip with `;`, newlines, Unicode, `None` · `Item.decide` table-driven · coordinator "all slots busy, then one completes" · `__download` returning `0`.

---

# Checklist

## 🔴 Blocking
- [ ] `channel.py` — validate URL in `__download`, return `False` if none
- [ ] `channel.py` — guard `datetime.fromtimestamp()` against bad epochs
- [ ] `coordinator.py` — restore periodic saves (dirty flag + interval); currently only on exit
- [ ] `coordinator.py` — detect dead slot threads, fail with a message
- [ ] `view.py` — `try/except` around message dispatch so one bad message can't kill rendering
- [ ] `view.py` — `timer()` counts up after expiry; clamp at `00:00` and fix the sign
- [ ] `terminal.py` — restore termios in `finally`; tolerate non-TTY stdin
- [ ] `main.py` — drop redundant `Config.save()`; `argparse`; catch top-level exceptions

## 🟠 Correctness
- [ ] `channel.py` — `Outcome` enum; only `DOWNLOADED` bumps the date
- [ ] `channel.py` — aggregate child outcomes; collect errors instead of overwriting
- [ ] `channel.py` — rename shadowed `playlist_index` loop variable
- [ ] `channel.py` — `max_sub_level` recursion guard
- [ ] `channel.py` — send `"sub_channel"` provider so the view branch is reachable
- [ ] `item.py` — implement `minimum_duration`
- [ ] `item.py` — `Decision` enum; unknown resolution/duration → `DEFER`, not accept
- [ ] `item.py` — harden `get_item()` against non-dict info
- [ ] `item.py` — display `requested_formats`, not an independently chosen format
- [ ] `item.py` — best-format key: video-bearing only, bitrate tiebreak
- [ ] `view.py` — route unknown sleep providers (yt-dlp rate limits) to the UI
- [ ] `util.py` — `format_seconds` drops seconds when `hr > 0` *(confirm intent)*

## 🟡 Data integrity
- [ ] `channel.py` — `csv` module for read/write (`;` and newlines in titles)
- [ ] `channel.py` — atomic save: tempfile → `fsync` → `os.replace`
- [ ] `channel.py` — `state_lock` around mutators; snapshot rows before writing
- [ ] `channel.py` — write empty fields, not literal `"None"`
- [ ] `channel.py` — feed yt-dlp errors back via a `Logger` callback
- [ ] `config.py` — resolve relative paths against the config file's directory
- [ ] `config.py` — `validate()`; `main` fails fast
- [ ] `hook.py` / `slot.py` — use `yt_dlp.utils.DownloadCancelled`, drop the local class
- [ ] `slot.py` — `YoutubeDL` as context manager; add `alive()`; drop stray `sleep(3)`
- [ ] Signals — `SIGINT`/`SIGTERM` → shared halt event

## 🔵 Rendering
- [ ] `view.py` — single `__write()` helper that always clears the full line
- [ ] `view.py` — resize reflow (compare size each tick, clear + reassign)
- [ ] `view.py` — handle slot overflow visibly, or clamp slot count
- [ ] `view.py` — render `__top_name_last_dl` (or delete it)
- [ ] `terminal.py` — buffer a whole frame, one `write` + one `flush`
- [ ] `terminal.py` — input reader thread; coordinator handles `q` to quit
- [ ] `ansi.py` — token-aware `trim()`; OSC-aware `remove()`
- [ ] `ansi.py` — `ValueError` instead of `assert`
- [ ] `ansi.py` — rename `hex`, `len`, `print`
- [ ] `unicode.py` — replace with `wcwidth` + `grapheme`

## 🟢 Features
- [ ] SQLite state store keyed on `(extractor_key, id)`
- [ ] Reject archive: reason + `rule_version`; transient failures stay retryable
- [ ] Resolution hold: persist `first_seen`, `best_height`, `deadline`, `retry_after`
- [ ] Deferred items excluded from `download_archive`
- [ ] `hold_expiry_action = accept | reject`; cutoff takes precedence over hold
- [ ] `View.Status.HELD` + "held until" status line

## ⚪ Refactor / tooling
- [ ] `item.py` — dict-based construction (or `Details`/`Format`/`Progress` split)
- [ ] `view.py` — `singledispatchmethod` dispatch; extract `SlotRenderer`
- [ ] `channel.py` — split record/persistence from job execution
- [ ] `channel.py` — `set_epoch_cutoff` → cached `epoch_cutoff()`
- [ ] `logger.py` — real `logging` + rotation; keep `target` in records; `is not None` checks
- [ ] `message.py` — `frozen=True`; delete stale comments
- [ ] `config.py` — migrate to `tomllib` + typed dataclass
- [ ] Dead code — `__progress_meter_long`, `get_id`, `get_top`, `View.ready()`, `Config.print()`
- [ ] `pyproject.toml` — deps, entry point, ruff + mypy
- [ ] Tests — `compileall` in CI, then the priority list above
