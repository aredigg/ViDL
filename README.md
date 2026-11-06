# 𓃥 ViDL

Pretty CLI frontend to yt-dlp.

ViDL keeps a list of channels and playlists up to date. It works through the list continuously,
downloading new videos with several parallel slots. Progress is shown in a terminal dashboard
with one panel per slot.

## Requirements

- Python 3.10 or newer
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/), used by yt-dlp to merge video and audio and to embed subtitles
- [mutagen](https://mutagen.readthedocs.io/), used to write mp4 tags and the cover (installed with
  ViDL)
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
left out first, then the network status. A pending quit (`c`) or a suspension is shown below the
title, and the key hints, prompt or the result of a command on the last line.

### Errors and suspension

Errors are shown in a short form in the panels and in the channels file, e.g.
`abc123: Private video` or `Rate limited`. The full yt-dlp message is written to the debug log.

Some errors indicate a temporary block by the site: bot checks, rate limits and
`Video unavailable`. As a single `Video unavailable` may also just be a deleted video, all slots are
suspended for 6 hours only when enough slots report such errors within 15 minutes of each other: one
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

Channels are processed in order of their last attempt, the one tried longest ago first and channels
that were never tried before the rest. A channel that fails is thereby tried again after all the
others. A channel whose attempt was cut short by quitting (`q`, or `c` while waiting) keeps its
place.

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
| `metadata`           | `True`  | Write metadata to new downloads, see below |
| `thumbnail`          | `"channel"` | Cover image embedded in new downloads: `"channel"` for the channel avatar (the video thumbnail when there is none), `"video"` for the video thumbnail, `None` for none |
| `maximum_size`       | `0`     | GB. A video larger than this is recorded in the archive and skipped, as is done with vertical videos. `0` disables. Videos of unknown size are never skipped |
| `exclusive_size`     | `10`    | GB. A download of at least this size runs alone: the downloads running in other slots finish first, and no others start until it is done. `0` disables |

Metadata, cover images and subtitles are only written to files downloaded in the same run. Files
that already exist are never changed.

Subtitles (all languages, but not the live chat) are embedded in the video as WebVTT converted by
ffmpeg to `mov_text`, the subtitle format of mp4, and the separate subtitle files are removed.
Styling and positioning of the cues is lost in that format.

`metadata` writes these mp4 tags: title (`©nam`), the channel as artist, album artist, album and show
(`©ART`, `aART`, `©alb`, `tvsh`), `stik` 10 (TV show), the resolution class (`hdvd`: 0 below 720,
1 for 720, 2 for 1080, 3 for 2160 and up), the publication date as `YYYY-MM-DD` (`©day`), genre,
episode number counted from the oldest video of the playlist or channel tab (`tves`), the video id
(`tven`), season 1 unless yt-dlp provides one (`tvsn`), the extractor (`tvnn`), the description cut to
255 characters (`desc`) and in full (`ldes`), the channel description (`sdes`), hashtags of the title
and description (`keyw`), explicit (`rtng`) for videos with an age limit of 18, and the playlist or
channel tab, such as Shorts and Live, as grouping (`©grp`). The comment and all encoder tags are
left empty. The video URL, the channel URL and id, and the upload time as ISO timestamp are
kept in the freeform tags `VIDEO_URL`, `CHANNEL_URL`, `CHANNEL_ID` and `UPLOAD_TIMESTAMP`.

### Large downloads, disk space and HLS

Just before a download starts, when yt-dlp has chosen the formats, ViDL checks:

- On YouTube, a download that would use HLS (m3u8) is refused, the quality is poor. The video is not
  recorded in the archive and is tried again later.
- The size against `maximum_size`.
- That the temporary directory can hold the download and the work on it: about twice the size,
  plus 5% and 256 MB, as the parts and the merged file exist together, and the metadata step makes
  a copy of the file. The download directory needs the size once, unless it is on the same disk.
  Space reserved by downloads that are running is counted. When there is not enough space, the
  download waits for the running ones to finish and fails with `No space in temp` when nothing is
  running. When the size is not known, 1 GB free is required.
- A large download (`exclusive_size`) waits for the running downloads and then runs alone.

Files written for a refused video, such as subtitles, are removed from the temporary directory.

### Cookies

When yt-dlp notices that YouTube rotated the cookies of a logged in session, it only warns. ViDL
then reloads the current cookies from `General.cookie_browser` into the running download (at most
once per 5 minutes) and tells so in the panel. If the browser has the same cookies, they have
expired there as well, and you have to log in again. The debug log shows a short hash of the
session cookies when they were loaded and at the end, never their values.

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
