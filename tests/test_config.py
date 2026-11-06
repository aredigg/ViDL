# pyright: standard
import io
import os
import re
import unittest
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, cast

from yt_dlp import YoutubeDL

from tests.helpers import IsolatedTestCase
from vidl.config import Config


class TestYoutubeDLSettings(IsolatedTestCase):
    def test_every_setting_is_a_documented_yt_dlp_option(self):
        # yt-dlp ignores unknown options silently, e.g. the command line name
        # "extractor-args" instead of "extractor_args"
        documentation = YoutubeDL.__doc__ or ""
        for key in Config.ydl_settings:
            with self.subTest(key=key):
                found = re.search(
                    rf"(?<![\w-]){re.escape(key)}(?![\w-])", documentation
                )
                self.assertIsNotNone(found, f"{key} is not a yt-dlp option")

    def test_extractor_arguments_are_read_by_yt_dlp(self):
        # Lists as values: a string is split into letters, a hyphenated name is
        # not read at all
        for params, expected in (
            ({"extractor_args": {"youtube": {"skip": ["hls"]}}}, ["hls"]),
            ({"extractor_args": {"youtube": {"skip": "hls"}}}, ["h", "l", "s"]),
            ({"extractor-args": {"youtube": {"skip": ["hls"]}}}, []),
        ):
            with self.subTest(params=params):
                ydl = self.ydl(params)
                extractor = cast(Any, ydl.get_info_extractor("Youtube"))
                self.assertEqual(extractor._configuration_arg("skip"), expected)

    def ydl(self, params: Mapping[str, object]) -> YoutubeDL:
        factory = cast(Callable[[dict[str, object]], YoutubeDL], YoutubeDL)
        ydl = factory({"quiet": True, **params})
        self.addCleanup(ydl.close)
        return ydl

    def test_real_yt_dlp_accepts_the_settings(self):
        params = cast(dict[str, object], self.ydl(Config.ydl_settings).params)
        self.assertEqual(params["subtitlesformat"], "vtt/srt/best")
        self.assertEqual(params["subtitleslangs"], ["all", "-live_chat"])

    def test_live_chat_is_left_out_of_all_subtitles(self):
        # "all" with "-live_chat" selects every language but the chat
        ydl = cast(Any, self.ydl(Config.ydl_settings))
        subtitles = {
            "en": [{"ext": "vtt", "url": "https://x/en"}],
            "de": [{"ext": "vtt", "url": "https://x/de"}],
            "live_chat": [{"ext": "json", "url": "https://x/chat"}],
        }
        chosen = ydl.process_subtitles("abc", subtitles, {})
        self.assertEqual(sorted(chosen), ["de", "en"])


class TestInterpret(unittest.TestCase):
    def test_none(self):
        for value in (None, "None", ""):
            self.assertIsNone(Config.interpret(value))

    def test_quoted_strings_stay_strings(self):
        self.assertEqual(Config.interpret('"text"'), "text")
        self.assertEqual(Config.interpret('"42"'), "42")
        self.assertEqual(Config.interpret('""'), "")

    def test_numbers(self):
        self.assertEqual(Config.interpret("42"), 42)
        self.assertEqual(Config.interpret("-3"), -3)
        self.assertEqual(Config.interpret("1.5"), 1.5)

    def test_booleans(self):
        for value in ("true", "True", "yes", "YES"):
            self.assertIs(Config.interpret(value), True)
        for value in ("false", "FALSE", "no", "No"):
            self.assertIs(Config.interpret(value), False)

    def test_unquoted_text(self):
        self.assertEqual(Config.interpret("safari"), "safari")


class TestConfigFile(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.ini = os.path.join(self.temp_dir(), "config.ini")

    def write(self, content: str) -> None:
        with open(self.ini, "w", encoding="utf-8") as f:
            f.write(content)

    def test_initialize_sets_file_and_config_path(self):
        Config.initialize(self.ini)
        self.assertEqual(Config.ini, self.ini)
        self.assertEqual(Config.settings["Paths"]["config"], Config.path)

    def test_load_parses_categories_values_and_comments(self):
        self.write(
            "ignored = 1\n"
            "# comment\n"
            "[Channels]\n"
            "slots = 4\n"
            "[Paths]\n"
            'output = "/downloads"\n'
            "[Custom]\n"
            'key = "a=b"\n'
        )
        Config.initialize(self.ini)
        Config.load()
        self.assertEqual(Config.settings["Channels"]["slots"], 4)
        self.assertEqual(Config.settings["Channels"]["file_name"], "channels")
        self.assertEqual(Config.settings["Paths"]["output"], "/downloads")
        self.assertEqual(Config.settings["Custom"], {"key": "a=b"})
        self.assertNotIn("ignored", Config.settings["Channels"])

    def test_load_writes_defaults_when_missing(self):
        Config.initialize(self.ini)
        Config.load()
        self.assertTrue(os.path.exists(self.ini))
        with open(self.ini, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Download]", content)
        self.assertIn('cookie_browser = "safari"', content)

    def test_save_and_load_round_trip(self):
        Config.initialize(self.ini)
        Config.settings["Paths"]["output"] = "/out put"
        Config.settings["Download"]["allow_vertical"] = True
        Config.settings["Channels"]["slots"] = 3
        Config.save()
        Config.settings["Paths"]["output"] = None
        Config.settings["Download"]["allow_vertical"] = False
        Config.settings["Channels"]["slots"] = 0
        Config.load()
        self.assertEqual(Config.settings["Paths"]["output"], "/out put")
        self.assertIs(Config.settings["Download"]["allow_vertical"], True)
        self.assertEqual(Config.settings["Channels"]["slots"], 3)

    def test_save_rewrites_the_file_without_comments(self):
        self.write("# my comment\n[Channels]\nslots = 2\n")
        Config.initialize(self.ini)
        Config.load()
        Config.save()
        with open(self.ini, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("# my comment", content)
        self.assertIn("slots = 2", content)

    def test_save_error_is_reported_not_raised(self):
        Config.initialize(os.path.join(self.ini, "missing", "config.ini"))
        output = io.StringIO()
        with redirect_stdout(output):
            Config.save()
        self.assertIn("error", output.getvalue())


class TestValidate(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        Config.settings["Paths"]["output"] = "/downloads"

    def validate(self) -> tuple[bool, str]:
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = Config.validate()
        return result, errors.getvalue()

    def test_defaults_with_output_are_valid(self):
        self.assertEqual(self.validate(), (True, ""))

    def test_output_is_required(self):
        Config.settings["Paths"]["output"] = None
        result, errors = self.validate()
        self.assertFalse(result)
        self.assertIn("Paths.output", errors)

    def test_download_values_must_be_non_negative_integers(self):
        for key, value in (("sleep_interval", -1), ("playlist_cutoff", "7")):
            with self.subTest(key=key):
                Config.settings["Download"][key] = value
                result, errors = self.validate()
                self.assertFalse(result)
                self.assertIn(f"Download.{key}", errors)
                Config.settings["Download"][key] = 0

    def test_slots(self):
        Config.settings["Channels"]["slots"] = 0
        self.assertTrue(self.validate()[0])
        Config.settings["Channels"]["slots"] = -1
        self.assertIn("Channels.slots", self.validate()[1])
        Config.settings["Channels"]["slots"] = "two"
        self.assertIn("Channels.slots must be an integer", self.validate()[1])

    def test_size_options(self):
        for key in ("maximum_size", "exclusive_size"):
            for value in (0, 10, 2.5, 0.0):
                with self.subTest(key=key, value=value):
                    Config.settings["Download"][key] = value
                    self.assertEqual(self.validate(), (True, ""))
            for value in (-1, -0.5, "5", None, True, float("nan"), float("inf")):
                with self.subTest(key=key, value=value):
                    Config.settings["Download"][key] = value
                    result, errors = self.validate()
                    self.assertFalse(result)
                    self.assertIn(
                        f"Download.{key} must be a non-negative number", errors
                    )
            Config.settings["Download"][key] = 0

    def test_size_defaults(self):
        self.assertEqual(Config.settings["Download"]["maximum_size"], 0)
        self.assertEqual(Config.settings["Download"]["exclusive_size"], 10)

    def test_metadata_options(self):
        for thumbnail in ("channel", "video", None):
            with self.subTest(thumbnail=thumbnail):
                Config.settings["Download"]["thumbnail"] = thumbnail
                self.assertTrue(self.validate()[0])
        Config.settings["Download"]["thumbnail"] = "avatar"
        result, errors = self.validate()
        self.assertFalse(result)
        self.assertIn("Download.thumbnail", errors)
        Config.settings["Download"]["thumbnail"] = "channel"
        Config.settings["Download"]["metadata"] = "yes please"
        result, errors = self.validate()
        self.assertFalse(result)
        self.assertIn("Download.metadata", errors)

    def test_check_options_must_be_booleans(self):
        for key in ("update_check", "network_check"):
            with self.subTest(key=key):
                Config.settings["General"][key] = 1
                result, errors = self.validate()
                self.assertFalse(result)
                self.assertIn(f"General.{key}", errors)
                Config.settings["General"][key] = False
                self.assertTrue(self.validate()[0])

    def test_new_options_have_safe_defaults(self):
        self.assertIs(Config.settings["Download"]["metadata"], True)
        self.assertEqual(Config.settings["Download"]["thumbnail"], "channel")
        self.assertIs(Config.settings["General"]["update_check"], True)
        self.assertIs(Config.settings["General"]["network_check"], True)

    def test_wrap_size_must_be_an_integer(self):
        Config.settings["Debug"]["wrap_size"] = "big"
        result, errors = self.validate()
        self.assertFalse(result)
        self.assertIn("Debug.wrap_size", errors)

    def test_all_errors_are_reported(self):
        Config.settings["Paths"]["output"] = None
        Config.settings["Channels"]["slots"] = -1
        _, errors = self.validate()
        self.assertEqual(len(errors.strip().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
