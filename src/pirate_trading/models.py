"""Data models for the Milestone 0 simulation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

type CommodityId = str
type PortId = str
type RouteId = str
type MerchantId = str
type Controller = Literal["ai", "player"]


@dataclass(frozen=True, slots=True)
class Commodity:
    id: CommodityId
    name: str
    base_price: int
    minimum_price: int
    maximum_price: int
    elasticity_bp: int
    spread_bp: int


@dataclass(slots=True)
class Inventory:
    quantity: int
    target: int
    capacity: int
    daily_production: int
    daily_consumption: int
    bid_price: int = 0
    ask_price: int = 0


@dataclass(slots=True)
class Port:
    id: PortId
    name: str
    treasury: int
    map_x: int
    map_y: int
    inventories: dict[CommodityId, Inventory]


@dataclass(frozen=True, slots=True)
class Route:
    id: RouteId
    port_a: PortId
    port_b: PortId
    travel_ticks: int
    operating_cost: int

    def other_end(self, port_id: PortId) -> PortId:
        if port_id == self.port_a:
            return self.port_b
        if port_id == self.port_b:
            return self.port_a
        raise ValueError(f"Port {port_id!r} is not an endpoint of route {self.id!r}")


@dataclass(slots=True)
class Voyage:
    route_id: RouteId
    destination_id: PortId
    remaining_ticks: int


@dataclass(slots=True)
class Merchant:
    id: MerchantId
    name: str
    port_id: PortId | None
    cash: int
    capacity: int
    controller: Controller
    preferences: dict[CommodityId, int]
    cargo: dict[CommodityId, int] = field(default_factory=dict)
    cargo_cost_basis: dict[CommodityId, int] = field(default_factory=dict)
    voyage: Voyage | None = None
    completed_trades: int = 0
    completed_voyages: int = 0
    realized_profit: int = 0

    @property
    def cargo_quantity(self) -> int:
        return sum(self.cargo.values())


@dataclass(frozen=True, slots=True)
class BuyCargo:
    merchant_id: MerchantId
    port_id: PortId
    commodity_id: CommodityId
    quantity: int


@dataclass(frozen=True, slots=True)
class SellCargo:
    merchant_id: MerchantId
    port_id: PortId
    commodity_id: CommodityId
    quantity: int


@dataclass(frozen=True, slots=True)
class BeginVoyage:
    merchant_id: MerchantId
    route_id: RouteId
    destination_id: PortId


type Command = BuyCargo | SellCargo | BeginVoyage


@dataclass(frozen=True, slots=True)
class DomainEvent:
    tick: int
    sequence: int
    event_type: str
    payload: dict[str, Any]

    def to_primitive(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketSample:
    tick: int
    quantity: int
    bid_price: int
    ask_price: int


@dataclass(slots=True)
class EconomicLedger:
    initial_goods: dict[CommodityId, int] = field(default_factory=dict)
    produced: dict[CommodityId, int] = field(default_factory=dict)
    consumed: dict[CommodityId, int] = field(default_factory=dict)
    overflow: dict[CommodityId, int] = field(default_factory=dict)
    unmet_demand: dict[CommodityId, int] = field(default_factory=dict)
    initial_cash: int = 0
    operating_costs: int = 0


def _command_to_primitive(command: Command) -> dict[str, Any]:
    return {"command_type": type(command).__name__, **asdict(command)}


def _command_from_primitive(data: dict[str, Any]) -> Command:
    command_type = data["command_type"]
    values = {key: value for key, value in data.items() if key != "command_type"}
    command_classes = {
        "BuyCargo": BuyCargo,
        "SellCargo": SellCargo,
        "BeginVoyage": BeginVoyage,
    }
    try:
        return command_classes[command_type](**values)
    except KeyError as error:
        raise ValueError(f"Unknown command type {command_type!r}") from error


@dataclass(slots=True)
class WorldState:
    seed: int
    tick: int
    commodities: dict[CommodityId, Commodity]
    ports: dict[PortId, Port]
    routes: dict[RouteId, Route]
    merchants: dict[MerchantId, Merchant]
    player_id: MerchantId
    ledger: EconomicLedger
    price_history: dict[PortId, dict[CommodityId, list[MarketSample]]] = field(default_factory=dict)
    recent_events: list[DomainEvent] = field(default_factory=list)
    pending_commands: list[Command] = field(default_factory=list)
    random_states: dict[str, Any] = field(default_factory=dict)
    next_event_sequence: int = 0

    def to_primitive(self) -> dict[str, Any]:
        """Return a stable, JSON-compatible representation of the world."""
        return {
            "seed": self.seed,
            "tick": self.tick,
            "commodities": {key: asdict(self.commodities[key]) for key in sorted(self.commodities)},
            "ports": {
                key: {
                    "id": port.id,
                    "name": port.name,
                    "treasury": port.treasury,
                    "map_x": port.map_x,
                    "map_y": port.map_y,
                    "inventories": {
                        commodity_id: asdict(port.inventories[commodity_id])
                        for commodity_id in sorted(port.inventories)
                    },
                }
                for key in sorted(self.ports)
                for port in [self.ports[key]]
            },
            "routes": {key: asdict(self.routes[key]) for key in sorted(self.routes)},
            "merchants": {
                key: {
                    "id": merchant.id,
                    "name": merchant.name,
                    "port_id": merchant.port_id,
                    "cash": merchant.cash,
                    "capacity": merchant.capacity,
                    "controller": merchant.controller,
                    "preferences": dict(sorted(merchant.preferences.items())),
                    "cargo": dict(sorted(merchant.cargo.items())),
                    "cargo_cost_basis": dict(sorted(merchant.cargo_cost_basis.items())),
                    "voyage": asdict(merchant.voyage) if merchant.voyage else None,
                    "completed_trades": merchant.completed_trades,
                    "completed_voyages": merchant.completed_voyages,
                    "realized_profit": merchant.realized_profit,
                }
                for key in sorted(self.merchants)
                for merchant in [self.merchants[key]]
            },
            "player_id": self.player_id,
            "ledger": asdict(self.ledger),
            "price_history": {
                port_id: {
                    commodity_id: [asdict(sample) for sample in samples]
                    for commodity_id, samples in sorted(commodities.items())
                }
                for port_id, commodities in sorted(self.price_history.items())
            },
            "recent_events": [event.to_primitive() for event in self.recent_events],
            "pending_commands": [
                _command_to_primitive(command) for command in self.pending_commands
            ],
            "random_states": self.random_states,
            "next_event_sequence": self.next_event_sequence,
        }

    @classmethod
    def from_primitive(cls, data: dict[str, Any]) -> WorldState:
        """Rebuild a world from :meth:`to_primitive` output."""
        commodities = {key: Commodity(**value) for key, value in data["commodities"].items()}
        ports = {
            key: Port(
                id=value["id"],
                name=value["name"],
                treasury=value["treasury"],
                map_x=value["map_x"],
                map_y=value["map_y"],
                inventories={
                    commodity_id: Inventory(**inventory)
                    for commodity_id, inventory in value["inventories"].items()
                },
            )
            for key, value in data["ports"].items()
        }
        routes = {key: Route(**value) for key, value in data["routes"].items()}
        merchants = {}
        for key, value in data["merchants"].items():
            voyage_data = value["voyage"]
            merchants[key] = Merchant(
                id=value["id"],
                name=value["name"],
                port_id=value["port_id"],
                cash=value["cash"],
                capacity=value["capacity"],
                controller=value["controller"],
                preferences=dict(value["preferences"]),
                cargo=dict(value["cargo"]),
                cargo_cost_basis=dict(value["cargo_cost_basis"]),
                voyage=Voyage(**voyage_data) if voyage_data else None,
                completed_trades=value["completed_trades"],
                completed_voyages=value["completed_voyages"],
                realized_profit=value["realized_profit"],
            )
        return cls(
            seed=data["seed"],
            tick=data["tick"],
            commodities=commodities,
            ports=ports,
            routes=routes,
            merchants=merchants,
            player_id=data["player_id"],
            ledger=EconomicLedger(**data["ledger"]),
            price_history={
                port_id: {
                    commodity_id: [MarketSample(**sample) for sample in samples]
                    for commodity_id, samples in commodities.items()
                }
                for port_id, commodities in data["price_history"].items()
            },
            recent_events=[DomainEvent(**event) for event in data["recent_events"]],
            pending_commands=[
                _command_from_primitive(command) for command in data["pending_commands"]
            ],
            random_states=dict(data["random_states"]),
            next_event_sequence=data["next_event_sequence"],
        )

    def state_hash(self) -> str:
        payload = json.dumps(
            self.to_primitive(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SimulationReport:
    seed: int
    completed_ticks: int
    state_hash: str
    event_counts: dict[str, int]
    rejected_commands: int
    invariant_failures: int
