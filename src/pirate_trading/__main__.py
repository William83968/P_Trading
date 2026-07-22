"""Command-line entry point for Pirate Trading."""

from pirate_trading import __version__


def main() -> int:
    """Print package information and exit successfully."""
    print(f"Pirate Trading {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
