"""Command-line entry point for the Pirate Trading pygame UI."""

import argparse
import sys

from pirate_trading import __version__
from pirate_trading.ui import AssetError, run_ui


def main(argv: list[str] | None = None) -> int:
    """Run the pygame trading interface."""
    parser = argparse.ArgumentParser(description="Run the Pirate Trading game.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    try:
        return run_ui()
    except (AssetError, OSError) as error:
        print(f"Could not start Pirate Trading: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
