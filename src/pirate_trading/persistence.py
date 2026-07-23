"""Versioned, atomic quick-save persistence."""

import json
import os
from pathlib import Path
from typing import Any

from pirate_trading import __version__
from pirate_trading.models import WorldState
from pirate_trading.settings import SETTINGS, SimulationSettings
from pirate_trading.simulation import InvariantError, SimulationEngine


class SaveError(ValueError):
    """Raised when a save cannot be written or safely reconstructed."""


def save_game(
    world: WorldState,
    path: Path | None = None,
    settings: SimulationSettings = SETTINGS,
) -> None:
    """Atomically write the canonical world state to a JSON quick-save."""
    destination = path or settings.quick_save_path
    payload = {
        "save_format_version": settings.save_format_version,
        "game_version": __version__,
        "state_hash": world.state_hash(),
        "world": world.to_primitive(),
    }
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SaveError(f"Could not write save {destination}: {error}") from error


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SaveError(f"{context} must be a JSON object")
    return value


def load_game(
    path: Path | None = None,
    settings: SimulationSettings = SETTINGS,
) -> WorldState:
    """Load and validate a quick-save without mutating the file."""
    source = path or settings.quick_save_path
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SaveError(f"No quick-save exists at {source}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise SaveError(f"Could not read save {source}: {error}") from error

    document = _mapping(payload, "Save")
    if document.get("save_format_version") != settings.save_format_version:
        raise SaveError(
            f"Unsupported save format {document.get('save_format_version')!r}; "
            f"expected {settings.save_format_version}"
        )
    expected_hash = document.get("state_hash")
    if not isinstance(expected_hash, str):
        raise SaveError("Save is missing its state hash")
    try:
        world = WorldState.from_primitive(_mapping(document.get("world"), "Save world"))
    except (KeyError, TypeError, ValueError) as error:
        raise SaveError(f"Save world is malformed: {error}") from error
    if world.state_hash() != expected_hash:
        raise SaveError("Save state hash does not match its contents")
    try:
        SimulationEngine(world, settings).assert_invariants()
    except InvariantError as error:
        raise SaveError(f"Save violates simulation invariants: {error}") from error
    return world
