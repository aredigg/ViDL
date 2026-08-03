Some of the suggestions are not relevant or necessary.

== Created by OpenAI Sol ==

Below is a static review based on the supplied files. I’ve separated definite defects from behavior that may be intentional.

## Critical defects

- [x] **`channel.py` — Fix invalid exception syntax.**  
  This prevents the module from loading under Python 3:
  ```python
  except (ValueError, TypeError):
      self.__last_download_date = 0
      self.__last_attempt_date = 0
  ```

- [x] **`view.py` — Fix invalid exception syntax in `__update_filesize()`.**
  ```python
  except (FileNotFoundError, OSError):
      pass
  ```

- [x] **`coordinator.py` — Fix the scheduler deadlock when all channels are active.**  
  `__next_channel()` can return `None`. The inner loop then rejects an idle slot whose
  `channel()` is also `None`, and never recalculates the channel. Select both the
  channel and slot on each iteration:
  ```python
  while self.__running:
      channel = self.__next_channel()
      slot = next((slot for slot in self.__slots if slot.ready()), None)

      if channel is not None and slot is not None:
          channel.set_active()
          slot.process(channel)
      else:
          self.__save_channels()
          sleep(1)
  ```

- [x] **`channel.py` — Treat `YoutubeDL.download()` return values correctly.**  
  yt-dlp returns `0` for success, which is false in Python. Consequently,
  successful downloads may never update `last_download_date`.

  Also pass a list of URLs rather than a bare string:
  ```python
  def __download(self, processor):
      if self.__item is None:
          return False

      url = self.__item.original_url or self.__item.webpage_url
      return bool(url) and processor.download([url]) == 0
  ```

- [x] **`channel.py` — Always clear `active` and `slot_index` with `finally`.**  
  Any extraction, hook, logger, or yt-dlp exception currently leaves the channel
  permanently active:
  ```python
  def download(self, slot, processor, queue, playlist_index=1):
      if self.__slot_index is not None:
          return False

      self.__slot_index = slot
      try:
          self.__set_attempt_date()
          success = self.__extract(processor, queue, playlist_index)
          if success:
              self.__set_download_date()
          return success
      finally:
          self.__slot_index = None
          self.__active = False
  ```

- [ ] **`slot.py` — Catch worker exceptions and restore slot state in `finally`.**  
  An unhandled exception kills the slot thread, leaves its channel active, and can
  deadlock the coordinator. Report the exception to the view/coordinator and always
  clear `self.__channel`.

- [ ] **`coordinator.py` — Always shut down the view, including early returns.**  
  Missing channel files and empty channel lists return before `View.halt()`. The
  non-daemon view thread can keep the process alive and leave the terminal in cbreak
  and alternate-screen mode. Put shutdown in a `finally` block.

- [x] **`view.py` — Initialize instance state before starting its thread.**  
  `self.__thread.start()` currently happens before `self.__slots`, dimensions, and
  slot size are initialized. The thread can access them immediately and raise
  `AttributeError`.
  ```python
  self.__slots = {}
  self.__columns = 0
  self.__rows = 0
  self.__slot_size = None

  self.__thread = Thread(target=self.__run, name=f"View-{View.index}")
  self.__thread.start()
  ```

- [x] **`terminal.py` — Correct the bounds check.**  
  The chained comparisons can never work as intended:
  ```python
  if row < 1 or row > self.__height or col < 1 or col > self.__width:
      return
  ```

## Channel and playlist processing

- [x] **`channel.py` — Clear or replace `__sub_channels` on every playlist extraction.**  
  It is currently appended to each time the parent channel runs. Recurring playlist
  checks will repeatedly process old entries and continuously increase memory use.

- [x] **`channel.py` — Handle missing or malformed playlist entries.**
  ```python
  entries = list(info.get("entries") or [])
  for entry in entries:
      if not isinstance(entry, dict):
          continue
      url = entry.get("webpage_url") or entry.get("url")
      if not url:
          continue
  ```

- [ ] **`channel.py` — Aggregate child-channel success correctly.**  
  A playlist currently returns success even when every child fails, causing the
  parent’s last-download date to be updated. Track whether at least one child
  succeeded, or distinguish “playlist successfully inspected” from “media
  downloaded.”

- [x] **`channel.py` — Do not overwrite the parent error with `"(None)"`.**  
  Only copy a child error when one exists. Prefer collecting multiple errors instead
  of replacing the previous one.

- [ ] **`channel.py` — Decide how a partially successful playlist should be recorded.**  
  Define separate outcomes such as `DOWNLOADED`, `DEFERRED`, `REJECTED`,
  `NO_NEW_ITEMS`, and `ERROR`. A Boolean is becoming too ambiguous for this flow.

- [x] **`channel.py` — Revisit the post-download sleep calculation.**  
  `sleep_time` is the elapsed download time, capped at one hour:
  ```python
  sleep_time = min(3600, int(time()) - process_time)
  ```
  If this proportional cooldown is deliberate, document it. Otherwise use the
  configured sleep interval.

- [x] **`channel.py` — Replace the sleep loop with interruptible event waiting.**  
  The current loop discards the remainder below eight seconds and performs no sleep
  if the halt event is absent:
  ```python
  if self.__halt_event is not None:
      self.__halt_event.wait(sleep_time)
  else:
      sleep(sleep_time)
  ```

- [x] **`channel.py` — Report the sleep before actually sleeping only if that is the UI contract.**  
  At present the view receives a timer and the slot then blocks. That is reasonable,
  but it should be explicitly documented because the provider names control how the
  view interprets the message.

- [ ] **`channel.py` — Validate fallback URLs before downloading.**  
  `original_url` can be empty. Fall back to `webpage_url`, and reject the item with a
  clear error if both are missing.

- [ ] **`channel.py` — Guard timestamp conversion.**  
  Invalid or extreme persisted timestamps can make `datetime.fromtimestamp()` raise
  `OverflowError`, `OSError`, or `ValueError`.

## Configuration and persistence

- [x] **`config.py` / `main.py` — Avoid rewriting configuration on every startup.**  
  `Config.load()` followed immediately by `Config.save()` removes comments,
  formatting, and ordering from the user’s file. Only save when creating a default
  file or when settings actually change.

- [x] **`config.py` — Catch useful write errors.**  
  Opening a file with `"w"` normally does not raise `FileExistsError`. Catch
  `OSError` and propagate or return it instead of only printing.

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

- [x] **`config.py` — Preserve string types during round trips.**  
  A string such as `"true"` is written without quotes and reloads as Boolean
  `True`. Add explicit serialization for strings, or use `configparser`, TOML, or
  another established format.

- [x] **`config.py` / `slot.py` — Do not mutate global nested yt-dlp settings per slot.**  
  `Config.ydl_settings["paths"]`, logger, and hook arrays are shared. Build a deep
  copy for every processor:
  ```python
  from copy import deepcopy

  settings = deepcopy(Config.ydl_settings)
  ```

- [x] **`config.py` — Make the hard-coded Safari cookie source configurable.**  
  This fails on systems without Safari and can make otherwise valid downloads fail.
  Allow no cookie source, a cookie file, or a configured browser.

- [x] **`config.py` — Verify and simplify the format-selector whitespace.**  
  The selector contains spaces around one fallback slash. Remove accidental
  whitespace and add tests that ensure the intended fallback order is selected.

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

- [x] **All file I/O — Specify an encoding.**  
  Channel names, errors, and configuration can contain Unicode:
  ```python
  with open(file_name, encoding="utf-8") as file:
      ...
  ```

## Slot and coordinator lifecycle

- [x] **`slot.py` — Use the supplied slot index.**  
  The constructor ignores its `index` argument and uses the static `Slot.index`.
  This breaks when multiple coordinators are created in the same process. Assign
  `self.__index = index` and remove the static counter.

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

- [x] **`coordinator.py` — Save once more after all workers have joined.**  
  Otherwise the last completed download or error can be lost during shutdown.

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

- [x] **`item.py` — Use a list rather than `{}` as the empty formats collection.**
  ```python
  info.get("formats") or []
  ```

- [ ] **`item.py` — Harden `get_item()` against `None` and malformed extractor data.**  
  It uses `info.get(...)` after partially accounting for `info is None`.

- [x] **`item.py` — Simplify the cutoff condition.**  
  Inside the walrus block, `cutoff == 0` is unreachable:
  ```python
  cutoff = Config.settings["Download"]["playlist_cutoff"]
  if not cutoff:
      return True
  return self.timestamp >= channel.set_epoch_cutoff(cutoff * 86_400)
  ```

- [x] **`item.py` — Decide how missing timestamps interact with cutoff rules.**  
  A missing timestamp currently becomes zero and is rejected whenever a cutoff is
  active. This may incorrectly reject live items or extractors without timestamps.

## View and terminal behavior

- [ ] **`view.py` — Render on a fixed cadence instead of only when the queue is empty.**  
  Under a continuous stream of progress messages, the queue may never become empty,
  so nothing is rendered. Drain a bounded batch, then redraw based on monotonic time.

- [ ] **`view.py` — Avoid `KeyError` for unknown postprocessors.**
  ```python
  label = processes.get(item.processor, item.processor or "Unknown")
  ```

- [x] **`view.py` — Handle absent error messages.**  
  `ErrorMessage.message` is annotated as optional, but `__update_error()` calls
  `.split()` unconditionally.

- [ ] **`view.py` — Clamp countdown timers at zero or transition state when expired.**  
  `timer()` uses an absolute-value formatter, so after the deadline it starts
  counting upward again.

- [ ] **`view.py` — Always clear or print long lines.**  
  Many methods skip printing when content is too long, leaving stale text on screen.
  Let `Terminal.print()` trim it, or clear the full interior line before drawing.

- [x] **`view.py` — Use `ANSI.len()` for decorated or wide-character titles.**  
  `__border()` uses built-in `len()` when deciding whether the channel title fits.

- [x] **`view.py` — Prevent negative progress-meter widths.**  
  Small terminals or long ETA text can produce a negative requested meter length.
  Clamp it with `max(0, length)`.

- [x] **`view.py` — Clamp progress values.**  
  Percentages and remaining bytes can be negative or exceed 100 when totals are
  absent or estimates change.

- [ ] **`view.py` — Prefer yt-dlp’s ETA and estimated total when available.**  
  The manually calculated remaining time only works when total bytes and bitrate are
  both reliable. Fragmented and live downloads often do not meet that condition.
    --- NOOOOOOOOOO! --- yt-dlp eta and estimates sucks!!!!!!!!!!!!!!!!!!!

- [ ] **`view.py` — Implement terminal-resize reflow or explicitly document fixed startup sizing.**  
  The size is read only at startup. Slots can become clipped or misplaced after a
  resize.

- [ ] **`view.py` — Handle display-slot overflow.**  
  More worker slots than visible grid cells receive no position and are silently
  invisible. Either cap worker count, add paging, or reflow.

- [x] **`view.py` — Review the reverse playlist index calculation.**  
  `item_count - index + 1` may be deliberate if entries are newest-first. Document
  that assumption or display the source playlist index directly.

- [ ] **`view.py` — Remove or display `__top_name_last_dl`.**  
  It is assigned but never rendered.

- [x] **`terminal.py` — Correct available-width calculation.**  
  Depending on whether columns are inclusive, this likely needs:
  ```python
  remaining_space = self.__width - col + 1
  ```

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

- [x] **`channel.py`** — Python 2 except syntax → `SyntaxError`, module won't import.
  ```diff
  -        except ValueError, TypeError:
  +        except (ValueError, TypeError):
  ```
- [x] **`view.py`** — same in `__update_filesize`. `FileNotFoundError` is a subclass of `OSError`, so `OSError` alone suffices.
  ```diff
  -                    except FileNotFoundError, OSError:
  +                    except OSError:
  ```
- [x] **`channel.py`** — `__download()` returns yt-dlp's *exit code* (`0` = success), which is falsy. So `if self.__extract(...)` treats every successful download as a failure and `__set_download_date()` never fires.
  ```diff
  -    def __download(self, processor):
  -        if item := self.__item:
  -            return processor.download(item.original_url)
  +    def __download(self, processor):
  +        if item := self.__item:
  +            return processor.download([item.original_url]) == 0
  +        return False
  ```
- [x] **`view.py`** — `processes[item.processor]` raises `KeyError` for any postprocessor not in the dict (`FFmpegVideoConvertor`, `EmbedSubtitle`, `ExtractAudio`, `SponsorBlock`…), killing the view thread and freezing the UI.
  ```diff
  -                    self.__slots[body.index].set_status_message(
  -                        processes[item.processor], item.status
  -                    )
  +                    self.__slots[body.index].set_status_message(
  +                        processes.get(item.processor, item.processor), item.status
  +                    )
  ```
- [x] **`view.py`** — `__update_error`: `body.message` can be `None` (`ErrorMessage.message: None | str`) → `AttributeError` in the view thread. Guard before `.split(":")`.
- [ ] **`view.py`** — the whole `__run` loop has no top-level `try/except`. One unhandled exception silently kills rendering while downloads continue. Wrap the message dispatch in `try/except Exception` and surface the error into a slot/status line.
- [x] **`slot.py`** — `Hook.common` raises `DownloadCancelled` on halt; it propagates out of `channel.download()` and kills the slot thread, so `ready()` is `False` forever and the coordinator spins. Wrap the work in `__run`:
  ```python
  try:
      channel.download(self.__index, processor, self.__view_queue)
  except DownloadCancelled:
      pass
  except Exception as e:  # report, don't die
      self.__view_queue.put(Message(Msg.ERROR, ErrorMessage(
          self.__index, "slot", None, str(e))))
  finally:
      self.__channel = None
      self.__ready = True
  ```
- [x] **`coordinator.py`** — early returns (`return 1`, `return 2`) skip `self.__view.halt()/join()`. The View thread is non-daemon → the process hangs after "No channels". Restructure with a `finally:` shutdown block, or `return` through a single exit path.

## 🟠 Logic bugs

- [x] **`terminal.py`** — `if 1 > row > self.__height` is a chained comparison that is *always false*; bounds checking is dead code.
  ```diff
  -        if 1 > row > self.__height or 1 > col > self.__width:
  +        if not (1 <= row <= self.__height and 1 <= col <= self.__width):
               return
  -        remaining_space = self.__width - col
  +        remaining_space = self.__width - col + 1
  ```
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
- [x] **`channel.py`** — `Channel.load_channels` turns *blank lines* into channels (`""` name, `None` url). Skip empty lines: `if not line or line.startswith("#"): continue`.
- [ ] **`channel.py`** — sub-channel loop shadows the `playlist_index` parameter and overwrites `__last_error` on every iteration, producing `"(None)"` when the sub-channel succeeded. Collect errors instead:
  ```python
  errors = [c.get_last_error() for c in self.__sub_channels if c.get_last_error()]
  if errors:
      self.__set_error(f"{len(errors)} sub-errors: {errors[-1]}")
  ```
- [x] **`channel.py`** — halt busy-loop: when the event *is* set it iterates `sleep_time >> 3` times doing nothing instead of breaking. Also the condition is inverted-looking.
  ```diff
  -                        for _ in range(sleep_time >> 3):
  -                            if (
  -                                self.__halt_event is not None
  -                                and not self.__halt_event.is_set()
  -                            ):
  -                                sleep(8)
  +                        if self.__halt_event is not None:
  +                            self.__halt_event.wait(sleep_time)
  +                        else:
  +                            sleep(sleep_time)
  ```
- [x] **`channel.py`** — `entries = list(info.get("entries"))` → `TypeError` when a playlist has no `entries` key. Use `info.get("entries") or []`.
- [ ] **`channel.py`** — for playlists `__extract` returns `True` unconditionally, so the parent's download date is bumped even if every sub-channel failed. Return `any(...)`/`all(...)` of the sub results (confirm intent — see questions).
- [ ] **`channel.py`** — unbounded recursion depth for nested playlists (playlist → playlist → …). Add a `max_sub_level` guard from `Config`.
- [x] **`coordinator.py`** — when all channels are active, `__next_channel()` returns `None`, and `slot.channel() != channel` is `False` for every idle slot (idle slots hold `None`), so the loop spins at 1 Hz **rewriting the channels file every second**. Guard on `channel is None` before slot search, and throttle saves:
  ```python
  channel = self.__next_channel()
  if channel is None:
      self.__halt_event.wait(1)  # or sleep(1)
      continue
  ```
- [ ] **`coordinator.py`** — `Channel` state is mutated from slot threads while the coordinator serialises it → torn writes. Add a lock around `write()`/mutators, or snapshot under a lock before saving.
- [x] **`slot.py`** — the `index` argument is ignored (`self.__index = Slot.index`) while the thread name uses `index`. If they ever diverge, view slots and log lines desync. Pick one:
  ```diff
  -        self.__index = Slot.index
  +        self.__index = index
  ```
- [x] **`slot.py`** — `__setup` mutates the *shared* `Config.ydl_settings` (including the nested `paths` dict, which `dict(settings)` copies by reference). Every `YoutubeDL` instance shares one `paths` object. Use `copy.deepcopy(Config.ydl_settings)` and mutate the copy.
- [x] **`item.py`** — `Item.get_item` passes `info.get("formats") or {}`; if `formats` were ever a non-empty dict, iteration yields `str` keys and `format.get` explodes. Use `or []`.
- [x] **`item.py`** — `get_status` ignores `total_bytes_estimate`, which is what yt-dlp supplies for most fragmented/HLS downloads → `total_bytes = 0` → `rem_bits` goes negative in `view.__update_item`.
  ```diff
  -            data.get("total_bytes") or 0,
  +            data.get("total_bytes") or data.get("total_bytes_estimate") or 0,
  ```
  and clamp in the view: `rem_bits = max(0, total - downloaded) >> 7`.
- [x] **`item.py`** — `within_cutoff`: `cutoff == 0` is dead after the walrus (walrus already guarantees truthiness). Simplify.
- [ ] **`view.py`** — `Logger` emits `SleepMessage` with `provider` = extractor name (e.g. `youtube`), but `__update_sleep` only handles `"download"`, `"channel"`, `"sub_channel"`, so yt-dlp's own sleep intervals never show. Either broaden the check or normalise providers in `Logger`.
- [ ] **`view.py`** — `"sub_channel"` provider is handled but **nothing ever sends it**; `Channel` always sends `"channel"`, so sub-channel top-name reset is unreachable. Send the real provider from `Channel.__extract` based on `__sub_level`.
- [x] **`view.py`** — `set_item` computes `self.__item_count - index + 1`; with `__item_count == 0` (non-playlist) this yields negatives. Guard with `if self.__item_count else str(index)`.
- [ ] **`view.py`** — `__assign_position` silently drops slots when `rows * columns` is exhausted (already flagged by your TODO). At minimum log/mark overflow slots so they aren't invisible.
- [x] **`view.py`** — the loop only redraws in the `Empty` branch; under a message flood the UI stalls. Prefer `self.__queue.get(timeout=0.25)` and redraw on a wall-clock tick.
- [x] **`terminal.py`** — no `flush()` after `print(...)`; output relies on line buffering. Also `print(ANSI.print())` re-homes the cursor after *every* cell write. Batch a frame into one `sys.stdout.write` + single flush.
- [ ] **`util.py`** — `format_seconds(include_seconds=True, two_parts=True)` with `hr > 0` falls through the `if` block and returns rounded `HH:MM`, dropping seconds. Verify intent; if unintended, return `f"{hr:02}:{mn:02}:{sc:02}"`.

## 🟡 Robustness / data integrity

- [ ] **`channel.py`** — the channels file is hand-rolled CSV: any `;` in a title corrupts the row, and `write()` silently returns `""` (dropping the channel from the file = **data loss**). Switch to `csv.reader/writer` with proper quoting, or escape `;`.
- [ ] **`channel.py`** — `None` fields are written as the literal string `"None"` and read back as the string `"None"` (hence the `url == "None"` check). Write empty fields and convert `"" -> None` on load.
- [ ] **`channel.py`** — errors from yt-dlp reach the View but are never written back to `__last_error`; the channels file only ever records internal errors. Wire `Logger.error` → slot → channel.
- [ ] **`config.py`** — `save()` catches `FileExistsError` (never raised by `open(..., "w")`) but not `PermissionError`/`IsADirectoryError`/`OSError`. Catch `OSError`.
- [ ] **`config.py`** — no validation: `Paths.output` defaults to `None` and is passed straight to yt-dlp. Fail fast with a clear message if required settings are missing.
- [x] **`config.py`** — `interpret` round-trips lossily: a string value like `007` or `yes` saved unquoted comes back as `int`/`bool`. Quote strings on save.
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
