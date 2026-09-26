import os
import sys
import tempfile

from .config import Config
from .coordinator import Coordinator
from .debug import Debug

TEMPORARY_PREFIX = ".vidl-"


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    # Paths.temporary if provided and usable, otherwise the system temporary directory
    if configured := Config.settings["Paths"]["temporary"]:
        try:
            return tempfile.TemporaryDirectory(
                prefix=TEMPORARY_PREFIX, dir=os.path.expanduser(str(configured))
            )
        except OSError as e:
            print(
                f"WARNING: Paths.temporary unavailable, using system default ({e})",
                file=sys.stderr,
            )
    return tempfile.TemporaryDirectory(prefix=TEMPORARY_PREFIX)


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    status, error = 0, None
    if len(args) > 0:
        Config.initialize(args[0])
    else:
        Config.initialize()
    Config.load()
    if not Config.validate():
        return 1
    Config.save()
    debug = bool(Config.settings["Debug"]["active"])
    if debug:
        Debug.activate()
    try:
        with temporary_directory() as t:
            Config.settings["Paths"]["temporary"] = t
            coord = Coordinator()
            status = coord.run()
            error = coord.error()
    except KeyboardInterrupt:
        status, error = 4, "Interrupted"
    except Exception as e:  # noqa: BLE001
        status, error = 4, e
    finally:
        # The debug thread is not a daemon, it must always be stopped
        if debug:
            Debug.deactivate()
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
    elif status == 0:
        print("Done.")
    else:
        print("ERROR: Unknown", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
