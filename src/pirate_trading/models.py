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
    enforcement_bp: int = 0


@dataclass(frozen=True, slots=True)
class Route:
    id: RouteId
    port_a: PortId
    port_b: PortId
    travel_ticks: int
    operating_cost: int
    risk_bp: int
    navy_patrol_bp: int = 0

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
    progress_remainder_bp: int = 0
    hours_at_sea: int = 0
    navy_checked: bool = False


@dataclass(slots=True)
class ShipState:
    provisions: int = 0
    condition: int = 100
    wage_debt: int = 0
    next_wage_tick: int = 168
    upgrades: list[str] = field(default_factory=list)


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
    ship: ShipState = field(default_factory=ShipState)
    stolen_cargo: dict[CommodityId, int] = field(default_factory=dict)
    mission_load: dict[str, int] = field(default_factory=dict)

    @property
    def cargo_quantity(self) -> int:
        return sum(self.cargo.values()) + self.ship.provisions + sum(self.mission_load.values())


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


@dataclass(frozen=True, slots=True)
class BuyProvisions:
    merchant_id: MerchantId
    port_id: PortId
    quantity: int


@dataclass(frozen=True, slots=True)
class PayWages:
    merchant_id: MerchantId
    port_id: PortId


@dataclass(frozen=True, slots=True)
class RepairShip:
    merchant_id: MerchantId
    port_id: PortId
    points: int


@dataclass(frozen=True, slots=True)
class PurchaseUpgrade:
    merchant_id: MerchantId
    port_id: PortId
    upgrade_id: str


@dataclass(frozen=True, slots=True)
class ResolveEncounter:
    merchant_id: MerchantId
    encounter_id: str
    choice: str


@dataclass(frozen=True, slots=True)
class RespondToOpportunity:
    merchant_id: MerchantId
    opportunity_id: str
    option_id: str


@dataclass(frozen=True, slots=True)
class RaidShip:
    attacker_id: MerchantId
    target_id: MerchantId


@dataclass(frozen=True, slots=True)
class RaidPort:
    attacker_id: MerchantId
    port_id: PortId


@dataclass(frozen=True, slots=True)
class ClaimRaidLoot:
    merchant_id: MerchantId
    raid_id: str
    quantities: dict[CommodityId, int]


@dataclass(frozen=True, slots=True)
class AbandonRaidLoot:
    merchant_id: MerchantId
    raid_id: str


@dataclass(frozen=True, slots=True)
class FenceStolenCargo:
    merchant_id: MerchantId
    port_id: PortId
    commodity_id: CommodityId
    quantity: int


@dataclass(frozen=True, slots=True)
class PayOffOfficials:
    merchant_id: MerchantId
    port_id: PortId
    points: int
    method: str = "officials"


@dataclass(frozen=True, slots=True)
class AcknowledgeWeeklyOutlook:
    week_index: int


type Command = (
    BuyCargo
    | SellCargo
    | BeginVoyage
    | BuyProvisions
    | PayWages
    | RepairShip
    | PurchaseUpgrade
    | ResolveEncounter
    | RespondToOpportunity
    | RaidShip
    | RaidPort
    | ClaimRaidLoot
    | AbandonRaidLoot
    | FenceStolenCargo
    | PayOffOfficials
    | AcknowledgeWeeklyOutlook
)


@dataclass(slots=True)
class WeeklyOutlook:
    week_index: int
    start_tick: int
    end_tick: int
    die_one: int
    die_two: int
    total: int
    setting_id: str
    reveal_pending: bool = True


@dataclass(slots=True)
class ActiveCondition:
    id: str
    event_type: str
    stage: str
    start_tick: int
    transition_tick: int
    recovery_tick: int
    end_tick: int
    severity_bp: int
    port_id: str | None = None
    route_id: str | None = None
    commodity_id: str | None = None
    capacity_reduction_bp: int = 0
    capacity_reduced: bool = False


@dataclass(frozen=True, slots=True)
class MarketReport:
    source_port_id: str
    commodity_id: str
    created_tick: int
    quantity: int
    bid_price: int
    ask_price: int


@dataclass(frozen=True, slots=True)
class InformationReport:
    id: str
    created_tick: int
    source_port_id: str
    information_kind: str
    headline: str
    body: str
    sequence: int = 0
    event_id: str | None = None
    port_id: str | None = None
    route_id: str | None = None
    commodity_id: str | None = None
    is_true: bool = True
    public_effect_summary: str = ""
    deadline_tick: int | None = None
    priority: str = "normal"
    opportunity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduledReport:
    destination_port_id: str
    delivery_tick: int
    report_type: str
    payload: dict[str, Any]


@dataclass(slots=True)
class Opportunity:
    id: str
    kind: str
    title: str
    description: str
    port_id: str
    created_tick: int
    expiry_tick: int
    status: str = "offered"
    event_id: str | None = None
    commodity_id: str | None = None
    required_quantity: int = 0
    reward: int = 0
    options: list[str] = field(default_factory=list)
    chosen_option: str | None = None
    accepted_by: str | None = None
    accepted_tick: int | None = None
    objective_type: str = "delivery"
    origin_port_id: str | None = None
    destination_port_id: str | None = None
    route_id: str | None = None
    reserved_capacity: int = 0
    progress: int = 0
    public_effect_summary: str = ""
    known_to_player: bool = True
    related_report_id: str | None = None
    resolved_tick: int | None = None
    failure_reason: str | None = None
    penalty_assessed: int = 0
    penalty_paid: int = 0


@dataclass(slots=True)
class StoryProgress:
    port_id: str
    chapter_id: str
    status: str = "unseen"
    choice: str | None = None
    journal_text: str = ""


@dataclass(slots=True)
class PendingEncounter:
    id: str
    merchant_id: str
    route_id: str
    severity_bp: int
    demand: int
    resolved: bool = False
    encounter_type: str = "pirate"
    port_id: str | None = None


@dataclass(slots=True)
class PendingRaidLoot:
    id: str
    raid_type: str
    attacker_id: MerchantId
    target_merchant_id: MerchantId | None
    target_port_id: PortId | None
    available_cargo: dict[CommodityId, int]
    maximum_haul: int
    preparation_cost: int
    base_condition_damage: int
    notoriety_gain: int
    heat_gain: int
    created_tick: int
    resolved: bool = False


@dataclass(slots=True)
class PiracyState:
    notoriety: int = 0
    local_heat: dict[PortId, int] = field(default_factory=dict)
    last_raid_ticks: dict[PortId, int] = field(default_factory=dict)
    last_global_raid_tick: int = -100_000
    next_decay_tick: int = 24
    inspection_results: dict[str, str] = field(default_factory=dict)
    payoff_cooldowns: dict[str, int] = field(default_factory=dict)
    encounter_history: list[dict[str, Any]] = field(default_factory=list)


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
    provisions_consumed: int = 0
    wages_paid: int = 0
    local_consumption_revenue: int = 0
    raid_expenses: int = 0
    event_losses: dict[CommodityId, int] = field(default_factory=dict)
    piracy_losses: dict[CommodityId, int] = field(default_factory=dict)
    fines_paid: int = 0
    mission_penalties: int = 0
    confiscated_goods: dict[CommodityId, int] = field(default_factory=dict)
    salvage_recovered: dict[CommodityId, int] = field(default_factory=dict)


def _command_to_primitive(command: Command) -> dict[str, Any]:
    return {"command_type": type(command).__name__, **asdict(command)}


def _command_from_primitive(data: dict[str, Any]) -> Command:
    command_type = data["command_type"]
    values = {key: value for key, value in data.items() if key != "command_type"}
    command_classes = {
        "BuyCargo": BuyCargo,
        "SellCargo": SellCargo,
        "BeginVoyage": BeginVoyage,
        "BuyProvisions": BuyProvisions,
        "PayWages": PayWages,
        "RepairShip": RepairShip,
        "PurchaseUpgrade": PurchaseUpgrade,
        "ResolveEncounter": ResolveEncounter,
        "RespondToOpportunity": RespondToOpportunity,
        "RaidShip": RaidShip,
        "RaidPort": RaidPort,
        "ClaimRaidLoot": ClaimRaidLoot,
        "AbandonRaidLoot": AbandonRaidLoot,
        "FenceStolenCargo": FenceStolenCargo,
        "PayOffOfficials": PayOffOfficials,
        "AcknowledgeWeeklyOutlook": AcknowledgeWeeklyOutlook,
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
    weekly_outlook: WeeklyOutlook | None = None
    trade_access: dict[str, bool] = field(default_factory=dict)
    fortune_consumed: list[str] = field(default_factory=list)
    active_conditions: list[ActiveCondition] = field(default_factory=list)
    event_cooldowns: dict[str, int] = field(default_factory=dict)
    next_world_event_tick: int = 0
    port_market_reports: dict[PortId, dict[PortId, dict[CommodityId, MarketReport]]] = field(
        default_factory=dict
    )
    port_information_reports: dict[PortId, list[InformationReport]] = field(default_factory=dict)
    player_market_reports: dict[PortId, dict[CommodityId, MarketReport]] = field(
        default_factory=dict
    )
    player_information_reports: list[InformationReport] = field(default_factory=list)
    scheduled_reports: list[ScheduledReport] = field(default_factory=list)
    opportunities: dict[str, Opportunity] = field(default_factory=dict)
    story_progress: dict[PortId, StoryProgress] = field(default_factory=dict)
    pending_encounters: dict[str, PendingEncounter] = field(default_factory=dict)
    pending_raid_loot: dict[str, PendingRaidLoot] = field(default_factory=dict)
    story_modifiers: dict[str, int] = field(default_factory=dict)
    unmet_demand_streaks: dict[str, int] = field(default_factory=dict)
    raid_cooldowns: dict[str, int] = field(default_factory=dict)
    piracy_state: PiracyState = field(default_factory=PiracyState)
    next_situation_tick: int = 0
    situation_history: list[str] = field(default_factory=list)

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
                    "enforcement_bp": port.enforcement_bp,
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
                    "ship": asdict(merchant.ship),
                    "stolen_cargo": dict(sorted(merchant.stolen_cargo.items())),
                    "mission_load": dict(sorted(merchant.mission_load.items())),
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
            "weekly_outlook": asdict(self.weekly_outlook) if self.weekly_outlook else None,
            "trade_access": dict(sorted(self.trade_access.items())),
            "fortune_consumed": sorted(self.fortune_consumed),
            "active_conditions": [asdict(item) for item in self.active_conditions],
            "event_cooldowns": dict(sorted(self.event_cooldowns.items())),
            "next_world_event_tick": self.next_world_event_tick,
            "port_market_reports": {
                observer: {
                    source: {
                        commodity_id: asdict(report)
                        for commodity_id, report in sorted(commodities.items())
                    }
                    for source, commodities in sorted(sources.items())
                }
                for observer, sources in sorted(self.port_market_reports.items())
            },
            "port_information_reports": {
                port_id: [asdict(report) for report in reports]
                for port_id, reports in sorted(self.port_information_reports.items())
            },
            "player_market_reports": {
                port_id: {
                    commodity_id: asdict(report)
                    for commodity_id, report in sorted(commodities.items())
                }
                for port_id, commodities in sorted(self.player_market_reports.items())
            },
            "player_information_reports": [
                asdict(report) for report in self.player_information_reports
            ],
            "scheduled_reports": [asdict(report) for report in self.scheduled_reports],
            "opportunities": {
                key: asdict(value) for key, value in sorted(self.opportunities.items())
            },
            "story_progress": {
                key: asdict(value) for key, value in sorted(self.story_progress.items())
            },
            "pending_encounters": {
                key: asdict(value) for key, value in sorted(self.pending_encounters.items())
            },
            "pending_raid_loot": {
                key: asdict(value) for key, value in sorted(self.pending_raid_loot.items())
            },
            "story_modifiers": dict(sorted(self.story_modifiers.items())),
            "unmet_demand_streaks": dict(sorted(self.unmet_demand_streaks.items())),
            "raid_cooldowns": dict(sorted(self.raid_cooldowns.items())),
            "piracy_state": asdict(self.piracy_state),
            "next_situation_tick": self.next_situation_tick,
            "situation_history": list(self.situation_history),
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
                enforcement_bp=value.get("enforcement_bp", 0),
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
                ship=ShipState(**value.get("ship", {})),
                stolen_cargo=dict(value.get("stolen_cargo", {})),
                mission_load=dict(value.get("mission_load", {})),
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
            weekly_outlook=(
                WeeklyOutlook(**data["weekly_outlook"]) if data.get("weekly_outlook") else None
            ),
            trade_access=dict(data.get("trade_access", {})),
            fortune_consumed=list(data.get("fortune_consumed", [])),
            active_conditions=[
                ActiveCondition(**item) for item in data.get("active_conditions", [])
            ],
            event_cooldowns=dict(data.get("event_cooldowns", {})),
            next_world_event_tick=data.get("next_world_event_tick", 0),
            port_market_reports={
                observer: {
                    source: {
                        commodity_id: MarketReport(**report)
                        for commodity_id, report in commodities.items()
                    }
                    for source, commodities in sources.items()
                }
                for observer, sources in data.get("port_market_reports", {}).items()
            },
            port_information_reports={
                port_id: [InformationReport(**report) for report in reports]
                for port_id, reports in data.get("port_information_reports", {}).items()
            },
            player_market_reports={
                port_id: {
                    commodity_id: MarketReport(**report)
                    for commodity_id, report in commodities.items()
                }
                for port_id, commodities in data.get("player_market_reports", {}).items()
            },
            player_information_reports=[
                InformationReport(**report) for report in data.get("player_information_reports", [])
            ],
            scheduled_reports=[
                ScheduledReport(**report) for report in data.get("scheduled_reports", [])
            ],
            opportunities={
                key: Opportunity(**value) for key, value in data.get("opportunities", {}).items()
            },
            story_progress={
                key: StoryProgress(**value) for key, value in data.get("story_progress", {}).items()
            },
            pending_encounters={
                key: PendingEncounter(**value)
                for key, value in data.get("pending_encounters", {}).items()
            },
            pending_raid_loot={
                key: PendingRaidLoot(**value)
                for key, value in data.get("pending_raid_loot", {}).items()
            },
            story_modifiers=dict(data.get("story_modifiers", {})),
            unmet_demand_streaks=dict(data.get("unmet_demand_streaks", {})),
            raid_cooldowns=dict(data.get("raid_cooldowns", {})),
            piracy_state=PiracyState(**data.get("piracy_state", {})),
            next_situation_tick=data.get("next_situation_tick", 0),
            situation_history=list(data.get("situation_history", [])),
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
