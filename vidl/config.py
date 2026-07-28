# A singleton class for accessing and reading/writing configuration data

import os


class Config:
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
        "Download": {
            "minimum_resolution": 0,
            "minimum_duration": 0,
            "playlist_cutoff": 0,
        },
        "Debug": {
            "file_name": "debug",
            "active": False,
        },
    }

    ydl_settings = {
        "ignoreerrors": True,
        "live_from_start": False,
        "multistreams": True,
        "retries": 5,
        "sleep_interval": 10,
        "max_sleep_interval": 60,
        "sleep_interval_requests": 0.01,
        "paths": {},
        "cookiesfrombrowser": ("safari", None, None, None),
        "outtmpl": "%(epoch>%Y-%m)s/%(epoch>W%W)s/%(epoch>%a)s/%(id)s.%(ext)s",
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["all"],
        "writedescription": False,
        "writeinfojson": False,
        "hls_prefer_native": True,
        "external_downloader_args": {
            "ffmpeg": ["-loglevel", "quiet", "-hide_banner", "-nostats"]
        },
        "downloader_args": {
            "ffmpeg": ["-loglevel", "quiet", "-hide_banner", "-nostats"],
            "ffmpeg_i": ["-rw_timeout", "30000000"],
        },
        "postprocessor_args": {
            "ffmpeg": ["-loglevel", "error", "-hide_banner", "-nostats"]
        },
    }

    @staticmethod
    def initialize(config_ini=ini):
        Config.ini = config_ini
        Config.settings["Paths"]["config"] = Config.path

    @staticmethod
    def load():
        try:
            with open(Config.ini) as f:
                category = None
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        ...
                    elif line.startswith("[") and line.endswith("]"):
                        category = line[1:-1]
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
            with open(Config.ini, "w") as f:
                for category, options in Config.settings.items():
                    f.write(f"[{category}]\n")
                    for key, value in options.items():
                        f.write(f"{key} = {value}\n")
                    f.write("\n")
        except FileExistsError:
            print(f"File {Config.ini} exists")

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
    def print():
        for category, options in Config.settings.items():
            print(f"{category}:")
            for key, value in options.items():
                print(f"  {key}: {value}")
            print()
