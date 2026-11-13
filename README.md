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

The last line of the header shows the keys as `[Q] Quit | [C] Complete | [A] Add`. `[C] Complete`
lets the running downloads complete, and then quits (see the table).

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

The header shows the time and the uptime (`HH:MM′` in hours, also beyond a day, `MM′SS″` below an
hour), free space on the download (`d`) and temporary (`t`) directories (a single `d/t` entry when
both are on the same disk, colored below 10% and 2% free), the network status, and the yt-dlp
version with available updates, separated by `|`. When the terminal is narrow, the yt-dlp version
is left out first, then the network status. The last line has the key hints, the prompt or the
result of a command. Below the title, the states that last are shown, separated by `|`:

- `Completing 2`: after `c`, the number of downloads that are still running
- `Suspended → 14:05`: all slots are suspended until that time, see below
- `Slot 2 alone`: a large download runs alone, the other slots wait
- `Slot 2 waiting`: slot 2 waits for the running downloads to finish before a large download

The network status is a symbol, and not shown when `General.network_check` is off. With
`General.nerd_fonts` these are Nerd Font glyphs, otherwise a globe with a mark. A degraded network
(three or more errors of the downloads in ten minutes) is followed by the number of errors.

| State                      | Nerd Font        | Plain |
|----------------------------|------------------|-------|
| online                     | `󰱔` | `🌐 ✓` |
| offline                    | `󰱟` | `🌐 ✕` |
| degraded                   | `󰲚` | `🌐 !` |
| unknown, not checked yet   | `󰲊` | `🌐 ?` |

### Errors and suspension

Errors are shown in a short form in the panels and in the channels file, e.g.
`abc123: Private video` or `Rate limited`. The full yt-dlp message is written to the debug log.

Some errors indicate a temporary block by the site: bot checks, rate limits and
`Video unavailable`. As a single `Video unavailable` may also just be a deleted video, all slots are
suspended for 6 hours only when enough slots report such errors within 15 minutes of each other: one
slot when only one is running, otherwise at least half of them and never fewer than two (2 slots:
both, 3 or 4 slots: 2, 5 or 6 slots: 3). Downloads that are running complete first. Below the
threshold, the slot pauses for 5 seconds and continues with the next video.

### Retagging existing files

```sh
python -m vidl.retag FOLDER --dry-run   # or: vidl-retag FOLDER --dry-run
python -m vidl.retag FOLDER
```

Rewrites the tags of the `mp4` and `m4v` files of YouTube videos in a folder (and its subfolders)
to the format of new downloads, see `metadata` below. The video id is taken from the `tven` tag, or
from the file name when `tven` holds no id (older versions wrote the title there). The video is
looked up on YouTube, and only used if YouTube answers with that id. A duration that differs from
that of the file (more than `--tolerance`, 5 seconds or 2%) gives a warning below the file, but the
file is tagged as usual, as videos are shortened or edited after they were downloaded. When `tven`
and the file name hold different ids, the one whose duration matches is preferred. Warnings are
counted in the summary and do not change the exit status.

- **Start with `--dry-run`**, which shows each tag that would change, and `--limit 3` to try it on a
  few files.
- The streams are never touched. By default the tags are written to the file itself, which is the
  fastest and needs no free space. The file is read back and checked afterwards (every tag as
  planned, all others and the streams unchanged), and its modification time is restored. When the
  new tags need more room, the data after them is moved, so a write that is interrupted (a crash, a
  full disk, an unplugged drive) can damage the file; it is then reported as "the file may be
  damaged". Ctrl-C waits until the file being written is finished.
- With `--safe` the tags are written to a copy that is checked and then replaces the file, so a
  failure leaves the file as it was. That is slower and needs room for a copy in the same folder
  (on APFS a clone, which takes almost no space). The copy keeps the modification time, the
  permissions and, on macOS, the extended attributes (Finder tags), but its creation date is new.
- The episode number (`tves`) is the position of the video among all the uploads of its channel,
  see below: the first video of the channel is 1. The list of the uploads is fetched once for each
  channel (about one request per 100 videos), and `EPISODE_ORDER` is written to say how the number
  was counted. When the list cannot be fetched, or the video is not in it (e.g. for members only),
  the number in the file is kept. A number that was counted over a playlist is kept as well.
- The season (`tvsn`) is set as for a new download: 1, unless YouTube gives the video a season of
  its own. A season in the file is replaced, also in files that otherwise have the new format.
- The grouping (`©grp`), the cover and everything else that cannot be derived from a single video
  are kept. The resolution class (`hdvd`) is that of the file, read with `ffprobe`.
- Files that already have the new format, with the episode numbered as described, are skipped
  without a request, so an interrupted run can be repeated. `--force` checks them again. Files
  from earlier versions of ViDL, whose numbers were counted in another way, have no
  `EPISODE_ORDER` and are therefore done again.
- YouTube is asked slowly: requests start at least `--sleep` seconds (default 3) apart. The time a
  request and the work on a file take counts, so only what is left of the interval is waited. The channel description is fetched once per
  channel (`--no-channel-description` leaves it out). The run stops when YouTube restricts the
  requests (bot check, rate limit, five restricted answers in a row) or when the network is down. Cookies
  come from `--cookies-from-browser` or `General.cookie_browser` of `--config` (default
  `config.ini` in the current folder, which is only read).
- Symbolic links, hidden files and files that are no readable mp4 are skipped.

The exit status is 0 when all went well, 1 when a file failed or the run was stopped, 2 for wrong
arguments.

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
episode number (`tves`), the video id
(`tven`), season 1 unless yt-dlp provides one (`tvsn`), the extractor (`tvnn`), the description cut to
255 characters (`desc`) and in full (`ldes`), the channel description (`sdes`), hashtags of the title
and description (`keyw`), explicit (`rtng`) for videos with an age limit of 18, and the playlist or
channel tab, such as Shorts and Live, as grouping (`©grp`). The comment and all encoder tags are
left empty. The video URL, the channel URL and id, the upload time as ISO timestamp and how the
episode was counted are kept in the freeform tags `VIDEO_URL`, `CHANNEL_URL`, `CHANNEL_ID`,
`UPLOAD_TIMESTAMP` and `EPISODE_ORDER`.

#### Episode numbers

The first video of a channel is episode 1, the next 2, and so on, in the order they were published.
YouTube keeps a list of all uploads of every channel, videos, shorts and streams together, newest
first, and the number is the position counted from the end of that list. It is the same whether the
video is found in the videos, shorts or live tab, and a new video never changes the number of the
others (only a removed video moves the ones after it). The list is fetched when a video is about
to be downloaded, once per run of a channel, and shown in the panel. Without the list no number is
written, rather than a wrong one.

The videos of a playlist (an address with `list=`) are numbered in the order of the playlist, the
first is 1, as that is the order its maker gave it. Other sites, and videos on their own, get no
episode number.

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
