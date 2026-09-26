#!/usr/bin/env python3
# pyright: standard
"""Runs the ViDL test suite and reports the result of each test.

Usage:
    python tests/run.py                  run all tests
    python tests/run.py channel view     run tests/test_channel.py and tests/test_view.py
    python tests/run.py -k cutoff        run tests with "cutoff" in their name

Options:
    -q, --quiet      only report problems and the summary
    -x, --failfast   stop at the first failure or error
    -k PATTERN       only run tests matching the pattern (may be repeated)
    --no-color       disable colored output

The exit status is 0 when all tests pass, 1 on failures or errors, and 2 when no
tests were found.
"""

import argparse
import os
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from typing import ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@dataclass
class ModuleReport:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


class Reporter(unittest.TestResult):
    """Reports each test as it completes, grouped by test module."""

    COLORS: ClassVar[dict[str, str]] = {
        "PASS": "32",
        "FAIL": "31",
        "ERROR": "31",
        "SKIP": "33",
    }

    def __init__(self, stream, quiet: bool, color: bool) -> None:
        super().__init__()
        self.buffer = True  # test output is only shown for problems
        self.stream = stream
        self.quiet = quiet
        self.color = color
        self.modules: dict[str, ModuleReport] = {}
        self.problems: list[tuple[str, str, str]] = []
        self.__module: str | None = None
        self.__status: str | None = None
        self.__started = 0.0

    def paint(self, text: str, status: str) -> str:
        return f"\x1b[{self.COLORS[status]}m{text}\x1b[0m" if self.color else text

    @staticmethod
    def module_of(test: unittest.TestCase) -> str:
        if type(test).__module__ == "unittest.loader":  # the module failed to load
            return getattr(test, "_testMethodName", "loader").rsplit(".", 1)[-1]
        return type(test).__module__.rsplit(".", 1)[-1]

    @staticmethod
    def name_of(test: unittest.TestCase) -> str:
        if type(test).__module__ == "unittest.loader":
            return "import"
        return f"{type(test).__name__}.{getattr(test, '_testMethodName', str(test))}"

    def startTest(self, test: unittest.TestCase) -> None:
        module = self.module_of(test)
        if module != self.__module:
            self.__module = module
            self.modules.setdefault(module, ModuleReport())
            if not self.quiet:
                self.stream.write(f"\n{module}\n")
        self.__status = "PASS"
        self.__started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        super().stopTest(test)
        duration = time.perf_counter() - self.__started
        report = self.modules[self.module_of(test)]
        report.duration += duration
        status = self.__status or "ERROR"
        match status:
            case "PASS":
                report.passed += 1
            case "FAIL":
                report.failed += 1
            case "ERROR":
                report.errors += 1
            case _:
                report.skipped += 1
        if not self.quiet or status in ("FAIL", "ERROR"):
            label = self.paint(f"{status:<5}", status)
            self.stream.write(f"  {label} {self.name_of(test)} ({duration:.2f}s)\n")
        self.stream.flush()
        self.__status = None

    def __problem(self, status: str, test: unittest.TestCase, details: str) -> None:
        self.problems.append(
            (status, f"{self.module_of(test)}.{self.name_of(test)}", details)
        )
        if self.__status is None:
            # Raised outside a test, e.g. in setUpClass or while importing
            report = self.modules.setdefault(self.module_of(test), ModuleReport())
            report.errors += 1
            self.stream.write(f"  {self.paint('ERROR', 'ERROR')} {test}\n")
        elif self.__status != "ERROR":
            self.__status = status

    # The captured output is part of the details in the summary, so unittest
    # should not also print it as soon as a test fails
    def addFailure(self, test, err) -> None:
        super().addFailure(test, err)
        self._mirrorOutput = False
        self.__problem("FAIL", test, self.failures[-1][1])

    def addError(self, test, err) -> None:
        super().addError(test, err)
        self._mirrorOutput = False
        self.__problem("ERROR", test, self.errors[-1][1])

    def addSubTest(self, test, subtest, err) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._mirrorOutput = False
            failed = err[0] is not None and issubclass(err[0], test.failureException)
            details = (self.failures if failed else self.errors)[-1][1]
            status = "FAIL" if failed else "ERROR"
            params = ", ".join(
                f"{k}={v!r}" for k, v in getattr(subtest, "params", {}).items()
            )
            name = f"{self.module_of(test)}.{self.name_of(test)} [{params}]"
            self.problems.append((status, name, details))
            if self.__status != "ERROR":
                self.__status = status

    def addSkip(self, test, reason) -> None:
        super().addSkip(test, reason)
        self.__status = "SKIP"

    def addExpectedFailure(self, test, err) -> None:
        super().addExpectedFailure(test, err)
        self.__status = "PASS"

    def addUnexpectedSuccess(self, test) -> None:
        super().addUnexpectedSuccess(test)
        self.__problem("FAIL", test, "Unexpected success\n")


def load(names: list[str], patterns: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    if patterns:
        loader.testNamePatterns = [p if "*" in p else f"*{p}*" for p in patterns]
    if not names:
        return loader.discover(TESTS, pattern="test_*.py", top_level_dir=ROOT)
    suite = unittest.TestSuite()
    for name in names:
        module = name.removesuffix(".py").removeprefix("tests.").removeprefix("test_")
        suite.addTests(loader.loadTestsFromName(f"tests.test_{module}"))
    return suite


def summary(reporter: Reporter, duration: float) -> None:
    write = reporter.stream.write
    for status, name, details in reporter.problems:
        write(f"\n{'=' * 78}\n{reporter.paint(status, status)}: {name}\n{'-' * 78}\n")
        write(details.rstrip() + "\n")

    header = f"{'Module':<24}{'Passed':>8}{'Failed':>8}{'Errors':>8}{'Skipped':>9}{'Time':>9}"
    write(f"\n{header}\n{'-' * len(header)}\n")
    total = ModuleReport()
    for module in sorted(reporter.modules):
        report = reporter.modules[module]
        write(
            f"{module:<24}{report.passed:>8}{report.failed:>8}{report.errors:>8}"
            f"{report.skipped:>9}{report.duration:>8.2f}s\n"
        )
        total.passed += report.passed
        total.failed += report.failed
        total.errors += report.errors
        total.skipped += report.skipped
    write(f"{'-' * len(header)}\n")
    write(
        f"{'Total':<24}{total.passed:>8}{total.failed:>8}{total.errors:>8}"
        f"{total.skipped:>9}{duration:>8.2f}s\n"
    )

    if reporter.wasSuccessful():
        result = reporter.paint("PASSED", "PASS")
    else:
        result = reporter.paint("FAILED", "FAIL")
    write(f"\nResult: {result} ({total.total} tests in {duration:.2f}s)\n")

    lingering = [
        t.name for t in threading.enumerate() if t is not threading.main_thread()
    ]
    if lingering:
        write(f"Warning: threads still running: {', '.join(lingering)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the ViDL test suite and report each test.",
        epilog="Modules are given without the test_ prefix, e.g. channel or view.",
    )
    parser.add_argument("modules", nargs="*", help="test modules to run (default: all)")
    parser.add_argument(
        "-k",
        dest="patterns",
        action="append",
        default=[],
        help="only run tests matching the pattern",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only report problems and the summary",
    )
    parser.add_argument(
        "-x",
        "--failfast",
        action="store_true",
        help="stop at the first failure or error",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable colored output"
    )
    args = parser.parse_args()

    stream = sys.stdout
    color = not args.no_color and stream.isatty() and "NO_COLOR" not in os.environ
    suite = load(args.modules, args.patterns)
    if suite.countTestCases() == 0:
        stream.write("No tests found.\n")
        return 2

    reporter = Reporter(stream, args.quiet, color)
    reporter.failfast = args.failfast
    stream.write(f"Running {suite.countTestCases()} tests\n")
    started = time.perf_counter()
    reporter.startTestRun()
    try:
        suite.run(reporter)
    finally:
        reporter.stopTestRun()
    summary(reporter, time.perf_counter() - started)
    return 0 if reporter.wasSuccessful() else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # The output was closed early, e.g. piped to head
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
