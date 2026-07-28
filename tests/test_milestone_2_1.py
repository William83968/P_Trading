"""Focused acceptance tests for situations, piracy pressure, and compatibility."""

import hashlib
import json
import random

from pirate_trading.models import (
    ActiveCondition,
    FenceStolenCargo,
    PayOffOfficials,
    PendingEncounter,
    ResolveEncounter,
)
from pirate_trading.narrative import maybe_generate_situation
from pirate_trading.persistence import load_game
from pirate_trading.simulation import SimulationEngine, load_world


def test_enforcement_and_patrol_content_is_loaded() -> None:
    world = load_world()

    assert world.ports["crowns_haven"].enforcement_bp == 10_000
    assert world.ports["blackwater_cay"].enforcement_bp == 0
    assert world.routes["crowns_haven_san_cordelia"].navy_patrol_bp == 600
    assert world.routes["isla_esmeralda_blackwater_cay"].navy_patrol_bp == 150


def test_pressure_thresholds_decay_and_services() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    engine._add_piracy_pressure(("crowns_haven",), 50, 60)

    assert engine._heat_status("crowns_haven") == "wanted"
    assert engine._heat_status("blackwater_cay") == "clear"

    player = world.merchants[world.player_id]
    player.port_id = "crowns_haven"
    player.cash = 100_000
    events = engine.execute_commands([PayOffOfficials(player.id, "crowns_haven", 20, "officials")])
    assert not any(event.event_type == "CommandRejected" for event in events)
    assert world.piracy_state.local_heat["crowns_haven"] == 40


def test_fencing_preserves_goods_and_stolen_provenance() -> None:
    world = load_world()
    player = world.merchants[world.player_id]
    player.port_id = "blackwater_cay"
    inventory = world.ports["blackwater_cay"].inventories["rum"]
    inventory.quantity -= 3
    player.cargo["rum"] = 3
    player.cargo_cost_basis["rum"] = 0
    player.stolen_cargo["rum"] = 3
    engine = SimulationEngine(world)

    events = engine.execute_commands([FenceStolenCargo(player.id, "blackwater_cay", "rum", 2)])

    assert not any(event.event_type == "CommandRejected" for event in events)
    assert player.cargo["rum"] == 1
    assert player.stolen_cargo["rum"] == 1
    engine.assert_invariants()


def test_situation_is_derived_from_active_condition() -> None:
    world = load_world()
    world.tick = 72
    world.next_situation_tick = 72
    for port in world.ports.values():
        for inventory in port.inventories.values():
            inventory.quantity = inventory.target
    world.active_conditions.append(
        ActiveCondition(
            id="storm_test",
            event_type="tropical_storm",
            stage="precursor",
            start_tick=72,
            transition_tick=96,
            recovery_tick=144,
            end_tick=168,
            severity_bp=5_000,
            port_id="crowns_haven",
            route_id="crowns_haven_whitecap_reach",
        )
    )
    emitted: list[tuple[str, dict[str, object]]] = []

    opportunity = maybe_generate_situation(
        world,
        SimulationEngine(world).settings,
        random.Random(4),
        lambda event_type, payload: emitted.append((event_type, payload)),
    )

    assert opportunity is not None
    assert opportunity.objective_type in {"reconnaissance", "protection"}
    assert opportunity.event_id == "storm_test"
    assert 48 <= world.next_situation_tick - world.tick <= 96
    assert emitted[-1][0] == "OpportunityCreated"


def test_navy_fine_reduces_pressure_and_is_ledgered() -> None:
    world = load_world()
    player = world.merchants[world.player_id]
    world.piracy_state.notoriety = 30
    world.piracy_state.local_heat["crowns_haven"] = 40
    encounter = PendingEncounter(
        id="navy_test",
        merchant_id=player.id,
        route_id="crowns_haven_san_cordelia",
        severity_bp=3_000,
        demand=2_000,
        encounter_type="navy",
        port_id="crowns_haven",
    )
    world.pending_encounters[encounter.id] = encounter
    engine = SimulationEngine(world)

    engine.execute_commands([ResolveEncounter(player.id, encounter.id, "pay")])

    assert world.ledger.fines_paid == 2_000
    assert world.piracy_state.notoriety == 25
    assert world.piracy_state.local_heat["crowns_haven"] == 30
    engine.assert_invariants()


def test_legacy_format_two_hash_is_checked_before_defaults(tmp_path) -> None:
    world = load_world()
    raw_world = world.to_primitive()
    raw_world.pop("piracy_state")
    raw_world.pop("next_situation_tick")
    raw_world.pop("situation_history")
    raw_world.pop("pending_raid_loot")
    raw_world["ledger"].pop("fines_paid")
    raw_world["ledger"].pop("confiscated_goods")
    raw_world["ledger"].pop("salvage_recovered")
    for port in raw_world["ports"].values():
        port.pop("enforcement_bp")
    for route in raw_world["routes"].values():
        route.pop("navy_patrol_bp")
    for merchant in raw_world["merchants"].values():
        merchant.pop("stolen_cargo")
        merchant.pop("mission_load")
        if merchant["voyage"]:
            merchant["voyage"].pop("navy_checked")
    canonical = json.dumps(raw_world, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    path = tmp_path / "legacy_format_2.json"
    path.write_text(
        json.dumps(
            {
                "save_format_version": 2,
                "game_version": "0.1.0",
                "state_hash": hashlib.sha256(canonical.encode()).hexdigest(),
                "world": raw_world,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_game(path)

    assert loaded.ports["crowns_haven"].enforcement_bp == 10_000
    assert loaded.routes["crowns_haven_san_cordelia"].navy_patrol_bp == 600
    assert loaded.piracy_state.local_heat["crowns_haven"] == 0
