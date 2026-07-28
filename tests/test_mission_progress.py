"""Objective-specific mission generation, progress, history, and compatibility."""

import hashlib
import json

import pytest

from pirate_trading.models import (
    ActiveCondition,
    Opportunity,
    RespondToOpportunity,
    Voyage,
)
from pirate_trading.narrative import maybe_generate_situation
from pirate_trading.persistence import load_game, save_game
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world
from pirate_trading.ui import build_mission_history, build_mission_progress


class _ObjectiveStream:
    """Select a requested objective without introducing unrelated random draws."""

    def __init__(self, objective: str) -> None:
        self.objective = objective

    def choice(self, values):
        return next(value for value in values if value[0] == self.objective)

    def randint(self, minimum: int, maximum: int) -> int:
        return minimum


def _quiet_world():
    world = load_world()
    world.tick = max(world.tick, world.next_situation_tick)
    world.active_conditions.clear()
    world.piracy_state.notoriety = 0
    for port_id in world.piracy_state.local_heat:
        world.piracy_state.local_heat[port_id] = 0
    player = world.merchants[world.player_id]
    player.stolen_cargo.clear()
    for port in world.ports.values():
        for inventory in port.inventories.values():
            inventory.quantity = inventory.target
    _rebase_goods_ledger(world)
    return world


def _rebase_goods_ledger(world) -> None:
    """Keep controlled inventory fixtures compatible with conservation checks."""

    for commodity_id in world.commodities:
        current = sum(port.inventories[commodity_id].quantity for port in world.ports.values())
        current += sum(merchant.cargo.get(commodity_id, 0) for merchant in world.merchants.values())
        if commodity_id == "grain":
            current += sum(merchant.ship.provisions for merchant in world.merchants.values())
        world.ledger.initial_goods[commodity_id] = current
        world.ledger.produced[commodity_id] = 0
        world.ledger.consumed[commodity_id] = 0
        world.ledger.overflow[commodity_id] = 0
        world.ledger.event_losses[commodity_id] = 0
        world.ledger.piracy_losses[commodity_id] = 0
        world.ledger.confiscated_goods[commodity_id] = 0
        world.ledger.salvage_recovered[commodity_id] = 0
    world.ledger.provisions_consumed = 0


def _condition(
    event_type: str,
    *,
    port_id: str = "crowns_haven",
    route_id: str | None = None,
    commodity_id: str | None = None,
) -> ActiveCondition:
    return ActiveCondition(
        id=f"condition_{event_type}",
        event_type=event_type,
        stage="active",
        start_tick=0,
        transition_tick=0,
        recovery_tick=1_000,
        end_tick=2_000,
        severity_bp=5_000,
        port_id=port_id,
        route_id=route_id,
        commodity_id=commodity_id,
    )


def _accepted_opportunity(
    world,
    objective: str,
    *,
    commodity_id: str | None = None,
    required_quantity: int = 0,
    destination_id: str = "crowns_haven",
    route_id: str | None = None,
    reserved_capacity: int = 0,
) -> Opportunity:
    opportunity = Opportunity(
        id=f"test_{objective}",
        kind="situation",
        title=objective.replace("_", " ").title(),
        description="Test mission",
        port_id="crowns_haven",
        created_tick=0,
        expiry_tick=120,
        status="accepted",
        commodity_id=commodity_id,
        required_quantity=required_quantity,
        reward=4_000,
        options=["accept", "decline"],
        accepted_by=world.player_id,
        accepted_tick=1,
        objective_type=objective,
        origin_port_id="crowns_haven",
        destination_port_id=destination_id,
        route_id=route_id,
        reserved_capacity=reserved_capacity,
    )
    world.opportunities[opportunity.id] = opportunity
    return opportunity


def test_festival_supply_names_its_deterministic_event_commodity() -> None:
    world = _quiet_world()
    condition = _condition("festival", commodity_id="rum")
    world.active_conditions.append(condition)

    opportunity = maybe_generate_situation(
        world,
        SETTINGS,
        _ObjectiveStream("festival_supply"),
        lambda *_: None,
    )

    assert opportunity is not None
    assert opportunity.objective_type == "festival_supply"
    assert opportunity.commodity_id == "rum"
    assert "Rum" in opportunity.description
    assert opportunity.required_quantity == 5


def test_harvest_export_uses_scarcest_directly_connected_destination() -> None:
    world = _quiet_world()
    world.active_conditions.append(_condition("bumper_harvest", commodity_id="sugar"))
    world.ports["whitecap_reach"].inventories["sugar"].quantity = 1

    opportunity = maybe_generate_situation(
        world,
        SETTINGS,
        _ObjectiveStream("harvest_export"),
        lambda *_: None,
    )

    assert opportunity is not None
    assert opportunity.objective_type == "harvest_export"
    assert opportunity.commodity_id == "sugar"
    assert opportunity.destination_port_id == "whitecap_reach"
    assert opportunity.route_id == "crowns_haven_whitecap_reach"


def test_route_jobs_are_complete_and_impossible_jobs_are_excluded() -> None:
    recon_world = _quiet_world()
    condition = _condition("hurricane")
    condition.stage = "precursor"
    recon_world.active_conditions.append(condition)
    recon = maybe_generate_situation(
        recon_world,
        SETTINGS,
        _ObjectiveStream("reconnaissance"),
        lambda *_: None,
    )
    assert recon is not None
    assert recon.route_id is not None
    assert recon.destination_port_id != recon.origin_port_id

    salvage_world = _quiet_world()
    salvage_world.active_conditions.append(_condition("warehouse_fire", commodity_id="timber"))
    assert (
        maybe_generate_situation(
            salvage_world,
            SETTINGS,
            _ObjectiveStream("salvage"),
            lambda *_: None,
        )
        is None
    )

    smuggling_world = _quiet_world()
    smuggling_world.merchants[smuggling_world.player_id].port_id = "blackwater_cay"
    smuggling_world.piracy_state.notoriety = 50
    assert (
        maybe_generate_situation(
            smuggling_world,
            SETTINGS,
            _ObjectiveStream("smuggling"),
            lambda *_: None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("objective", "condition"),
    [
        ("delivery", _condition("shipyard_commission", commodity_id="cloth")),
        ("evacuation", _condition("hurricane")),
        (
            "protection",
            _condition(
                "tropical_storm",
                route_id="crowns_haven_san_cordelia",
            ),
        ),
        ("salvage", _condition("warehouse_fire", commodity_id="timber")),
    ],
)
def test_generated_objectives_have_required_payload_or_route(
    objective: str,
    condition: ActiveCondition,
) -> None:
    world = _quiet_world()
    world.active_conditions.append(condition)
    if objective == "salvage":
        world.ledger.event_losses["timber"] = 4

    opportunity = maybe_generate_situation(
        world,
        SETTINGS,
        _ObjectiveStream(objective),
        lambda *_: None,
    )

    assert opportunity is not None
    assert opportunity.objective_type == objective
    if objective in {"delivery", "salvage"}:
        assert opportunity.commodity_id in world.commodities
    else:
        assert opportunity.route_id in world.routes
        assert opportunity.origin_port_id != opportunity.destination_port_id


def test_blackwater_passage_is_generated_only_away_from_blackwater() -> None:
    world = _quiet_world()
    world.piracy_state.notoriety = 50

    opportunity = maybe_generate_situation(
        world,
        SETTINGS,
        _ObjectiveStream("smuggling"),
        lambda *_: None,
    )

    assert opportunity is not None
    assert opportunity.objective_type == "smuggling"
    assert opportunity.destination_port_id == "blackwater_cay"
    assert opportunity.route_id is None
    assert opportunity.reserved_capacity == 5


def test_delivery_checklist_counts_only_clean_cargo_and_reports_readiness() -> None:
    world = _quiet_world()
    player = world.merchants[world.player_id]
    opportunity = _accepted_opportunity(
        world,
        "delivery",
        commodity_id="medicine",
        required_quantity=5,
    )
    player.cargo["medicine"] = 7
    player.stolen_cargo["medicine"] = 3

    partial = build_mission_progress(world, player.id, opportunity)

    assert partial.cargo_current == 4
    assert "Clean Medicine: 4/5" in {step.label for step in partial.steps}
    assert partial.action_hint == "Acquire 1 more clean Medicine."
    assert partial.disabled_reason == "insufficient clean cargo"
    assert not partial.can_complete

    player.cargo["medicine"] = 8
    ready = build_mission_progress(world, player.id, opportunity)
    assert ready.can_complete
    assert ready.action_hint == "Ready to hand over the contracted cargo."


@pytest.mark.parametrize("objective", ["reconnaissance", "protection"])
def test_exact_route_checklists_report_live_progress_and_off_course(objective: str) -> None:
    world = _quiet_world()
    player = world.merchants[world.player_id]
    route_id = "crowns_haven_san_cordelia"
    route = world.routes[route_id]
    opportunity = _accepted_opportunity(
        world,
        objective,
        destination_id="san_cordelia",
        route_id=route_id,
    )
    player.port_id = None
    player.voyage = Voyage(
        route_id=route_id,
        destination_id="san_cordelia",
        remaining_ticks=route.travel_ticks // 2,
    )

    en_route = build_mission_progress(world, player.id, opportunity)

    assert en_route.route_progress_percent is not None
    assert any("Sailing Crown's Haven → San Cordelia" in step.label for step in en_route.steps)

    player.voyage = Voyage(
        route_id="crowns_haven_whitecap_reach",
        destination_id="whitecap_reach",
        remaining_ticks=5,
    )
    off_course = build_mission_progress(world, player.id, opportunity)
    assert off_course.disabled_reason == "wrong route"
    assert any(step.status == "blocked" and "Off course" in step.label for step in off_course.steps)


def test_passenger_contraband_and_salvage_checklists_name_mission_loads() -> None:
    world = _quiet_world()
    player = world.merchants[world.player_id]
    evacuation = _accepted_opportunity(
        world,
        "evacuation",
        destination_id="san_cordelia",
        route_id="crowns_haven_san_cordelia",
        reserved_capacity=5,
    )
    smuggling = _accepted_opportunity(
        world,
        "smuggling",
        destination_id="blackwater_cay",
        reserved_capacity=5,
    )
    salvage = _accepted_opportunity(
        world,
        "salvage",
        commodity_id="timber",
    )
    player.mission_load[evacuation.id] = 5
    player.mission_load[smuggling.id] = 5
    world.ledger.event_losses["timber"] = 3

    evacuation_view = build_mission_progress(world, player.id, evacuation)
    smuggling_view = build_mission_progress(world, player.id, smuggling)
    salvage_view = build_mission_progress(world, player.id, salvage)

    assert "Reserved hold space: 5" in {step.label for step in evacuation_view.steps}
    assert "Passengers aboard: 5/5" in {step.label for step in evacuation_view.steps}
    assert "Contraband crates aboard: 5/5" in {step.label for step in smuggling_view.steps}
    assert salvage_view.payload_label == "Timber"
    assert salvage_view.can_complete
    assert salvage_view.action_hint == "Ready to recover the declared warehouse loss."


def test_outcome_fields_and_history_cover_completion_and_failure() -> None:
    world = _quiet_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    completed = _accepted_opportunity(
        world,
        "delivery",
        commodity_id="medicine",
        required_quantity=5,
    )
    player.cargo["medicine"] = 5
    player.cargo_cost_basis["medicine"] = 500
    world.ports[player.port_id].inventories["medicine"].quantity -= 5
    engine.execute_commands([RespondToOpportunity(player.id, completed.id, "deliver")])
    completed_view = build_mission_progress(world, player.id, completed)

    failed = _accepted_opportunity(world, "protection")
    engine.execute_commands([RespondToOpportunity(player.id, failed.id, "abandon")])
    history = build_mission_history(world)

    assert completed.resolved_tick == world.tick
    assert completed.failure_reason is None
    assert completed.penalty_paid == 0
    assert completed_view.disabled_reason == "objective already completed"
    assert failed.resolved_tick == world.tick
    assert failed.failure_reason == "abandoned"
    assert failed.penalty_assessed >= SETTINGS.mission_failure_minimum
    assert failed.penalty_paid == failed.penalty_assessed
    assert any("accepted Day 1, 01:00" in entry and "COMPLETED" in entry for entry in history)
    assert any("ABANDONED" in entry and "penalty" in entry for entry in history)


def test_expired_accepted_mission_records_a_historical_penalty() -> None:
    world = _quiet_world()
    engine = SimulationEngine(world)
    opportunity = _accepted_opportunity(world, "protection")
    world.tick = opportunity.expiry_tick

    engine._step_events = []
    engine._expire_opportunities()

    assert opportunity.status == "expired"
    assert opportunity.resolved_tick == world.tick
    assert opportunity.failure_reason == "expired"
    assert opportunity.penalty_assessed >= SETTINGS.mission_failure_minimum
    assert opportunity.penalty_paid == opportunity.penalty_assessed
    assert any("EXPIRED" in entry for entry in build_mission_history(world))
    engine.assert_invariants()


def test_resolved_mission_history_survives_format_two_round_trip(tmp_path) -> None:
    world = _quiet_world()
    engine = SimulationEngine(world)
    opportunity = _accepted_opportunity(world, "protection")
    engine.execute_commands([RespondToOpportunity(world.player_id, opportunity.id, "abandon")])
    path = tmp_path / "mission_history.json"

    save_game(world, path)
    restored = load_game(path)
    restored_opportunity = restored.opportunities[opportunity.id]

    assert restored_opportunity.resolved_tick == opportunity.resolved_tick
    assert restored_opportunity.failure_reason == "abandoned"
    assert restored_opportunity.penalty_assessed == opportunity.penalty_assessed
    assert restored_opportunity.penalty_paid == opportunity.penalty_paid
    assert build_mission_history(restored) == build_mission_history(world)


def test_legacy_festival_private_delivery_is_normalized_after_hash_check(tmp_path) -> None:
    world = _quiet_world()
    port = world.ports["crowns_haven"]
    port.inventories["coffee"].quantity = 1
    _rebase_goods_ledger(world)
    condition = _condition("festival")
    world.active_conditions.append(condition)
    opportunity = _accepted_opportunity(
        world,
        "private_delivery",
        required_quantity=5,
    )
    opportunity.event_id = condition.id

    raw_world = world.to_primitive()
    raw_opportunity = raw_world["opportunities"][opportunity.id]
    for field in (
        "resolved_tick",
        "failure_reason",
        "penalty_assessed",
        "penalty_paid",
    ):
        raw_opportunity.pop(field)
    canonical = json.dumps(raw_world, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    path = tmp_path / "legacy_festival.json"
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

    restored = load_game(path)

    assert restored.active_conditions[0].commodity_id == "coffee"
    assert restored.opportunities[opportunity.id].commodity_id == "coffee"
    assert restored.opportunities[opportunity.id].destination_port_id == "crowns_haven"
