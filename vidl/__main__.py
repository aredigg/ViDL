import sys
import tempfile

from .config import Config
from .coordinator import Coordinator
from .debug import Debug


def main(args) -> int:
    status, error = 0, None
    if len(args) > 0:
        Config.initialize(args[0])
    else:
        Config.initialize()
    Config.load()
    if not Config.validate():
        return 1
    Config.save()
    if Config.settings["Debug"]["active"]:
        Debug.activate()
    try:
        with tempfile.TemporaryDirectory() as t:
            Config.settings["Paths"]["temporary"] = t
            coord = Coordinator()
            status = coord.run()
            error = coord.error()
    except Exception as e:
        status, error = 4, e
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
    elif status == 0:
        print("Done.")
    else:
        print("ERROR: Unknown", file=sys.stderr)
    if Config.settings["Debug"]["active"]:
        Debug.deactivate()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
