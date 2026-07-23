import json
from dataclasses import replace
from pathlib import Path

import pytest

from pirate_trading.persistence import SaveError, load_game, save_game
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world


def test_save_round_trip_preserves_state_hash(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json", write_event_log=False)
    world = load_world(settings)
    SimulationEngine(world, settings).step()

    save_game(world, settings=settings)
    restored = load_game(settings=settings)

    assert restored.state_hash() == world.state_hash()


def test_loaded_game_has_same_future_as_uninterrupted_game(tmp_path: Path) -> None:
    first_leg = replace(
        SETTINGS,
        simulation_ticks=100,
        quick_save_path=tmp_path / "save.json",
        write_event_log=False,
    )
    continued_world = load_world(first_leg)
    SimulationEngine(continued_world, first_leg).run()
    save_game(continued_world, settings=first_leg)
    continued_world = load_game(settings=first_leg)
    SimulationEngine(continued_world, first_leg).run()

    uninterrupted = replace(first_leg, simulation_ticks=200)
    uninterrupted_world = load_world(uninterrupted)
    SimulationEngine(uninterrupted_world, uninterrupted).run()

    assert continued_world.state_hash() == uninterrupted_world.state_hash()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(save_format_version=999), "Unsupported save format"),
        (lambda payload: payload.update(state_hash="bad"), "state hash"),
    ],
)
def test_invalid_save_is_rejected(tmp_path: Path, mutation, message: str) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    world = load_world(settings)
    save_game(world, settings=settings)
    payload = json.loads(settings.quick_save_path.read_text(encoding="utf-8"))
    mutation(payload)
    settings.quick_save_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SaveError, match=message):
        load_game(settings=settings)


def test_failed_atomic_replace_preserves_previous_save(tmp_path: Path, monkeypatch) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    world = load_world(settings)
    save_game(world, settings=settings)
    original = settings.quick_save_path.read_bytes()

    def fail_replace(source, destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("pirate_trading.persistence.os.replace", fail_replace)
    with pytest.raises(SaveError, match="simulated replace failure"):
        save_game(world, settings=settings)

    assert settings.quick_save_path.read_bytes() == original
