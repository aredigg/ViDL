# A singleton class for accessing and reading/writing configuration data

import os
import sys


class Config:
    SLEEP_INTERVAL = 3
    path = os.path.abspath(os.path.curdir)
    ini = "config.ini"

    settings = {
        "Channels": {
            "file_name": "channels",
            "archived": "archived",
            "slots": 0,
        },
        "Paths": {
            "config": None,
            "output": None,
            "temporary": None,
        },
        "General": {
            "cookie_browser": "safari",
            "nerd_fonts": True,
        },
        "Download": {
            "allow_vertical": False,
            "minimum_resolution": 0,
            "minimum_duration": 0,
            "playlist_cutoff": 0,
            "resolution_defer": 0,
            "sleep_interval": 120,
            "post_sleep_cutoff": 60,
        },
        "Debug": {
            "file_name": "debug",
            "active": False,
        },
    }

    ydl_settings = {
        "color": "no_color",
        "ignoreerrors": False,
        "noprogress": True,
        "live_from_start": False,
        "allow_multiple_audio_streams": True,
        "retries": 5,
        "sleep_interval": SLEEP_INTERVAL,
        "max_sleep_interval": SLEEP_INTERVAL,
        "sleep_interval_requests": 1,
        "paths": {},
        "outtmpl": "%(channel)s/%(timestamp>%Y-%m)s/%(id)s.%(ext)s",
        "format": "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["all"],
        "writedescription": False,
        "writeinfojson": False,
        "hls_prefer_native": True,
        "extractor-args": {
            "youtube": {"player_client": ["default", "web_embedded", "-tv_downgraded"]},
        },
        "external_downloader_args": {
            "ffmpeg": ["-loglevel", "quiet", "-hide_banner", "-nostats"]
        },
    }

    @staticmethod
    def initialize(config_ini=ini):
        Config.ini = config_ini
        Config.settings["Paths"]["config"] = Config.path

    @staticmethod
    def load():
        try:
            with open(Config.ini, encoding="utf-8") as f:
                category = None
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        ...
                    elif line.startswith("[") and line.endswith("]"):
                        category = line[1:-1]
                        if category not in Config.settings:
                            Config.settings[category] = {}
                    elif line.count("=") > 0:
                        key, value = line.split("=", maxsplit=1)
                        key = key.strip()
                        value = value.strip()
                        if category is not None:
                            value = Config.interpret(value)
                            Config.settings[category][key] = value
        except FileNotFoundError:
            Config.save()

    @staticmethod
    def save():
        try:
            with open(Config.ini, "w", encoding="utf-8") as f:
                for category, options in Config.settings.items():
                    f.write(f"[{category}]\n")
                    for key, value in options.items():
                        if isinstance(value, str):
                            f.write(f'{key} = "{value}"\n')
                        else:
                            f.write(f"{key} = {value}\n")
                    f.write("\n")
        except OSError as e:
            print(f"File {Config.ini} error, {e}")

    @staticmethod
    def interpret(value):
        # None
        if value is None or value == "None" or value == "":
            return None
        # Quoted string
        if value.startswith('"') and value.endswith('"'):
            return str(value[1:-1])
        # Integer
        try:
            return int(value)
        except ValueError:
            ...
        # Float
        try:
            return float(value)
        except ValueError:
            ...
        # Boolean
        if isinstance(value, str):
            if value.casefold() in ["true", "yes"]:
                return True
            if value.casefold() in ["false", "no"]:
                return False
        return value

    @staticmethod
    def validate():
        result = True
        download = Config.settings["Download"]
        for key in (
            "minimum_resolution",
            "minimum_duration",
            "playlist_cutoff",
            "resolution_defer",
            "sleep_interval",
            "post_sleep_cutoff",
        ):
            value = download.get(key)
            if not isinstance(value, int) or value < 0:
                print(
                    f"ERROR: Download.{key} must be a non-negative integer",
                    file=sys.stderr,
                )
                result = False
        if not isinstance(Config.settings["Channels"]["slots"], int):
            print("ERROR: Slots must be an integer", file=sys.stderr)
            result = False
        elif Config.settings["Channels"]["slots"] < 0:
            print("ERROR: Channels.slots must be positive", file=sys.stderr)
            result = False
        if not Config.settings["Paths"]["output"]:
            print("ERROR: Paths.output missing", file=sys.stderr)
            result = False
        return result

    @staticmethod
    def print():
        for category, options in Config.settings.items():
            print(f"{category}:")
            for key, value in options.items():
                print(f"  {key}: {value}")
            print()
