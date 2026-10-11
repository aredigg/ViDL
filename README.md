# 𓃥 ViDL

Pretty CLI frontend to yt-dlp.

ViDL keeps a list of channels and playlists up to date. It works through the list continuously,
downloading new videos with several parallel slots. Progress is shown in a terminal dashboard
with one panel per slot.

## Requirements

- Python 3.10 or newer
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/), used by yt-dlp to merge video and audio
- macOS or Linux, and a terminal with true color support
- Optional: a [Nerd Font](https://www.nerdfonts.com/) for panel decorations

## Installation

```sh
pip install .
```

This installs the `vidl` command. ViDL can also be run from the source directory with
`python -m vidl`.

## Usage

```sh
vidl [config.ini]
```

The configuration file defaults to `config.ini` in the current directory. Relative paths in the
configuration are resolved from the current directory.

On the first run a default configuration is written. Set `Paths.output` and run again.

| Key               | Action                                           |
|-------------------|--------------------------------------------------|
| `q`               | Quit, running downloads are cancelled            |
| `Ctrl-C`, `Ctrl-D`| Same as `q`                                      |
| `c`               | Finish the running downloads, then quit. No new downloads are started, and the pause after a download is skipped. Press `c` again to continue normally, or `q` to quit at once |
| `a`               | Add a channel or playlist, see below             |

`SIGTERM` also triggers a graceful shutdown. Keyboard input is only read when standard input is
a terminal. Pasted text is never taken as key commands.

### Adding channels while running

`a` opens a prompt in the header. Type or paste an `http(s)` URL and press `Enter`, or press `Esc`
to cancel. The prompt supports the usual line editing keys (arrows, `Home`/`End`, `Ctrl-A`/`Ctrl-E`,
`Ctrl-U`, `Ctrl-K`, `Ctrl-W`). A URL that is already listed is rejected; the comparison ignores the
case of the host name, a leading `www.` or `m.`, and a trailing `/`. The channel is saved to the
channels file at once, and started next. When fewer slots are running than `Channels.slots` allows,
an extra slot is started.

This is the safe way to add channels while ViDL is running: the channels file is rewritten while
running, so changes made to it by hand are lost.

### Header

The header shows the time and uptime, free space on the download and temporary directories (a
single entry when both are on the same disk, colored below 10% and 2% free), the network status,
and the yt-dlp version with available updates. When the terminal is narrow, the yt-dlp version is
left out first, then the network status. A pending quit (`c`) or a back-off is shown below the
title, and the key hints, prompt or the result of a command on the last line.

### Errors and back-off

Errors are shown in a short form in the panels and in the channels file, e.g.
`abc123: Private video` or `Rate limited`. The full yt-dlp message is written to the debug log.

Some errors indicate a temporary block by the site: bot checks, rate limits and
`Video unavailable`. As a single `Video unavailable` may also just be a deleted video, all slots back
off for 6 hours only when enough slots report such errors within 15 minutes of each other: one
slot when only one is running, otherwise at least half of them and never fewer than two (2 slots:
both, 3 or 4 slots: 2, 5 or 6 slots: 3). Downloads that are running complete first. Below the
threshold, the slot pauses for 5 seconds and continues with the next video.

### Exit status

| Status | Meaning                                                  |
|--------|----------------------------------------------------------|
| 0      | Completed normally                                       |
| 1      | Invalid configuration, or the channels file is missing or unreadable |
| 2      | The channels file contains no channels                   |
| 3      | An error occurred while running, e.g. saving channels    |
| 4      | Unexpected error or interrupted during startup           |

## Channels file

The channels file is a semicolon separated list with one channel or playlist per line:

```
#;URL;Last Download;Last Attempt;Last Error Message
Some Channel;https://www.youtube.com/@somechannel;1767225600;1767225600;
https://www.youtube.com/@anotherchannel
```

- Lines starting with `#` are ignored.
- A line only needs the first column. When the URL column is empty, the first column is used as
  the URL, and the name is filled in from the channel metadata.
- Missing columns are treated as empty and extra columns are ignored.
- The dates are Unix timestamps, maintained by ViDL.
- The file is rewritten while running, keeping its permissions.

Channels are processed in order of their last download, or their last attempt if they have never
been downloaded.

## Configuration

The configuration file uses an INI style format. Strings are quoted, `None` means unset, and
booleans are written as `True`/`False` (`yes`/`no` is also accepted). The file is rewritten on
every start to keep it complete and valid. Comments are not preserved.

### `[Channels]`

| Option      | Default      | Description                                                       |
|-------------|--------------|-------------------------------------------------------------------|
| `file_name` | `"channels"` | The channels file                                                 |
| `archived`  | `"archived"` | yt-dlp download archive, videos recorded here are skipped         |
| `slots`     | `0`          | Number of parallel downloads, at least one slot is always used, and never more than the number of channels |

### `[Paths]`

| Option      | Default | Description                                                            |
|-------------|---------|------------------------------------------------------------------------|
| `output`    | `None`  | Download destination, required                                         |
| `temporary` | `None`  | Directory for partial downloads, the system temporary directory is used when unset or unavailable |
| `config`    |         | Set automatically to the current directory, informational only        |

Partial downloads are kept in a `.vidl-*` directory inside the temporary directory. It is removed
on exit.

### `[General]`

| Option           | Default    | Description                                            |
|------------------|------------|--------------------------------------------------------|
| `cookie_browser` | `"safari"` | Browser to load cookies from, `None` to disable        |
| `nerd_fonts`     | `True`     | Use Nerd Font glyphs in the panel decorations          |
| `update_check`   | `True`     | Check PyPI for a newer yt-dlp at start and every 6 hours, shown in the header |
| `network_check`  | `True`     | Check the network status every minute (a small request to YouTube), shown in the header |

### `[Download]`

| Option               | Default | Description |
|----------------------|---------|-------------|
| `allow_vertical`     | `False` | Download vertical videos. When disabled, they are recorded in the archive and skipped |
| `minimum_resolution` | `0`     | Minimum video height. Lower resolutions are recorded in the archive and skipped. `0` disables |
| `minimum_duration`   | `0`     | Reserved for a planned feature, currently unused |
| `playlist_cutoff`    | `0`     | Days. Only videos published within this many days of the newest video in a playlist are downloaded. Each tab of a channel, such as videos, shorts and live, is a separate playlist. `0` disables |
| `resolution_defer`   | `0`     | Days. Newer videos are postponed to give higher resolutions time to become available, unless the video is already available above 2000 pixels in height. `0` disables |
| `sleep_interval`     | `120`   | Seconds. Minimum pause after each download |
| `post_sleep_cutoff`  | `60`    | Minutes. The pause after a download lasts as long as the download did, but never longer than this |
| `metadata`           | `True`  | Write metadata to new downloads: title, date, description, the channel as show, and the episode number, counted from the oldest video of the playlist or channel tab |
| `thumbnail`          | `"channel"` | Cover image embedded in new downloads: `"channel"` for the channel avatar (the video thumbnail when there is none), `"video"` for the video thumbnail, `None` for none |

Metadata and cover images are only written to files downloaded in the same run. Files that already
exist are never changed.

### `[Debug]`

| Option      | Default   | Description                                                                 |
|-------------|-----------|-----------------------------------------------------------------------------|
| `active`    | `False`   | Write a debug log                                                           |
| `file_name` | `"debug"` | The debug log file                                                          |
| `wrap_size` | `1024`    | The log is rotated when it exceeds this many 16 KiB blocks. Up to 1000 rotated files are kept, suffixed `.000` to `.999` |

## Tests

The tests use the standard library only, and do not access the network:

```sh
python tests/run.py              # all tests, reported per module
python tests/run.py channel view # tests/test_channel.py and tests/test_view.py
python tests/run.py -k cutoff    # tests with "cutoff" in their name
python tests/run.py -q -x        # only problems and the summary, stop at the first
```

The exit status is 0 when all tests pass, 1 on failures or errors, and 2 when no tests match.

## Copyright

Copyright 2026 Are Digranes
