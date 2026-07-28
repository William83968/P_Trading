"""Versioned, atomic quick-save persistence."""

import hashlib
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
        if document.get("save_format_version") == 1 and settings.save_format_version == 2:
            raise SaveError(
                "This Milestone 1 save uses format 1 and cannot be upgraded safely. "
                "Start a New Game for the living-world simulation."
            )
        raise SaveError(
            f"Unsupported save format {document.get('save_format_version')!r}; "
            f"expected {settings.save_format_version}"
        )
    expected_hash = document.get("state_hash")
    if not isinstance(expected_hash, str):
        raise SaveError("Save is missing its state hash")
    raw_world = _mapping(document.get("world"), "Save world")
    raw_payload = json.dumps(raw_world, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(raw_payload.encode()).hexdigest() != expected_hash:
        raise SaveError("Save state hash does not match its contents")
    try:
        world = WorldState.from_primitive(raw_world)
    except (KeyError, TypeError, ValueError) as error:
        raise SaveError(f"Save world is malformed: {error}") from error
    enforcement_defaults = {
        "crowns_haven": 10_000,
        "san_cordelia": 8_000,
        "whitecap_reach": 7_000,
        "isla_esmeralda": 6_000,
        "blackwater_cay": 0,
    }
    patrol_defaults = {
        "crowns_haven_san_cordelia": 600,
        "san_cordelia_blackwater_cay": 250,
        "blackwater_cay_crowns_haven": 350,
        "crowns_haven_whitecap_reach": 500,
        "whitecap_reach_isla_esmeralda": 350,
        "isla_esmeralda_san_cordelia": 450,
        "isla_esmeralda_blackwater_cay": 150,
    }
    for port_id, port in world.ports.items():
        if "enforcement_bp" not in raw_world["ports"][port_id]:
            port.enforcement_bp = enforcement_defaults.get(port_id, 0)
    for route_id, route in tuple(world.routes.items()):
        if "navy_patrol_bp" not in raw_world["routes"][route_id]:
            world.routes[route_id] = type(route)(
                id=route.id,
                port_a=route.port_a,
                port_b=route.port_b,
                travel_ticks=route.travel_ticks,
                operating_cost=route.operating_cost,
                risk_bp=route.risk_bp,
                navy_patrol_bp=patrol_defaults.get(route_id, 0),
            )
    legacy_festival_commodities: dict[str, str] = {}
    for condition in world.active_conditions:
        if (
            condition.event_type == "festival"
            and condition.commodity_id is None
            and condition.port_id in world.ports
        ):
            condition.commodity_id = min(
                ("coffee", "rum", "sugar"),
                key=lambda commodity_id: (
                    world.ports[condition.port_id].inventories[commodity_id].quantity
                    * 10_000
                    // world.ports[condition.port_id].inventories[commodity_id].target,
                    commodity_id,
                ),
            )
            legacy_festival_commodities[condition.id] = condition.commodity_id
    for opportunity in world.opportunities.values():
        if opportunity.objective_type == "private_delivery" and opportunity.commodity_id is None:
            opportunity.commodity_id = legacy_festival_commodities.get(opportunity.event_id or "")
            if opportunity.commodity_id is None:
                port = world.ports[opportunity.port_id]
                opportunity.commodity_id = min(
                    ("coffee", "rum", "sugar"),
                    key=lambda commodity_id: (
                        port.inventories[commodity_id].quantity
                        * 10_000
                        // port.inventories[commodity_id].target,
                        commodity_id,
                    ),
                )
    for commodity_id in world.commodities:
        world.ledger.confiscated_goods.setdefault(commodity_id, 0)
        world.ledger.salvage_recovered.setdefault(commodity_id, 0)
    if "pending_raid_loot" not in raw_world:
        for merchant in world.merchants.values():
            for commodity_id, stolen in merchant.stolen_cargo.items():
                if stolen >= merchant.cargo.get(commodity_id, 0):
                    merchant.cargo_cost_basis[commodity_id] = 0
    for port_id in world.ports:
        world.piracy_state.local_heat.setdefault(port_id, 0)
        world.piracy_state.last_raid_ticks.setdefault(port_id, -100_000)
    try:
        SimulationEngine(world, settings).assert_invariants()
    except InvariantError as error:
        raise SaveError(f"Save violates simulation invariants: {error}") from error
    return world
