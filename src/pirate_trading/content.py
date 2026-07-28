"""Load and validate Milestone 0 YAML content."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pirate_trading.models import Commodity, Inventory, Port, Route
from pirate_trading.settings import SimulationSettings


class ContentError(ValueError):
    """Raised when authored game content is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class LoadedContent:
    commodities: dict[str, Commodity]
    ports: dict[str, Port]
    routes: dict[str, Route]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContentError(f"Could not read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ContentError(f"Malformed YAML in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContentError(f"{path} must contain a mapping at its root")
    return value


def _records(document: dict[str, Any], key: str, path: Path) -> list[dict[str, Any]]:
    records = document.get(key)
    if not isinstance(records, list):
        raise ContentError(f"{path}: {key!r} must be a list")
    if not all(isinstance(record, dict) for record in records):
        raise ContentError(f"{path}: every {key!r} entry must be a mapping")
    return records


def _integer(record: dict[str, Any], key: str, context: str, *, positive: bool = False) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContentError(f"{context}: {key!r} must be an integer")
    if positive and value <= 0:
        raise ContentError(f"{context}: {key!r} must be positive")
    if not positive and value < 0:
        raise ContentError(f"{context}: {key!r} cannot be negative")
    return value


def _identifier(record: dict[str, Any], context: str) -> str:
    value = record.get("id")
    if not isinstance(value, str) or not value or not value.replace("_", "").isalnum():
        raise ContentError(f"{context}: 'id' must be a non-empty alphanumeric slug")
    return value


def _name(record: dict[str, Any], context: str) -> str:
    value = record.get("name")
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{context}: 'name' must be a non-empty string")
    return value


def _load_commodities(path: Path, settings: SimulationSettings) -> dict[str, Commodity]:
    commodities: dict[str, Commodity] = {}
    for index, record in enumerate(_records(_read_yaml(path), "commodities", path)):
        context = f"{path}: commodities[{index}]"
        commodity_id = _identifier(record, context)
        if commodity_id in commodities:
            raise ContentError(f"{context}: duplicate commodity id {commodity_id!r}")
        base_price = _integer(record, "base_price", context, positive=True)
        minimum_price = _integer(record, "minimum_price", context, positive=True)
        maximum_price = _integer(record, "maximum_price", context, positive=True)
        elasticity = _integer(record, "elasticity_bp", context)
        spread = _integer(record, "spread_bp", context)
        if not minimum_price <= base_price <= maximum_price:
            raise ContentError(f"{context}: expected minimum_price <= base_price <= maximum_price")
        if spread >= settings.basis_point_scale:
            raise ContentError(f"{context}: spread_bp must be below the basis-point scale")
        commodities[commodity_id] = Commodity(
            id=commodity_id,
            name=_name(record, context),
            base_price=base_price,
            minimum_price=minimum_price,
            maximum_price=maximum_price,
            elasticity_bp=elasticity,
            spread_bp=spread,
        )
    if not commodities:
        raise ContentError(f"{path}: at least one commodity is required")
    return commodities


def _load_ports(
    path: Path, commodities: dict[str, Commodity], settings: SimulationSettings
) -> dict[str, Port]:
    ports: dict[str, Port] = {}
    expected_commodities = set(commodities)
    for index, record in enumerate(_records(_read_yaml(path), "ports", path)):
        context = f"{path}: ports[{index}]"
        port_id = _identifier(record, context)
        if port_id in ports:
            raise ContentError(f"{context}: duplicate port id {port_id!r}")
        raw_inventories = record.get("inventories")
        if not isinstance(raw_inventories, dict):
            raise ContentError(f"{context}: 'inventories' must be a mapping")
        actual_commodities = set(raw_inventories)
        if actual_commodities != expected_commodities:
            missing = sorted(expected_commodities - actual_commodities)
            unknown = sorted(actual_commodities - expected_commodities)
            raise ContentError(
                f"{context}: inventory commodity mismatch; missing={missing}, unknown={unknown}"
            )
        inventories: dict[str, Inventory] = {}
        for commodity_id, inventory_record in raw_inventories.items():
            inventory_context = f"{context}.inventories.{commodity_id}"
            if not isinstance(inventory_record, dict):
                raise ContentError(f"{inventory_context} must be a mapping")
            quantity = _integer(inventory_record, "quantity", inventory_context)
            target = _integer(inventory_record, "target", inventory_context, positive=True)
            capacity = target * settings.storage_capacity_multiplier
            if quantity > capacity:
                raise ContentError(
                    f"{inventory_context}: quantity cannot exceed capacity {capacity}"
                )
            inventories[commodity_id] = Inventory(
                quantity=quantity,
                target=target,
                capacity=capacity,
                daily_production=_integer(inventory_record, "production", inventory_context),
                daily_consumption=_integer(inventory_record, "consumption", inventory_context),
            )
        ports[port_id] = Port(
            id=port_id,
            name=_name(record, context),
            treasury=settings.port_starting_treasury,
            map_x=_integer(record, "map_x", context),
            map_y=_integer(record, "map_y", context),
            inventories=inventories,
            enforcement_bp=_integer(record, "enforcement_bp", context),
        )
        if ports[port_id].enforcement_bp > settings.basis_point_scale:
            raise ContentError(f"{context}: enforcement_bp cannot exceed 10000")
        if ports[port_id].map_x > settings.map_width or ports[port_id].map_y > settings.map_height:
            raise ContentError(f"{context}: map coordinates fall outside the configured map")
    if not ports:
        raise ContentError(f"{path}: at least one port is required")
    return ports


def _load_routes(path: Path, ports: dict[str, Port]) -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for index, record in enumerate(_records(_read_yaml(path), "routes", path)):
        context = f"{path}: routes[{index}]"
        route_id = _identifier(record, context)
        if route_id in routes:
            raise ContentError(f"{context}: duplicate route id {route_id!r}")
        port_a = record.get("port_a")
        port_b = record.get("port_b")
        if port_a not in ports or port_b not in ports:
            raise ContentError(f"{context}: route references an unknown port")
        if port_a == port_b:
            raise ContentError(f"{context}: route endpoints must be different")
        routes[route_id] = Route(
            id=route_id,
            port_a=port_a,
            port_b=port_b,
            travel_ticks=_integer(record, "travel_ticks", context, positive=True),
            operating_cost=_integer(record, "operating_cost", context),
            risk_bp=_integer(record, "risk_bp", context),
            navy_patrol_bp=_integer(record, "navy_patrol_bp", context),
        )
        if routes[route_id].navy_patrol_bp > 10_000:
            raise ContentError(f"{context}: navy_patrol_bp cannot exceed 10000")
    if not routes:
        raise ContentError(f"{path}: at least one route is required")
    connected_ports = {
        endpoint for route in routes.values() for endpoint in (route.port_a, route.port_b)
    }
    disconnected = sorted(set(ports) - connected_ports)
    if disconnected:
        raise ContentError(f"{path}: disconnected ports: {disconnected}")
    return routes


def load_content(settings: SimulationSettings) -> LoadedContent:
    """Load all game content from the configured directory."""
    directory = settings.content_directory
    commodities = _load_commodities(directory / "commodities.yaml", settings)
    ports = _load_ports(directory / "ports.yaml", commodities, settings)
    routes = _load_routes(directory / "routes.yaml", ports)
    return LoadedContent(commodities=commodities, ports=ports, routes=routes)
