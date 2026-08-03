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

- [ ] **`ansi.py`** — `ANSI.trim()` is broken: escape positions are recorded against the *original* string (including escape lengths) but re-inserted into the *stripped* string, so colors land at wrong offsets; the `while ANSI.len(...)` loop is also O(n²). Rewrite as a single left-to-right walk that accumulates visible width and passes escapes through:
  ```python
  @staticmethod
  def trim(string, length):
      regex = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
      out, width, pos = [], 0, 0
      while pos < len(string):
          if m := regex.match(string, pos):
              out.append(m.group())
              pos = m.end()
              continue
          w = Unicode.len(string[pos])
          if width + w > length:
              break
          out.append(string[pos])
          width += w
          pos += 1
      return "".join(out)
  ```
- [ ] **`channel.py`** — sub-channel loop shadows the `playlist_index` parameter and overwrites `__last_error` on every iteration, producing `"(None)"` when the sub-channel succeeded. Collect errors instead:
  ```python
  errors = [c.get_last_error() for c in self.__sub_channels if c.get_last_error()]
  if errors:
      self.__set_error(f"{len(errors)} sub-errors: {errors[-1]}")
  ```
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

- [ ] **`item.py`** — the 60-field dataclass built by concatenating three positional tuples is the most fragile part of the codebase: reordering one field in `get_details` silently mis-assigns ~30 attributes. Split into `Details`/`Format`/`Progress` dataclasses (composed into `Item`) and construct with **keyword** arguments:
  ```python
  @dataclass
  class Item:
      details: Details
      format: Format
      progress: Progress

      @classmethod
      def from_info(cls, info: dict) -> "Item":
          return cls(Details.from_info(info), Format.best(info.get("formats") or []),
                     Progress.empty())
  ```
  If you want to keep the flat shape, at least build via a dict + `Item(**fields)`.
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
- [ ] **Week hold for low-resolution uploads** — don't reject young videos that haven't finished processing; defer them. Suggested predicate in `Item`:
  ```python
  def on_hold(self, now: int) -> bool:
      """Young upload below target resolution: likely still transcoding."""
      hold_days = Config.settings["Download"]["resolution_hold_days"]  # e.g. 7
      target = Config.settings["Download"]["target_resolution"]        # e.g. 2160
      if not hold_days or not target or self.height >= target:
          return False
      if not self.timestamp:
          return False
      return (now - self.timestamp) < hold_days * 86_400
  ```
  Then in `Channel.__extract`: hold → **skip without recording** to the reject archive and without setting the download date, so the next pass retries. Add a `View.Status.HELD` (or reuse `WARNING`) with a "held until YYYY-MM-DD" status line so it's visible why nothing downloaded.
  - New config keys: `target_resolution`, `resolution_hold_days`.
  - Note this interacts with `playlist_cutoff`: a video can be *held* and then fall *outside* the cutoff. Decide precedence (suggest: cutoff wins, and log it).

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

## channel.py

**Outcome instead of bool** (resolves aggregation, partial playlists, and hold semantics at once):

```python
from enum import Enum, auto


class Outcome(Enum):
    DOWNLOADED = auto()
    NO_NEW_ITEMS = auto()
    DEFERRED = auto()
    REJECTED = auto()
    ERROR = auto()

    def bumps_download_date(self):
        return self is Outcome.DOWNLOADED
```

```python
def download(self, slot, processor, queue, playlist_index=1):
    if self.__slot_index is not None:
        return Outcome.ERROR
    self.__slot_index = slot
    try:
        self.__set_attempt_date()
        outcome = self.__extract(processor, queue, playlist_index)
        if outcome.bumps_download_date():
            self.__set_download_date()
        return outcome
    finally:
        self.__slot_index = None
        self.__active = False
```

**Playlist aggregation + error collection + no parameter shadowing:**

```python
outcomes, errors = [], []
for index, child in enumerate(sub_channels, start=1):
    child.set_active()
    child.set_halt_event(self.__halt_event)
    outcomes.append(child.download(self.__slot_index, processor, queue, index))
    if error := child.get_last_error():
        errors.append(error)

if errors:
    self.__set_error(f"{len(errors)} sub-errors; last: {errors[-1]}")
if any(outcome is Outcome.DOWNLOADED for outcome in outcomes):
    return Outcome.DOWNLOADED
if any(outcome is Outcome.ERROR for outcome in outcomes):
    return Outcome.ERROR
return Outcome.NO_NEW_ITEMS
```

**Provider name for sub-channels** (makes `view.__update_sleep`'s `"sub_channel"` branch reachable):

```python
provider = "channel" if self.__sub_level == 0 else "sub_channel"
```

---

## config.py

**Anchor relative paths to the config file:**

```python
@staticmethod
def initialize(config_ini=ini):
    Config.ini = os.path.abspath(config_ini)
    Config.path = os.path.dirname(Config.ini)
    Config.settings["Paths"]["config"] = Config.path


@staticmethod
def resolve(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(Config.path, path)
```

Call `Config.resolve()` at the point of use (`Channels.file_name`, `Channels.archived`, `Paths.output`, `Debug.file_name`), not at load time, so saving stays lossless.

---

## main.py

```python
import argparse
import signal
import sys
import tempfile

from .config import Config
from .coordinator import Coordinator


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="vidl")
    parser.add_argument("--config", default=Config.ini)
    parser.add_argument("--slots", type=int)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    Config.initialize(args.config)
    Config.load()
    if args.slots is not None:
        Config.settings["Channels"]["slots"] = args.slots
    if args.debug:
        Config.settings["Debug"]["active"] = True

    if errors := Config.validate():
        for error in errors:
            print(f"CONFIG: {error}", file=sys.stderr)
        return 2

    status, error = 0, None
    try:
        with tempfile.TemporaryDirectory() as temp:
            Config.settings["Paths"]["temporary"] = temp
            coord = Coordinator()
            signal.signal(signal.SIGTERM, lambda *_: coord.halt())
            status = coord.run()
            error = coord.error()
    except Exception as e:  # terminal is already restored by View teardown
        status, error = 3, e

    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return status or 3
    if status == 0:
        print("Done.")
    return status
```

Cleanup happens inside `Coordinator.run()`'s `finally` (View → Terminal `__exit__`), so every `print` here lands on the real screen.

---

## coordinator.py

**Periodic dirty save + dead-slot detection + keyboard exit:**

```python
SAVE_INTERVAL = 30

def run(self):
    try:
        ...
        last_save = time()
        while self.__running:
            self.__poll_input()
            if dead := [slot for slot in self.__slots if not slot.alive()]:
                self.__error = f"{len(dead)} worker slot(s) terminated unexpectedly"
                self.__running = False
                break

            channel = self.__next_channel()
            slot = next((slot for slot in self.__slots if slot.ready()), None)
            if channel is not None and slot is not None:
                channel.set_active()
                slot.process(channel)
                self.__dirty = True
            else:
                self.__halt_event.wait(1)

            if self.__dirty and time() - last_save >= self.SAVE_INTERVAL:
                self.__save_channels()
                self.__dirty = False
                last_save = time()
    finally:
        ...
```

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

---

## slot.py

```python
def alive(self):
    return self.__thread.is_alive()
```

```python
with Slot.processor_lock:
    processor = self.__setup(channel)
channel.set_halt_event(self.__halt_event)
try:
    with processor:
        channel.download(self.__index, processor, self.__view_queue)
except DownloadCancelled:
    ...
```

Drop the bare `sleep(3)` after `download()` — the politeness sleep already lives in `Channel.__extract`.

---

## hook.py

Do not define your own exception. yt-dlp already has one that its own error handling recognises and refuses to swallow under `ignoreerrors`:

```python
from yt_dlp.utils import DownloadCancelled
```

Delete the local class and import it in `slot.py` from the same place.

---

## item.py

**Decision instead of bool:**

```python
from enum import Enum, auto


class Decision(Enum):
    ACCEPT = auto()
    DEFER = auto()
    REJECT = auto()


def decide(self, first_seen=None):
    download = Config.settings["Download"]
    if not self.height or not self.width:
        return Decision.DEFER, "unknown resolution"
    if not download["allow_vertical"] and self.height > self.width:
        return Decision.REJECT, "vertical"
    if (minimum := download["minimum_resolution"]) and self.height < minimum:
        if self.__within_hold(first_seen):
            return Decision.DEFER, f"below {minimum}p, holding"
        return Decision.REJECT, f"below {minimum}p"
    if minimum := download["minimum_duration"]:
        if not self.duration:
            return Decision.DEFER, "unknown duration"
        if self.duration < minimum:
            return Decision.REJECT, f"shorter than {minimum}s"
    return Decision.ACCEPT, ""


def __within_hold(self, first_seen):
    days = Config.settings["Download"]["resolution_hold_days"]
    if not days or first_seen is None:
        return False
    return int(time()) - first_seen < days * 86_400
```

`first_seen` comes from the state store (below), **not** from `timestamp` — that is exactly the value that can be absent or wrong.

Cutoff precedence: check `within_cutoff()` first and let it win, so a held item that ages past the cutoff becomes a clean `REJECT` with reason `"outside cutoff"` instead of being held forever.

**Harden `get_item`:**

```python
@staticmethod
def get_item(info):
    info = info if isinstance(info, dict) else {}
    return Item(
        *(
            Item.get_details(info)
            + Item.selected_format(info)
            + Item.get_status({})
        )
    )
```

**Display what will actually be downloaded:**

```python
@staticmethod
def selected_format(info):
    requested = info.get("requested_formats") or info.get("requested_downloads")
    if requested:
        video = next(
            (f for f in requested if (f.get("vcodec") or "none") != "none"),
            requested[0],
        )
        audio = next(
            (f for f in requested if (f.get("acodec") or "none") != "none"), {}
        )
        merged = dict(video)
        for key in ("asr", "audio_channels", "acodec"):
            if not merged.get(key) or merged.get(key) == "none":
                merged[key] = audio.get(key)
        return Item.get_format(merged)
    return Item.enumerate_best_format(info.get("formats") or [])
```

**Better ordering** for the pre-extraction fallback (video-bearing only, bitrate as tiebreak):

```python
def key(format):
    return (
        format.get("height") or 0,
        format.get("fps") or 0,
        (format.get("dynamic_range") or "SDR").upper() != "SDR",
        format.get("vbr") or format.get("tbr") or 0,
    )

matching = [
    format
    for format in formats
    if (format.get("ext") or "").lower() == extension.lower()
    and (format.get("vcodec") or "none") != "none"
]
```

**Structural refactor** — the 60-field positional constructor is the highest-risk code you have. Minimum viable fix without a full split: build a dict and expand it.

```python
@staticmethod
def get_details(info) -> dict:
    return {
        "id": info.get("id") or "",
        "title": info.get("title") or "",
        ...
    }

Item(**(Item.get_details(info) | Item.selected_format(info) | Item.get_status({})))
```

Field order stops mattering, and a typo becomes a `TypeError` at construction rather than 30 silently mis-assigned attributes.

---

## view.py

**Guard the dispatch, keep the loop alive:**

```python
try:
    self.__dispatch(message)
except Exception as e:
    self.__report_internal_error(message, e)
```

```python
def __report_internal_error(self, message, error):
    index = getattr(getattr(message, "body", None), "index", None)
    if index in self.__slots:
        self.__slots[index].set_status_message("View", f"{type(error).__name__}: {error}")
        self.__slots[index].set_status(View.Status.ERROR)
```

**Countdown clamp (and sign fix — the current expression counts up):**

```python
def timer(self):
    if self.__timer is None:
        return "--:--"
    remaining = self.__timer - int(time())
    if remaining <= 0:
        if self.__status is View.Status.SLEEPING:
            self.__status = View.Status.WAITING
        return "00:00"
    return Util.format_seconds(remaining)
```

**One line writer, always clears:**

```python
def __write(self, terminal, text, row, col, width):
    text = ANSI.trim(text, width)
    terminal.print(text + " " * max(0, width - ANSI.len(text)), row, col)
```

Replace every `if line_len < w - 10: terminal.print(...)` with this. Stale text stops persisting on narrow terminals.

**Resize reflow** (cheap, once per second):

```python
size = terminal.get_size()
if size != self.__last_size:
    self.__last_size = size
    self.__term_size(terminal)
    for slot in self.__slots.values():
        slot.set_position(None)
    for slot in self.__slots.values():
        self.__assign_position(slot)
    terminal.clear()
    self.__term_header(terminal)
    full_count = 0
```

**Overflow visibility:**

```python
def __assign_position(self, slot):
    for row in range(self.__rows):
        for column in range(self.__columns):
            ...
    slot.set_position(None)
    self.__hidden += 1
```

and render `f"{self.__hidden} slot(s) hidden — enlarge terminal"` in the header. Simplest alternative: have `Coordinator` clamp `slots` to `rows * columns` at startup.

**Render `__top_name_last_dl`** on the right edge of the border:

```python
suffix = f" last {self.__top_name_last_dl} " if self.__top_name_last_dl else ""
fill = "─" * max(0, w - 4 - ANSI.len(header) - ANSI.len(suffix))
terminal.print("╭──" + header + fill + suffix + "╮", r, c)
```

**yt-dlp's own sleeps** currently vanish because the provider is an extractor name:

```python
def __update_sleep(self, body):
    slot = self.__slots.get(body.index)
    if slot is None:
        return
    if body.provider == "download":
        slot.set_timer(body.time_offset + 1)
        slot.set_status(View.Status.WAITING)
    elif body.provider in ("channel", "sub_channel"):
        slot.set_timer(body.time_offset)
        slot.reset()
        if body.provider == "sub_channel":
            slot.set_top("", "")
        slot.set_status_message(
            "Sleeping", f"ETA {Util.get_time(int(time()) + body.time_offset)}"
        )
        slot.set_status(View.Status.SLEEPING)
    else:  # extractor rate-limit sleep
        slot.set_timer(body.time_offset)
        slot.set_status_message(body.provider, "Rate limited")
        slot.set_status(View.Status.WAITING)
```

**Dispatch table** replacing the `isinstance` ladder:

```python
from functools import singledispatchmethod

@singledispatchmethod
def __dispatch_body(self, body):
    ...

@__dispatch_body.register
def _(self, body: InitMessage):
    self.__create_slot(body)

@__dispatch_body.register
def _(self, body: ItemMessage):
    self.__update_item(body)
```

`Msg.kind` then becomes redundant for everything except `HALT`.

**`View.Slot` split** — extract the pure part so it can be tested without a TTY:

```python
class SlotRenderer:
    def lines(self, state, width, height) -> list[tuple[int, int, str]]:
        """Pure: state -> positioned strings. No terminal, no I/O."""
```

`Slot.update()` becomes `for row, col, text in renderer.lines(...): terminal.print(text, row, col)`.

---

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
