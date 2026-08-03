import sys
import tempfile

from .config import Config
from .coordinator import Coordinator
from .debug import Debug


def main(args) -> int:
    status = 0
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
    with tempfile.TemporaryDirectory() as t:
        Config.settings["Paths"]["temporary"] = t
        coord = Coordinator()
        status = coord.run()
        if coord.error() is not None:
            print(f"ERROR: {coord.error()}", file=sys.stderr)
    print("Done.")
    if Config.settings["Debug"]["active"]:
        Debug.deactivate()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
