# ViDL

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
| `Ctrl-C`          | Same as `q`                                      |

`SIGTERM` also triggers a graceful shutdown. Keyboard input is only read when standard input is
a terminal.

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

### `[Debug]`

| Option      | Default   | Description                                                                 |
|-------------|-----------|-----------------------------------------------------------------------------|
| `active`    | `False`   | Write a debug log                                                           |
| `file_name` | `"debug"` | The debug log file                                                          |
| `wrap_size` | `1024`    | The log is rotated when it exceeds this many 16 KiB blocks. Up to 1000 rotated files are kept, suffixed `.000` to `.999` |

## Copyright

Copyright 2026 Are Digranes
