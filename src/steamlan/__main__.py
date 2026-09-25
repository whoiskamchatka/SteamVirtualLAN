import logging
import sys


def main() -> int:
    from steamlan.adapter.protocol import HELPER_FLAG

    if sys.argv[1:2] == [HELPER_FLAG]:
        # Started elevated by the app to own the virtual network adapter.
        from steamlan.adapter.helper import main as helper_main

        return helper_main(sys.argv[2:])

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    from steamlan.gui.window import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
