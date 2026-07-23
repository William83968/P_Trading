"""Command-line entry point for the deterministic headless simulation."""

import argparse
import sys
from contextlib import nullcontext

from pirate_trading import __version__
from pirate_trading.content import ContentError
from pirate_trading.diagnostics import JsonlEventWriter, format_report
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import InvariantError, SimulationEngine, load_world


def main(argv: list[str] | None = None) -> int:
    """Run the configured headless simulation."""
    parser = argparse.ArgumentParser(description="Run the Pirate Trading headless simulation.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    try:
        world = load_world(SETTINGS)
        writer_context = (
            JsonlEventWriter(SETTINGS.event_log_path)
            if SETTINGS.write_event_log
            else nullcontext(None)
        )
        with writer_context as writer:
            engine = SimulationEngine(
                world,
                SETTINGS,
                event_sink=writer.write if writer is not None else None,
            )
            report = engine.run()
        print(format_report(report, world))
    except (ContentError, InvariantError, OSError) as error:
        print(f"Simulation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
