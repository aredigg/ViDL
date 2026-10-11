# TODO List

- [x] Complete keyboard/input class, and add interface to input text
  - [x] Raw input reading in `Terminal.__read_input` (`vidl/terminal.py`) with `os.read(fd, 1024)` and an incremental UTF-8 decoder, so escape sequences and pastes are never left behind in a Python buffer
  - [x] Input goes through the ViewController message queue as `InputMessage` (decoded keys), the separate `Queue[str]` is gone
  - [x] `vidl/keyboard.py`: `Key`, `KeyDecoder` (CSI/SS3 sequences, modifiers, Alt keys, lone ESC after a timeout, bracketed paste, bounded buffers), `LineEditor` (readline style editing, words, combining marks, wide characters, horizontal scrolling)
  - [x] `vidl/controls.py`: single key shortcuts (`COMMAND` mode) and a text prompt (`TEXT` mode). Pasted text never runs shortcuts
  - [x] Commands from the view thread to the coordinator (`vidl/command.py`, a `Queue[Command]`). Waiting for a command replaced `sleep(1)` in `Coordinator.run`
  - [x] Prompt, key hints and notices on header row 4, with the terminal cursor at the edit position while a prompt is open
  - [x] Tests: `tests/test_keyboard.py`, `tests/test_controls.py`, terminal input over a pty, ViewController dispatch
  - [x] README key table

- [x] Add a `c` key option to quit after current downloads complete
  - [x] "Current downloads" are the videos being downloaded right now, the pause after them is skipped
  - [x] A shared drain `Event` (`vidl/shared.py`), separate from the halt event, so running downloads are not cancelled
    - [x] Checked before each playlist entry, before extracting, in the pause after a download, in the back-off wait and the grace pause
    - [x] A drained channel records neither a download date nor an error (`Channel.interrupted()`). The same now applies to a halt between playlist entries, which before recorded a download date for an incomplete channel
    - [x] Scheduled videos (`available_at`) are waited for outside yt-dlp, whose `time.sleep` could not be interrupted by a halt or drain
  - [x] The coordinator starts no new channels while draining, and quits with status 0 once no slot is busy
  - [x] `c` again cancels, `q`/`Ctrl-C` during a drain quits at once
  - [x] Header and title bar show the pending quit
  - [x] Tests in `tests/test_channel.py` and `tests/test_coordinator.py`

- [x] Add channels from this keyboard interface
  - [x] `a` opens the `Add channel` prompt, submitting sends `AddChannelCommand`
  - [x] Handled on the coordinator thread: only `http(s)` URLs, duplicates rejected (host case, `www.`/`m.`, trailing `/` ignored, path and query case sensitive), saved at once, started next
  - [x] An extra slot is started when fewer are running than `Channels.slots` allows, and the back-off threshold follows
  - [x] Feedback as a short lived notice in the header
  - [ ] Optional: pause/remove a channel, would need channel selection UI
  - [x] README note that this is the safe way to add channels while running
  - [x] Tests: duplicates, invalid input, saving, extra slots, URL normalization

- [x] A common status bar in the header, should show time/program uptime, network status, yt-dlp version and if updates are available, disk usage- availability (dl/tmp)
  - [x] Header rows: logo, clock and uptime / status and system segments / prompt, notice or hints. Segments are dropped by priority on narrow terminals, colored by severity
  - [x] `vidl/monitor.py`: a `Monitor` thread publishing `SystemStatusMessage`, stopped in `Coordinator.__stop`
  - [x] Disk usage of the output and actual temporary directory, merged when on the same device, warnings below 10% and 2%
  - [x] Network status: a probe every minute, and network errors reported by the slots (degraded)
  - [x] yt-dlp version, the latest from PyPI every 6 hours, and a restart hint when upgraded while running
  - [x] `General.update_check` and `General.network_check` options, documented
  - [ ] Optional: total throughput of all slots
  - [ ] Optional: `Download.minimum_free_space` to pause starting downloads when space is low
  - [x] Tests: `tests/test_monitor.py`, header drawing in `tests/test_view.py`

- [x] Improving state transition, not depending on Logger
- [x] Use more yt-dlp features than parsing log
- [x] Use mostly sleep outside yt-dlp

- [x] Metadata updating, use the playlist index for episode metadata, channel graphics for image
  - [x] Scope: new downloads only. Already downloaded files are never touched: yt-dlp runs its post-processors also for files that already exist, so the metadata and thumbnail post-processors are guarded (`NewFileMetadataPP`, `NewFileEmbedThumbnailPP`), and thumbnails written to the temporary directory for an existing file are deleted instead of moved next to it
  - [x] Episode number as shown in the panel, counted from the oldest entry of the immediate playlist (e.g. a channel tab). Caveat: numbers shift if videos are removed from the channel
  - [x] `series` is the top level channel name, `episode` the title when not set
  - [ ] Optional: `season_number`, e.g. upload year or tab
  - [x] `MetadataPostProcessor` at the `video` stage (before thumbnails are written) fed a context by the channel before each download
  - [ ] Alternative not taken: `process_ie_result` instead of `download([url])` to save a request per video
  - [x] yt-dlp's `FFmpegMetadata` and `EmbedThumbnail` post-processors, with `writethumbnail`
  - [x] Channel avatar (`avatar_uncropped`) as cover, the video thumbnail when there is none
  - [x] `Download.metadata` and `Download.thumbnail` options, documented
  - [ ] Optional: `poster.jpg`/`folder.jpg` in the channel folder
  - [x] Tests: `tests/test_metadata.py` incl. an end to end run with a real `YoutubeDL` on an existing file

- [x] Improve error messages displayed in the panel, make them concise and short so they fit inside the panel
  - [x] No fixed message format is assumed, every step falls back to the cleaned full text
  - [x] Exception types and attributes are used before text (`ExtractorError.video_id`/`orig_msg`, `GeoRestrictedError`, `UserNotLive`, HTTP status, network exceptions)
  - [x] `Error` classifies into categories with a kind (restriction, permanent, transient) and a summary of at most 60 characters, without URLs
  - [x] One prefix parser replaces the `:` split routines (`Error.__parse_colon`, the split in `ViewController`, `Logger.__parse`): only a prefix is parsed, URLs and times are masked, an identity must look like an id, the body is never split
  - [x] `ErrorMessage` carries the summary, the channels file stores `id: summary`, the debug log the full text. Unexpected exceptions show `Internal error (TypeName)`, the channels file keeps the exception text
  - [x] Status line keeps the message over the provider, cuts are marked with `…` (`ANSI.ellipsize`)
  - [ ] Not needed: wrapping to panel row 3, summaries fit on one line
  - [ ] Optional: a key to show the full last error of a slot
  - [x] Tests: real yt-dlp messages in `tests/test_error.py`, status line widths in `tests/test_view.py`

- [x] Apply the 6 hour back-off only when 50% or more of the slots report restrictions
  - [x] `vidl/backoff.py`: a thread safe tracker of the latest restriction per slot, expiring after 15 minutes, cleared by a successful extraction or download
  - [x] Threshold: 1 for one slot, otherwise `max(2, ceil(slots / 2))`, so 2 slots: both, 3-4 slots: 2, 5-6 slots: 3
  - [x] Below the threshold: the error is reported and the slot pauses 5 seconds, nothing is archived
  - [x] Above it: one global back-off. No channels are started, the next playlist entry waits, running downloads complete, all panels show the mandatory sleep. Entries are cleared when it starts, reports during it are not recorded
  - [x] Halt and drain end the wait
  - [x] Header shows the back-off, decisions are written to the debug log
  - [x] `Channel.BACKOFF_TRIGGER` and the per channel 6 hour sleep are removed
  - [x] Documented in the README
  - [x] Tests: thresholds for 1-8 slots, the window, success, permanent errors, waiting, halt and drain
