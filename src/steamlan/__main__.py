import logging
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    from steamlan.gui.window import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
