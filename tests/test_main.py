# pyright: standard
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import ClassVar
from unittest import mock

from tests.helpers import IsolatedTestCase
from vidl import __main__ as vidl_main
from vidl.config import Config


class FakeCoordinator:
    """Records the temporary directory, and returns or raises a result."""

    result: ClassVar[tuple[int, object] | BaseException] = (0, None)
    temporary: ClassVar[list[tuple[str, bool]]] = []

    def __init__(self) -> None:
        path = str(Config.settings["Paths"]["temporary"])
        FakeCoordinator.temporary.append((path, os.path.isdir(path)))
        if isinstance(FakeCoordinator.result, BaseException):
            raise FakeCoordinator.result
        self.status, self.message = FakeCoordinator.result

    def run(self) -> int:
        return self.status

    def error(self) -> object:
        return self.message


class MainTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.temp_dir()
        self.ini = os.path.join(self.directory, "config.ini")
        FakeCoordinator.result = (0, None)
        FakeCoordinator.temporary = []
        patcher = mock.patch.object(vidl_main, "Coordinator", FakeCoordinator)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self, **paths: str) -> None:
        lines = ["[Paths]", f'output = "{self.directory}"']
        lines += [f'{key} = "{value}"' for key, value in paths.items()]
        with open(self.ini, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def main(self, args: list[str] | None = None) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = vidl_main.main([self.ini] if args is None else args)
        return status, stdout.getvalue(), stderr.getvalue()


class TestMain(MainTestCase):
    def test_first_run_writes_a_default_config(self):
        status, _, errors = self.main()
        self.assertEqual(status, 1)
        self.assertIn("Paths.output missing", errors)
        self.assertTrue(os.path.exists(self.ini))
        self.assertEqual(FakeCoordinator.temporary, [])

    def test_successful_run(self):
        self.write_config()
        status, output, errors = self.main()
        self.assertEqual((status, output, errors), (0, "Done.\n", ""))
        ((temporary, existed),) = FakeCoordinator.temporary
        self.assertTrue(existed)
        self.assertFalse(os.path.exists(temporary))  # removed on exit
        self.assertTrue(
            os.path.basename(temporary).startswith(vidl_main.TEMPORARY_PREFIX)
        )

    def test_config_is_rewritten(self):
        self.write_config()
        self.main()
        with open(self.ini, encoding="utf-8") as f:
            self.assertIn("[Download]", f.read())

    def test_coordinator_error(self):
        self.write_config()
        FakeCoordinator.result = (3, "save failed")
        status, _, errors = self.main()
        self.assertEqual((status, errors), (3, "ERROR: save failed\n"))

    def test_status_without_error(self):
        self.write_config()
        FakeCoordinator.result = (2, None)
        self.assertEqual(self.main()[0::2], (2, "ERROR: Unknown\n"))

    def test_unexpected_exception(self):
        self.write_config()
        FakeCoordinator.result = RuntimeError("boom")
        self.assertEqual(self.main()[0::2], (4, "ERROR: boom\n"))

    def test_interrupted_during_startup(self):
        self.write_config()
        FakeCoordinator.result = KeyboardInterrupt()
        self.assertEqual(self.main()[0::2], (4, "ERROR: Interrupted\n"))

    def test_debug_is_always_stopped(self):
        self.write_config()
        Config.settings["Debug"]["active"] = True
        with open(self.ini, "a", encoding="utf-8") as f:
            f.write("[Debug]\nactive = True\n")
        FakeCoordinator.result = KeyboardInterrupt()
        with mock.patch.object(vidl_main, "Debug") as debug:
            self.main()
        debug.activate.assert_called_once_with()
        debug.deactivate.assert_called_once_with()

    def test_arguments_default_to_the_command_line(self):
        self.write_config()
        with mock.patch("sys.argv", ["vidl", self.ini]), redirect_stdout(io.StringIO()):
            self.assertEqual(vidl_main.main(), 0)
        self.assertEqual(Config.ini, self.ini)


class TestTemporaryDirectory(MainTestCase):
    def test_configured_directory(self):
        configured = os.path.join(self.directory, "tmp")
        os.mkdir(configured)
        self.write_config(temporary=configured)
        self.main()
        ((temporary, _),) = FakeCoordinator.temporary
        self.assertEqual(os.path.dirname(temporary), configured)

    def test_unavailable_directory_falls_back_to_the_system_default(self):
        self.write_config(temporary=os.path.join(self.directory, "missing"))
        status, _, errors = self.main()
        self.assertEqual(status, 0)
        self.assertIn("WARNING: Paths.temporary unavailable", errors)
        ((temporary, existed),) = FakeCoordinator.temporary
        self.assertTrue(existed)
        self.assertEqual(os.path.dirname(temporary), tempfile.gettempdir())

    def test_unset_uses_the_system_default(self):
        Config.settings["Paths"]["temporary"] = None
        with vidl_main.temporary_directory() as temporary:
            self.assertEqual(os.path.dirname(temporary), tempfile.gettempdir())

    def test_home_directory_is_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": self.directory}):
            Config.settings["Paths"]["temporary"] = "~"
            with vidl_main.temporary_directory() as temporary:
                self.assertEqual(os.path.dirname(temporary), self.directory)


if __name__ == "__main__":
    unittest.main()
