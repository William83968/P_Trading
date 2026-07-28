"""Deterministic fixed-tick simulation engine."""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from collections.abc import Callable
from typing import Any

from pirate_trading.content import ContentError, load_content
from pirate_trading.economy import (
    DirtyPrices,
    apply_daily_economy,
    best_merchant_opportunity,
    buy_cargo,
    initialize_prices,
    recalculate_prices,
    sell_cargo,
)
from pirate_trading.models import (
    AbandonRaidLoot,
    AcknowledgeWeeklyOutlook,
    ActiveCondition,
    BeginVoyage,
    BuyCargo,
    BuyProvisions,
    ClaimRaidLoot,
    Command,
    DomainEvent,
    EconomicLedger,
    FenceStolenCargo,
    MarketSample,
    Merchant,
    Opportunity,
    PayOffOfficials,
    PayWages,
    PendingEncounter,
    PendingRaidLoot,
    PiracyState,
    PurchaseUpgrade,
    RaidPort,
    RaidShip,
    RepairShip,
    ResolveEncounter,
    RespondToOpportunity,
    SellCargo,
    ShipState,
    SimulationReport,
    Voyage,
    WorldState,
)
from pirate_trading.narrative import (
    create_event_opportunity,
    deliver_reports,
    initialize_information,
    maybe_generate_situation,
    maybe_offer_intro_story,
    merge_player_knowledge,
    publish_event_report,
    publish_market_reports,
)
from pirate_trading.settings import SETTINGS, SimulationSettings
from pirate_trading.world_events import (
    advance_conditions,
    current_outlook_rule,
    maybe_generate_event,
    roll_weekly_outlook,
    route_risk_multiplier,
    route_speed_multiplier,
)

EventSink = Callable[[DomainEvent], None]


class InvariantError(RuntimeError):
    """Raised when simulation state violates a required invariant."""


def _json_state(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_state(item) for item in value]
    if isinstance(value, list):
        return [_json_state(item) for item in value]
    return value


def _random_state(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_random_state(item) for item in value)
    return value


class RandomService:
    """Own independent, reproducible random streams derived from one seed."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        if name not in self._streams:
            digest = hashlib.sha256(f"{self.seed}:{name}".encode()).digest()
            derived_seed = int.from_bytes(digest[:16], "big")
            self._streams[name] = random.Random(derived_seed)
        return self._streams[name]

    def snapshot(self) -> dict[str, Any]:
        return {
            name: _json_state(stream.getstate()) for name, stream in sorted(self._streams.items())
        }

    def restore(self, states: dict[str, Any]) -> None:
        self._streams.clear()
        for name, state in states.items():
            stream = self.stream(name)
            stream.setstate(_random_state(state))


def load_world(settings: SimulationSettings = SETTINGS) -> WorldState:
    """Load content and construct the deterministic initial world."""
    if settings.merchant_count <= 0:
        raise ContentError("At least one AI merchant is required")
    if settings.ticks_per_day <= 0 or settings.simulation_ticks < 0:
        raise ContentError("Simulation tick settings must be positive")
    if settings.merchant_capacity <= 0 or settings.merchant_starting_cash < 0:
        raise ContentError("Merchant capacity and cash settings are invalid")

    loaded = load_content(settings)
    random_service = RandomService(settings.world_seed)
    preference_stream = random_service.stream("merchant_ai")
    merchants: dict[str, Merchant] = {}
    if settings.merchant_count != len(loaded.ports):
        raise ContentError("Milestone 2 requires one AI merchant per port")
    for index, port_id in enumerate(sorted(loaded.ports), start=1):
        merchant_id = f"merchant_{index}"
        merchants[merchant_id] = Merchant(
            id=merchant_id,
            name=f"Merchant {index}",
            port_id=port_id,
            cash=settings.merchant_starting_cash,
            capacity=settings.merchant_capacity,
            controller="ai",
            preferences={
                commodity_id: preference_stream.randint(
                    settings.merchant_preference_min, settings.merchant_preference_max
                )
                for commodity_id in sorted(loaded.commodities)
            },
            ship=ShipState(
                provisions=settings.initial_provisions,
                condition=settings.condition_maximum,
                next_wage_tick=settings.week_ticks,
            ),
        )
    if settings.player_starting_port not in loaded.ports:
        raise ContentError("Player starting port does not exist")
    merchants[settings.player_id] = Merchant(
        id=settings.player_id,
        name=settings.player_name,
        port_id=settings.player_starting_port,
        cash=settings.player_starting_cash,
        capacity=settings.player_capacity,
        controller="player",
        preferences={
            commodity_id: settings.basis_point_scale for commodity_id in sorted(loaded.commodities)
        },
        ship=ShipState(
            provisions=settings.player_initial_provisions,
            condition=settings.condition_maximum,
            next_wage_tick=settings.week_ticks,
        ),
    )

    initial_goods = {
        commodity_id: sum(port.inventories[commodity_id].quantity for port in loaded.ports.values())
        for commodity_id in loaded.commodities
    }
    initial_goods["grain"] += sum(merchant.ship.provisions for merchant in merchants.values())
    empty_goods = {commodity_id: 0 for commodity_id in loaded.commodities}
    initial_cash = sum(port.treasury for port in loaded.ports.values())
    initial_cash += sum(merchant.cash for merchant in merchants.values())
    world = WorldState(
        seed=settings.world_seed,
        tick=0,
        commodities=loaded.commodities,
        ports=loaded.ports,
        routes=loaded.routes,
        merchants=merchants,
        player_id=settings.player_id,
        ledger=EconomicLedger(
            initial_goods=initial_goods,
            produced=dict(empty_goods),
            consumed=dict(empty_goods),
            overflow=dict(empty_goods),
            unmet_demand=dict(empty_goods),
            initial_cash=initial_cash,
        ),
        random_states=random_service.snapshot(),
        next_world_event_tick=settings.event_gap_min_days * settings.ticks_per_day,
        piracy_state=PiracyState(
            local_heat={port_id: 0 for port_id in loaded.ports},
            last_raid_ticks={port_id: -100_000 for port_id in loaded.ports},
            next_decay_tick=settings.ticks_per_day,
        ),
        next_situation_tick=random_service.stream("situations").randint(
            settings.situation_gap_min_ticks,
            settings.situation_gap_max_ticks,
        ),
    )
    world.weekly_outlook = roll_weekly_outlook(0, settings, random_service.stream("weekly_outlook"))
    world.random_states = random_service.snapshot()
    world.ledger.event_losses = dict(empty_goods)
    world.ledger.piracy_losses = dict(empty_goods)
    world.ledger.confiscated_goods = dict(empty_goods)
    world.ledger.salvage_recovered = dict(empty_goods)
    initialize_prices(world, settings)
    world.price_history = {
        port_id: {
            commodity_id: [
                MarketSample(
                    tick=0,
                    quantity=inventory.quantity,
                    bid_price=inventory.bid_price,
                    ask_price=inventory.ask_price,
                )
            ]
            for commodity_id, inventory in port.inventories.items()
        }
        for port_id, port in world.ports.items()
    }
    initialize_information(world)
    return world


class SimulationEngine:
    """Coordinate the deterministic living-world simulation."""

    def __init__(
        self,
        world: WorldState,
        settings: SimulationSettings = SETTINGS,
        event_sink: EventSink | None = None,
    ) -> None:
        self.world = world
        self.settings = settings
        self.event_sink = event_sink
        self.random_service = RandomService(world.seed)
        self.random_service.restore(world.random_states)
        self.event_counts: Counter[str] = Counter()
        self.rejected_commands = 0
        self.invariant_failures = 0
        self._step_events: list[DomainEvent] = []

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = DomainEvent(
            tick=self.world.tick,
            sequence=self.world.next_event_sequence,
            event_type=event_type,
            payload=payload,
        )
        self.world.next_event_sequence += 1
        self._step_events.append(event)
        self.world.recent_events.append(event)
        del self.world.recent_events[: -self.settings.recent_event_limit]
        self.event_counts[event_type] += 1

    def _reject(self, command: Command, reason: str) -> None:
        self.rejected_commands += 1
        self._emit(
            "CommandRejected",
            {"command": type(command).__name__, "reason": reason},
        )

    def _heat_level(self, port_id: str) -> int:
        port = self.world.ports[port_id]
        if port.enforcement_bp == 0:
            return 0
        local = self.world.piracy_state.local_heat.get(port_id, 0)
        return max(local, self.world.piracy_state.notoriety)

    def _heat_status(self, port_id: str) -> str:
        level = self._heat_level(port_id)
        if level >= 75:
            return "hunted"
        if level >= 50:
            return "wanted"
        if level >= 25:
            return "watched"
        return "clear"

    def _add_piracy_pressure(self, port_ids: tuple[str, ...], notoriety: int, heat: int) -> None:
        state = self.world.piracy_state
        state.notoriety = min(100, state.notoriety + notoriety)
        state.last_global_raid_tick = self.world.tick
        for port_id in port_ids:
            state.local_heat[port_id] = min(100, state.local_heat.get(port_id, 0) + heat)
            state.last_raid_ticks[port_id] = self.world.tick
        self._emit(
            "PiracyPressureChanged",
            {
                "notoriety": state.notoriety,
                "ports": list(port_ids),
                "heat_gain": heat,
            },
        )

    def _lawful_service_reason(self, port_id: str, service: str) -> str | None:
        status = self._heat_status(port_id)
        if status == "hunted" and service == "trade":
            return "Hunted captains are barred from public trading here"
        if status in {"wanted", "hunted"} and service == "upgrade":
            return "local shipwrights refuse service to wanted captains"
        return None

    def _trade_access_allowed(self, merchant_id: str, port_id: str) -> bool:
        day = self.world.tick // self.settings.ticks_per_day
        key = f"{day}:{merchant_id}:{port_id}"
        if key not in self.world.trade_access:
            rule = current_outlook_rule(self.world)
            chance = rule.trade_access_bp
            if merchant_id == self.world.player_id:
                status = self._heat_status(port_id)
                chance -= {"watched": 1_000, "wanted": 2_500}.get(status, 0)
            roll = self.random_service.stream("trade_access").randint(1, 10_000)
            self.world.trade_access[key] = roll <= max(0, chance)
            self._emit(
                "TradeAccessResolved",
                {
                    "merchant_id": merchant_id,
                    "port_id": port_id,
                    "day": day,
                    "success": self.world.trade_access[key],
                    "chance_bp": max(0, chance),
                    "setting_id": rule.setting_id,
                },
            )
        return self.world.trade_access[key]

    def _trade_disruption_cause(self, merchant_id: str, port_id: str) -> str:
        if merchant_id == self.world.player_id:
            last_raid = self.world.piracy_state.last_raid_ticks.get(port_id, -100_000)
            if last_raid // self.settings.ticks_per_day == (
                self.world.tick // self.settings.ticks_per_day
            ):
                return "local_raid_alert"
        return "weekly_market_disruption"

    def _favorable_trade_price(
        self,
        merchant_id: str,
        unit_price: int,
        *,
        buying: bool,
    ) -> tuple[int, str | None]:
        rule = current_outlook_rule(self.world)
        if rule.total != 12:
            return unit_price, None
        day = self.world.tick // self.settings.ticks_per_day
        key = f"{day}:{merchant_id}"
        if key in self.world.fortune_consumed:
            return unit_price, None
        adjusted = (
            unit_price * 9_500 // 10_000 if buying else (unit_price * 10_500 + 9_999) // 10_000
        )
        return max(1, adjusted), key

    def _apply_service_command(self, command: Command, dirty: DirtyPrices) -> bool:
        if isinstance(command, BuyProvisions):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            if (
                merchant is None
                or port is None
                or merchant.port_id != command.port_id
                or merchant.voyage is not None
            ):
                self._reject(command, "merchant is not docked at this port")
                return True
            grain = port.inventories["grain"]
            if command.quantity <= 0 or grain.quantity < command.quantity:
                self._reject(command, "invalid or unavailable provision quantity")
                return True
            if merchant.cargo_quantity + command.quantity > merchant.capacity:
                self._reject(command, "provisions would exceed cargo capacity")
                return True
            cost = grain.ask_price * command.quantity
            if merchant.cash < cost:
                self._reject(command, "merchant cannot afford provisions")
                return True
            merchant.cash -= cost
            port.treasury += cost
            grain.quantity -= command.quantity
            merchant.ship.provisions += command.quantity
            dirty.setdefault((port.id, "grain"), set()).add("provisions_purchase")
            self._emit(
                "ProvisionsPurchased",
                {
                    "merchant_id": merchant.id,
                    "port_id": port.id,
                    "quantity": command.quantity,
                    "cost": cost,
                },
            )
            return True
        if isinstance(command, PayWages):
            merchant = self.world.merchants.get(command.merchant_id)
            if (
                merchant is None
                or merchant.port_id != command.port_id
                or merchant.ship.wage_debt <= 0
            ):
                self._reject(command, "no payable wage debt at this port")
                return True
            payment = min(merchant.cash, merchant.ship.wage_debt)
            if payment <= 0:
                self._reject(command, "merchant cannot pay wages")
                return True
            merchant.cash -= payment
            merchant.ship.wage_debt -= payment
            self.world.ledger.wages_paid += payment
            self._emit("WagesPaid", {"merchant_id": merchant.id, "amount": payment})
            return True
        if isinstance(command, RepairShip):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            if merchant is None or port is None or merchant.port_id != port.id:
                self._reject(command, "merchant is not docked at this port")
                return True
            points = min(
                command.points,
                self.settings.condition_maximum - merchant.ship.condition,
            )
            multiplier = {"wanted": 15_000, "hunted": 20_000}.get(
                self._heat_status(port.id), 10_000
            )
            cost = points * self.settings.repair_cost_per_point * multiplier // 10_000
            if points <= 0 or merchant.cash < cost:
                self._reject(command, "invalid repair or insufficient cash")
                return True
            merchant.cash -= cost
            port.treasury += cost
            merchant.ship.condition += points
            self._emit(
                "ShipRepaired",
                {"merchant_id": merchant.id, "points": points, "cost": cost},
            )
            return True
        if isinstance(command, PurchaseUpgrade):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            costs = {
                "expanded_hold": self.settings.upgrade_expanded_hold_cost,
                "fine_sails": self.settings.upgrade_fine_sails_cost,
                "reinforced_hull": self.settings.upgrade_reinforced_hull_cost,
                "crows_nest": self.settings.upgrade_crows_nest_cost,
            }
            cost = costs.get(command.upgrade_id)
            if merchant is None or port is None or merchant.port_id != port.id or cost is None:
                self._reject(command, "unknown upgrade or invalid location")
                return True
            restriction = self._lawful_service_reason(port.id, "upgrade")
            if restriction:
                self._reject(command, restriction)
                return True
            if command.upgrade_id in merchant.ship.upgrades:
                self._reject(command, "upgrade is already installed")
                return True
            if merchant.ship.wage_debt or merchant.cash < cost:
                self._reject(command, "pay wages and provide sufficient cash first")
                return True
            merchant.cash -= cost
            port.treasury += cost
            merchant.ship.upgrades.append(command.upgrade_id)
            merchant.ship.upgrades.sort()
            if command.upgrade_id == "expanded_hold":
                merchant.capacity += 10
            self._emit(
                "UpgradePurchased",
                {
                    "merchant_id": merchant.id,
                    "upgrade_id": command.upgrade_id,
                    "cost": cost,
                },
            )
            return True
        if isinstance(command, FenceStolenCargo):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            stolen = merchant.stolen_cargo.get(command.commodity_id, 0) if merchant else 0
            if (
                merchant is None
                or port is None
                or port.id != "blackwater_cay"
                or merchant.port_id != port.id
            ):
                self._reject(command, "stolen cargo can only be fenced at Blackwater Cay")
                return True
            quantity = command.quantity
            if quantity <= 0 or stolen < quantity:
                self._reject(command, "selected stolen cargo is unavailable")
                return True
            inventory = port.inventories.get(command.commodity_id)
            if inventory is None or inventory.quantity + quantity > inventory.capacity:
                self._reject(command, "Blackwater storage cannot take that cargo")
                return True
            price = inventory.bid_price * self.settings.fence_price_bp // 10_000
            proceeds = price * quantity
            if port.treasury < proceeds:
                self._reject(command, "Blackwater's treasury cannot cover the deal")
                return True
            merchant.cargo[command.commodity_id] -= quantity
            merchant.stolen_cargo[command.commodity_id] -= quantity
            if merchant.cargo[command.commodity_id] == 0:
                merchant.cargo.pop(command.commodity_id)
                merchant.cargo_cost_basis.pop(command.commodity_id, None)
            if merchant.stolen_cargo[command.commodity_id] == 0:
                merchant.stolen_cargo.pop(command.commodity_id)
            inventory.quantity += quantity
            port.treasury -= proceeds
            merchant.cash += proceeds
            dirty.setdefault((port.id, command.commodity_id), set()).add("fenced_cargo")
            self._emit(
                "StolenCargoFenced",
                {
                    "merchant_id": merchant.id,
                    "commodity_id": command.commodity_id,
                    "quantity": quantity,
                    "proceeds": proceeds,
                },
            )
            return True
        if isinstance(command, PayOffOfficials):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            state = self.world.piracy_state
            key = f"{command.method}:{merchant.id if merchant else ''}:{command.port_id}"
            if merchant is None or port is None or merchant.port_id != port.id:
                self._reject(command, "captain must be docked to arrange this service")
                return True
            if state.payoff_cooldowns.get(key, 0) > self.world.tick:
                self._reject(command, "this service is available only once per week")
                return True
            if command.method == "lay_low":
                if port.id != "blackwater_cay" or merchant.cash < self.settings.lay_low_cost:
                    self._reject(command, "Lay Low costs $100 and is available only at Blackwater")
                    return True
                merchant.cash -= self.settings.lay_low_cost
                self.world.ledger.operating_costs += self.settings.lay_low_cost
                state.notoriety = max(0, state.notoriety - 10)
                state.local_heat = {
                    item: max(0, value - 5) for item, value in state.local_heat.items()
                }
                removed = 10
                cost = self.settings.lay_low_cost
            else:
                if port.enforcement_bp == 0 or self._heat_status(port.id) == "hunted":
                    self._reject(command, "official payoff is unavailable at this port")
                    return True
                removed = min(20, max(0, command.points), state.local_heat.get(port.id, 0))
                cost = removed * self.settings.payoff_cost_per_point
                if removed <= 0 or merchant.cash < cost:
                    self._reject(command, "invalid payoff or insufficient cash")
                    return True
                merchant.cash -= cost
                port.treasury += cost
                state.local_heat[port.id] -= removed
            state.payoff_cooldowns[key] = (
                self.world.tick + self.settings.piracy_service_cooldown_ticks
            )
            self._emit(
                "PiracyPressureReduced",
                {
                    "merchant_id": merchant.id,
                    "method": command.method,
                    "removed": removed,
                    "cost": cost,
                },
            )
            return True
        return False

    def _apply_encounter_command(self, command: ResolveEncounter) -> None:
        encounter = self.world.pending_encounters.get(command.encounter_id)
        merchant = self.world.merchants.get(command.merchant_id)
        if (
            encounter is None
            or merchant is None
            or encounter.merchant_id != merchant.id
            or encounter.resolved
        ):
            self._reject(command, "unknown or resolved encounter")
            return
        if command.choice not in {"flee", "pay", "surrender", "bluff"}:
            self._reject(command, "unknown encounter choice")
            return
        stream = self.random_service.stream("encounters")
        success = True
        if encounter.encounter_type == "navy":
            self._resolve_navy_encounter(command, encounter, merchant)
            return
        if command.choice == "pay":
            payment = min(merchant.cash, encounter.demand)
            merchant.cash -= payment
            self.world.ledger.operating_costs += payment
        elif command.choice == "surrender":
            self._lose_pirate_cargo(merchant, max(1, encounter.severity_bp // 1_000))
        else:
            chance = 5_000 if command.choice == "flee" else self.settings.base_bluff_success_bp
            if "fine_sails" in merchant.ship.upgrades and command.choice == "flee":
                chance += 1_500
            if "crows_nest" in merchant.ship.upgrades:
                chance += 1_000
            if merchant.ship.wage_debt:
                chance -= 500
            chance -= encounter.severity_bp // 4
            success = stream.randint(1, 10_000) <= max(500, min(9_500, chance))
            if not success:
                damage = max(1, encounter.severity_bp // 1_500)
                if "reinforced_hull" in merchant.ship.upgrades:
                    damage = max(1, damage * 7 // 10)
                merchant.ship.condition = max(0, merchant.ship.condition - damage)
                self._lose_pirate_cargo(merchant, max(1, damage // 2))
        encounter.resolved = True
        self._emit(
            "EncounterResolved",
            {
                "encounter_id": encounter.id,
                "merchant_id": merchant.id,
                "choice": command.choice,
                "success": success,
            },
        )

    def _confiscate_stolen(self, merchant: Merchant, maximum_units: int) -> int:
        confiscated = 0
        for commodity_id in sorted(
            merchant.stolen_cargo,
            key=lambda item: (-self.world.commodities[item].base_price, item),
        ):
            quantity = min(maximum_units - confiscated, merchant.stolen_cargo[commodity_id])
            if quantity <= 0:
                continue
            held = merchant.cargo.get(commodity_id, 0)
            basis = merchant.cargo_cost_basis.get(commodity_id, 0)
            merchant.cargo[commodity_id] = held - quantity
            merchant.cargo_cost_basis[commodity_id] = basis
            merchant.stolen_cargo[commodity_id] -= quantity
            self.world.ledger.confiscated_goods[commodity_id] += quantity
            if merchant.cargo[commodity_id] == 0:
                merchant.cargo.pop(commodity_id)
                merchant.cargo_cost_basis.pop(commodity_id, None)
            if merchant.stolen_cargo[commodity_id] == 0:
                merchant.stolen_cargo.pop(commodity_id)
            confiscated += quantity
            if confiscated >= maximum_units:
                break
        return confiscated

    def _resolve_navy_encounter(
        self,
        command: ResolveEncounter,
        encounter: PendingEncounter,
        merchant: Merchant,
    ) -> None:
        state = self.world.piracy_state
        route = self.world.routes[encounter.route_id]
        port_id = encounter.port_id or route.port_a
        success = True
        amount = 0
        confiscated = 0
        if command.choice == "pay":
            amount = min(merchant.cash, encounter.demand)
            if amount < encounter.demand:
                self._reject(command, "captain cannot afford the navy fine")
                return
            merchant.cash -= amount
            self.world.ledger.fines_paid += amount
            state.notoriety = max(0, state.notoriety - 5)
            state.local_heat[port_id] = max(0, state.local_heat.get(port_id, 0) - 10)
        elif command.choice == "surrender":
            confiscated = self._confiscate_stolen(
                merchant, max(1, sum(merchant.stolen_cargo.values()))
            )
            state.local_heat[port_id] = max(0, state.local_heat.get(port_id, 0) - 20)
            state.notoriety = max(0, state.notoriety - 8)
        else:
            chance = 5_000 if command.choice == "flee" else self.settings.base_bluff_success_bp
            if command.choice == "flee" and "fine_sails" in merchant.ship.upgrades:
                chance += 1_500
            if "crows_nest" in merchant.ship.upgrades:
                chance += 1_000
            chance -= route.navy_patrol_bp * 2
            chance -= self._heat_level(port_id) * 25
            success = self.random_service.stream("navy_encounters").randint(1, 10_000) <= max(
                500, min(9_000, chance)
            )
            if success:
                state.local_heat[port_id] = min(100, state.local_heat.get(port_id, 0) + 8)
            else:
                damage = max(1, encounter.severity_bp // 1_500)
                merchant.ship.condition = max(0, merchant.ship.condition - damage)
                confiscated = self._confiscate_stolen(merchant, max(1, damage))
                if command.choice == "bluff":
                    amount = min(merchant.cash, encounter.demand)
                    merchant.cash -= amount
                    self.world.ledger.fines_paid += amount
        encounter.resolved = True
        history = {
            "tick": self.world.tick,
            "encounter_id": encounter.id,
            "choice": command.choice,
            "success": success,
            "fine": amount,
            "confiscated": confiscated,
        }
        state.encounter_history.append(history)
        del state.encounter_history[:-100]
        day = self.world.tick // self.settings.ticks_per_day
        state.inspection_results[f"{day}:{merchant.id}:{route.id}"] = (
            f"{command.choice}:{'success' if success else 'failure'}"
        )
        self._emit("NavyEncounterResolved", history)

    def _lose_pirate_cargo(self, merchant: Merchant, units: int) -> None:
        units = min(units, self.settings.incoming_raid_loss_limit)
        for commodity_id in sorted(
            merchant.cargo,
            key=lambda item: self.world.commodities[item].base_price,
            reverse=True,
        ):
            lost = min(units, merchant.cargo[commodity_id])
            if lost <= 0:
                continue
            held = merchant.cargo[commodity_id]
            basis = merchant.cargo_cost_basis.get(commodity_id, 0)
            stolen_before = merchant.stolen_cargo.get(commodity_id, 0)
            clean_before = held - stolen_before
            stolen_lost = min(lost, stolen_before)
            clean_lost = lost - stolen_lost
            basis_lost = (
                basis
                if clean_lost == clean_before
                else basis * clean_lost // clean_before
                if clean_before
                else 0
            )
            merchant.cargo[commodity_id] -= lost
            merchant.cargo_cost_basis[commodity_id] = basis - basis_lost
            if stolen_lost:
                merchant.stolen_cargo[commodity_id] -= stolen_lost
                if merchant.stolen_cargo[commodity_id] == 0:
                    merchant.stolen_cargo.pop(commodity_id)
            if merchant.cargo[commodity_id] == 0:
                del merchant.cargo[commodity_id]
                merchant.cargo_cost_basis.pop(commodity_id, None)
            self.world.ledger.piracy_losses[commodity_id] += lost
            units -= lost
            if units == 0:
                break

    def _apply_opportunity_command(
        self,
        command: RespondToOpportunity,
        dirty: DirtyPrices,
    ) -> None:
        opportunity = self.world.opportunities.get(command.opportunity_id)
        merchant = self.world.merchants.get(command.merchant_id)
        if opportunity is None or merchant is None:
            self._reject(command, "opportunity is unavailable")
            return
        if opportunity.kind in {"contract", "situation"}:
            if opportunity.status == "offered":
                if (
                    command.option_id not in {"accept", "decline"}
                    or merchant.port_id != opportunity.port_id
                ):
                    self._reject(
                        command,
                        "visit the issuing port to accept or decline this contract",
                    )
                    return
                if command.option_id == "decline":
                    opportunity.status = "declined"
                    opportunity.chosen_option = command.option_id
                    self._emit(
                        "OpportunityResolved",
                        {
                            "opportunity_id": opportunity.id,
                            "option_id": command.option_id,
                        },
                    )
                    return
                if (
                    opportunity.reserved_capacity
                    and merchant.cargo_quantity + opportunity.reserved_capacity > merchant.capacity
                ):
                    self._reject(command, "insufficient free hold capacity for this mission")
                    return
                opportunity.status = "accepted"
                opportunity.accepted_by = merchant.id
                opportunity.accepted_tick = self.world.tick
                opportunity.chosen_option = "accept"
                opportunity.resolved_tick = None
                opportunity.failure_reason = None
                opportunity.penalty_assessed = 0
                opportunity.penalty_paid = 0
                if opportunity.reserved_capacity:
                    merchant.mission_load[opportunity.id] = opportunity.reserved_capacity
                self._emit(
                    "OpportunityAccepted",
                    {
                        "opportunity_id": opportunity.id,
                        "merchant_id": merchant.id,
                        "port_id": opportunity.port_id,
                    },
                )
                return
            if opportunity.status != "accepted" or opportunity.accepted_by != merchant.id:
                self._reject(command, "contract is not active for this captain")
                return
            if command.option_id == "abandon":
                opportunity.status = "declined"
                opportunity.chosen_option = "abandon"
                merchant.mission_load.pop(opportunity.id, None)
                penalty_assessed, penalty_paid = self._apply_mission_failure_penalty(
                    opportunity,
                    merchant,
                )
                opportunity.resolved_tick = self.world.tick
                opportunity.failure_reason = "abandoned"
                opportunity.penalty_assessed = penalty_assessed
                opportunity.penalty_paid = penalty_paid
                self._emit(
                    "OpportunityFailed",
                    {
                        "opportunity_id": opportunity.id,
                        "merchant_id": merchant.id,
                        "reason": "abandoned",
                        "penalty_assessed": penalty_assessed,
                        "penalty_paid": penalty_paid,
                    },
                )
                self._emit(
                    "OpportunityResolved",
                    {
                        "opportunity_id": opportunity.id,
                        "option_id": "abandon",
                    },
                )
                return
            if opportunity.objective_type in {
                "reconnaissance",
                "evacuation",
                "protection",
                "smuggling",
            }:
                self._reject(command, "complete this objective through the required voyage")
                return
            destination = opportunity.destination_port_id or opportunity.port_id
            if command.option_id not in {"deliver", "recover"} or merchant.port_id != destination:
                self._reject(command, "visit the objective destination to complete this contract")
                return
            if opportunity.objective_type == "salvage":
                commodity_id = opportunity.commodity_id or "timber"
                available = self.world.ledger.event_losses.get(
                    commodity_id, 0
                ) - self.world.ledger.salvage_recovered.get(commodity_id, 0)
                recovered = min(
                    5,
                    available,
                    merchant.capacity - merchant.cargo_quantity,
                )
                if recovered <= 0:
                    self._reject(command, "no declared event loss can be recovered")
                    return
                merchant.cargo[commodity_id] = merchant.cargo.get(commodity_id, 0) + recovered
                merchant.cargo_cost_basis.setdefault(commodity_id, 0)
                self.world.ledger.salvage_recovered[commodity_id] += recovered
                opportunity.progress = recovered
                merchant.cash += opportunity.reward
                self.world.ledger.initial_cash += opportunity.reward
                opportunity.status = "resolved"
                opportunity.chosen_option = "recover"
                opportunity.resolved_tick = self.world.tick
                merchant.mission_load.pop(opportunity.id, None)
                self._emit(
                    "OpportunityCompleted",
                    {
                        "opportunity_id": opportunity.id,
                        "merchant_id": merchant.id,
                        "reward": opportunity.reward,
                        "progress": recovered,
                    },
                )
                return
            commodity_id = opportunity.commodity_id
            held = merchant.cargo.get(commodity_id or "", 0)
            clean = held - merchant.stolen_cargo.get(commodity_id or "", 0)
            if commodity_id is None or clean < opportunity.required_quantity:
                self._reject(command, "required cargo is not in the hold")
                return
            merchant.cargo[commodity_id] -= opportunity.required_quantity
            basis = merchant.cargo_cost_basis.get(commodity_id, 0)
            removed_basis = basis * opportunity.required_quantity // clean
            merchant.cargo_cost_basis[commodity_id] = basis - removed_basis
            if merchant.cargo[commodity_id] == 0:
                del merchant.cargo[commodity_id]
                merchant.cargo_cost_basis.pop(commodity_id, None)
            self.world.ledger.consumed[commodity_id] += opportunity.required_quantity
            merchant.cash += opportunity.reward
            self.world.ledger.initial_cash += opportunity.reward
            opportunity.status = "resolved"
            opportunity.chosen_option = "deliver"
            opportunity.progress = opportunity.required_quantity
            opportunity.resolved_tick = self.world.tick
            merchant.mission_load.pop(opportunity.id, None)
            self._emit(
                "OpportunityCompleted",
                {
                    "opportunity_id": opportunity.id,
                    "merchant_id": merchant.id,
                    "reward": opportunity.reward,
                    "progress": opportunity.progress,
                },
            )
        elif (
            opportunity.status == "offered"
            and merchant.port_id == opportunity.port_id
            and command.option_id in opportunity.options
            and opportunity.kind == "story"
        ):
            progress = self.world.story_progress[opportunity.port_id]
            progress.status = "resolved"
            progress.choice = command.option_id
            progress.journal_text = (
                f"{opportunity.title}: chose {command.option_id.replace('_', ' ')}."
            )
            self.world.story_modifiers[command.option_id] = 1
            if command.option_id == "turn_over_network":
                merchant.cash += 1_500
                self.world.ledger.initial_cash += 1_500
            if command.option_id in {"salt_concession", "distressed_coffee"}:
                commodity_id = "salt" if command.option_id == "salt_concession" else "coffee"
                port = self.world.ports[opportunity.port_id]
                quantity = min(
                    10,
                    merchant.capacity - merchant.cargo_quantity,
                    port.inventories[commodity_id].quantity,
                )
                if quantity:
                    port.inventories[commodity_id].quantity -= quantity
                    merchant.cargo[commodity_id] = merchant.cargo.get(commodity_id, 0) + quantity
                    merchant.cargo_cost_basis.setdefault(commodity_id, 0)
                    dirty.setdefault((port.id, commodity_id), set()).add(command.option_id)
            opportunity.status = "resolved" if command.option_id != "decline" else "declined"
            opportunity.chosen_option = command.option_id
        elif (
            opportunity.status == "offered"
            and merchant.port_id == opportunity.port_id
            and command.option_id in opportunity.options
            and opportunity.kind == "fortune"
        ):
            if command.option_id == "accept":
                merchant.cash += opportunity.reward
                self.world.ledger.initial_cash += opportunity.reward
                opportunity.status = "resolved"
            else:
                opportunity.status = "declined"
            opportunity.chosen_option = command.option_id
        else:
            self._reject(command, "opportunity is unavailable at this location")
            return
        self._emit(
            "OpportunityResolved",
            {
                "opportunity_id": opportunity.id,
                "option_id": command.option_id,
            },
        )

    def _apply_mission_failure_penalty(
        self,
        opportunity: Opportunity,
        merchant: Merchant,
    ) -> tuple[int, int]:
        assessed = max(
            self.settings.mission_failure_minimum,
            opportunity.reward * self.settings.mission_failure_penalty_bp // 10_000,
        )
        paid = min(merchant.cash, assessed)
        merchant.cash -= paid
        self.world.ledger.mission_penalties += paid
        return assessed, paid

    def _raid_damage(self, raw_damage: int, merchant: Merchant) -> int:
        if "reinforced_hull" in merchant.ship.upgrades:
            return max(1, (raw_damage * 7 + 9) // 10)
        return raw_damage

    def _create_pending_raid_loot(
        self,
        *,
        raid_type: str,
        attacker: Merchant,
        target: Merchant | None = None,
        port_id: str | None = None,
        preparation_cost: int,
        base_damage: int,
        notoriety_gain: int,
        heat_gain: int,
    ) -> PendingRaidLoot:
        if target is not None:
            available = {
                commodity_id: quantity
                for commodity_id, quantity in sorted(target.cargo.items())
                if quantity > 0
            }
            maximum_haul = self.settings.ship_raid_haul_limit
        else:
            port = self.world.ports[port_id or ""]
            available = {
                commodity_id: inventory.quantity
                for commodity_id, inventory in sorted(port.inventories.items())
                if inventory.quantity > 0
            }
            maximum_haul = self.settings.port_raid_haul_limit
        raid_id = f"raid_loot_{self.world.next_event_sequence}_{attacker.id}"
        pending = PendingRaidLoot(
            id=raid_id,
            raid_type=raid_type,
            attacker_id=attacker.id,
            target_merchant_id=target.id if target else None,
            target_port_id=port_id,
            available_cargo=available,
            maximum_haul=maximum_haul,
            preparation_cost=preparation_cost,
            base_condition_damage=base_damage,
            notoriety_gain=notoriety_gain,
            heat_gain=heat_gain,
            created_tick=self.world.tick,
        )
        self.world.pending_raid_loot[pending.id] = pending
        attacker.ship.condition = max(
            0,
            attacker.ship.condition - self._raid_damage(base_damage, attacker),
        )
        return pending

    def _apply_raid_loot_command(self, command: Command, dirty: DirtyPrices) -> bool:
        if not isinstance(command, (ClaimRaidLoot, AbandonRaidLoot)):
            return False
        pending = self.world.pending_raid_loot.get(command.raid_id)
        merchant = self.world.merchants.get(command.merchant_id)
        if (
            pending is None
            or pending.resolved
            or merchant is None
            or pending.attacker_id != merchant.id
        ):
            self._reject(command, "raid loot decision is unavailable")
            return True
        if isinstance(command, AbandonRaidLoot):
            pending.resolved = True
            self._emit(
                "RaidLootAbandoned",
                {"raid_id": pending.id, "merchant_id": merchant.id},
            )
            self.world.pending_raid_loot.pop(pending.id)
            return True

        quantities = dict(command.quantities)
        if not quantities or any(
            commodity_id not in self.world.commodities
            or not isinstance(quantity, int)
            or quantity < 0
            for commodity_id, quantity in quantities.items()
        ):
            self._reject(command, "loot selection contains invalid quantities")
            return True
        quantities = {key: value for key, value in quantities.items() if value > 0}
        total = sum(quantities.values())
        if total <= 0:
            self._reject(command, "select at least one unit or leave the loot")
            return True
        if total > pending.maximum_haul:
            self._reject(command, "loot selection exceeds this raid's haul limit")
            return True
        if merchant.cargo_quantity + total > merchant.capacity:
            self._reject(command, "loot selection exceeds free hold capacity")
            return True
        if any(
            quantity > pending.available_cargo.get(commodity_id, 0)
            for commodity_id, quantity in quantities.items()
        ):
            self._reject(command, "selected loot is no longer available")
            return True

        if pending.target_merchant_id is not None:
            source = self.world.merchants[pending.target_merchant_id]
            if any(
                quantity > source.cargo.get(commodity_id, 0)
                for commodity_id, quantity in quantities.items()
            ):
                self._reject(command, "target ship no longer carries the selected loot")
                return True
        else:
            port = self.world.ports[pending.target_port_id or ""]
            if any(
                quantity > port.inventories[commodity_id].quantity
                for commodity_id, quantity in quantities.items()
            ):
                self._reject(command, "port no longer holds the selected loot")
                return True

        for commodity_id, quantity in sorted(quantities.items()):
            if pending.target_merchant_id is not None:
                source = self.world.merchants[pending.target_merchant_id]
                held = source.cargo[commodity_id]
                basis = source.cargo_cost_basis.get(commodity_id, 0)
                stolen_before = source.stolen_cargo.get(commodity_id, 0)
                clean_before = held - stolen_before
                stolen = min(quantity, stolen_before)
                clean_moved = quantity - stolen
                removed_basis = (
                    basis
                    if clean_moved == clean_before
                    else basis * clean_moved // clean_before
                    if clean_before
                    else 0
                )
                source.cargo[commodity_id] -= quantity
                source.cargo_cost_basis[commodity_id] = basis - removed_basis
                if stolen:
                    source.stolen_cargo[commodity_id] -= stolen
                    if source.stolen_cargo[commodity_id] == 0:
                        source.stolen_cargo.pop(commodity_id)
                if source.cargo[commodity_id] == 0:
                    source.cargo.pop(commodity_id)
                    source.cargo_cost_basis.pop(commodity_id, None)
            else:
                port = self.world.ports[pending.target_port_id or ""]
                port.inventories[commodity_id].quantity -= quantity
                dirty.setdefault((port.id, commodity_id), set()).add("player_port_raid")
            merchant.cargo[commodity_id] = merchant.cargo.get(commodity_id, 0) + quantity
            merchant.cargo_cost_basis.setdefault(commodity_id, 0)
            merchant.stolen_cargo[commodity_id] = (
                merchant.stolen_cargo.get(commodity_id, 0) + quantity
            )

        divisor = 4 if pending.raid_type == "ship" else 3
        total_raw_damage = pending.base_condition_damage + (total + divisor - 1) // divisor
        additional_damage = self._raid_damage(total_raw_damage, merchant) - self._raid_damage(
            pending.base_condition_damage, merchant
        )
        merchant.ship.condition = max(0, merchant.ship.condition - additional_damage)
        pending.resolved = True
        self._emit(
            "RaidLootClaimed",
            {
                "raid_id": pending.id,
                "merchant_id": merchant.id,
                "raid_type": pending.raid_type,
                "quantities": dict(sorted(quantities.items())),
                "total_quantity": total,
                "condition_damage": self._raid_damage(total_raw_damage, merchant),
            },
        )
        self.world.pending_raid_loot.pop(pending.id)
        return True

    def _apply_raid_command(self, command: Command, dirty: DirtyPrices) -> bool:
        if isinstance(command, RaidShip):
            attacker = self.world.merchants.get(command.attacker_id)
            target = self.world.merchants.get(command.target_id)
            if (
                attacker is None
                or target is None
                or attacker.controller != "player"
                or target.controller != "ai"
                or attacker.voyage is None
                or target.voyage is None
                or attacker.voyage.route_id != target.voyage.route_id
            ):
                self._reject(command, "target ship is not within raiding range")
                return True
            if any(
                not item.resolved and item.attacker_id == attacker.id
                for item in self.world.pending_raid_loot.values()
            ):
                self._reject(command, "resolve the previous raid loot before raiding again")
                return True
            if attacker.cash < self.settings.ship_raid_cost or attacker.ship.condition <= 20:
                self._reject(command, "raiding requires $5 and ship condition above 20")
                return True
            attacker.cash -= self.settings.ship_raid_cost
            self.world.ledger.raid_expenses += self.settings.ship_raid_cost
            chance = 5_500 + (attacker.ship.condition - target.ship.condition) * 30
            if "fine_sails" in attacker.ship.upgrades:
                chance += 500
            if "crows_nest" in attacker.ship.upgrades:
                chance += 500
            success = self.random_service.stream("player_raids").randint(1, 10_000) <= max(
                1_500, min(8_500, chance)
            )
            pending = None
            if success:
                pending = self._create_pending_raid_loot(
                    raid_type="ship",
                    attacker=attacker,
                    target=target,
                    preparation_cost=self.settings.ship_raid_cost,
                    base_damage=2,
                    notoriety_gain=6,
                    heat_gain=8,
                )
            else:
                attacker.ship.condition = max(0, attacker.ship.condition - 8)
            route = self.world.routes[attacker.voyage.route_id]
            self._add_piracy_pressure(
                (route.port_a, route.port_b),
                6 if success else 10,
                8 if success else 12,
            )
            self._emit(
                "PlayerRaidResolved",
                {
                    "raid_type": "ship",
                    "attacker_id": attacker.id,
                    "target_id": target.id,
                    "success": success,
                    "raid_id": pending.id if pending else None,
                    "maximum_haul": pending.maximum_haul if pending else 0,
                    "cost": self.settings.ship_raid_cost,
                },
            )
            return True
        if isinstance(command, RaidPort):
            attacker = self.world.merchants.get(command.attacker_id)
            port = self.world.ports.get(command.port_id)
            cooldown_key = f"{command.attacker_id}:{command.port_id}"
            if (
                attacker is None
                or port is None
                or attacker.controller != "player"
                or attacker.port_id != port.id
                or attacker.voyage is not None
            ):
                self._reject(command, "the ship must be docked at the target port")
                return True
            if any(
                not item.resolved and item.attacker_id == attacker.id
                for item in self.world.pending_raid_loot.values()
            ):
                self._reject(command, "resolve the previous raid loot before raiding again")
                return True
            if self.world.raid_cooldowns.get(cooldown_key, 0) > self.world.tick:
                self._reject(command, "this port is still alert after the previous raid")
                return True
            if attacker.cash < self.settings.port_raid_cost or attacker.ship.condition <= 30:
                self._reject(command, "port raids require $25 and ship condition above 30")
                return True
            attacker.cash -= self.settings.port_raid_cost
            self.world.ledger.raid_expenses += self.settings.port_raid_cost
            hostility_key = f"port_hostility:{port.id}"
            hostility = self.world.story_modifiers.get(hostility_key, 0)
            chance = 4_000 + attacker.ship.condition * 20 - hostility * 500
            if "reinforced_hull" in attacker.ship.upgrades:
                chance += 500
            success = self.random_service.stream("player_raids").randint(1, 10_000) <= max(
                1_000, min(8_000, chance)
            )
            pending = None
            if success:
                pending = self._create_pending_raid_loot(
                    raid_type="port",
                    attacker=attacker,
                    port_id=port.id,
                    preparation_cost=self.settings.port_raid_cost,
                    base_damage=3,
                    notoriety_gain=12,
                    heat_gain=25,
                )
            else:
                attacker.ship.condition = max(0, attacker.ship.condition - 11)
            self.world.raid_cooldowns[cooldown_key] = (
                self.world.tick + self.settings.port_raid_cooldown_ticks
            )
            self.world.story_modifiers[hostility_key] = hostility + 1
            day = self.world.tick // self.settings.ticks_per_day
            self.world.trade_access[f"{day}:{attacker.id}:{port.id}"] = False
            self._add_piracy_pressure(
                (port.id,),
                12 if success else 18,
                25 if success else 35,
            )
            self._emit(
                "PlayerRaidResolved",
                {
                    "raid_type": "port",
                    "attacker_id": attacker.id,
                    "port_id": port.id,
                    "success": success,
                    "raid_id": pending.id if pending else None,
                    "maximum_haul": pending.maximum_haul if pending else 0,
                    "cost": self.settings.port_raid_cost,
                },
            )
            return True
        return False

    def _apply_command(self, command: Command, dirty: DirtyPrices) -> None:
        if isinstance(command, AcknowledgeWeeklyOutlook):
            outlook = self.world.weekly_outlook
            if outlook is None or outlook.week_index != command.week_index:
                self._reject(command, "weekly outlook is no longer current")
                return
            outlook.reveal_pending = False
            self._emit("WeeklyOutlookAcknowledged", {"week_index": outlook.week_index})
            return
        if self._apply_raid_loot_command(command, dirty):
            return
        if self._apply_raid_command(command, dirty):
            return
        if self._apply_service_command(command, dirty):
            return
        if isinstance(command, ResolveEncounter):
            self._apply_encounter_command(command)
            return
        if isinstance(command, RespondToOpportunity):
            self._apply_opportunity_command(command, dirty)
            return
        if isinstance(command, BuyCargo):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            if (
                merchant is None
                or port is None
                or command.commodity_id not in self.world.commodities
            ):
                self._reject(command, "unknown merchant, port, or commodity")
                return
            inventory = port.inventories[command.commodity_id]
            if command.quantity <= 0:
                self._reject(command, "quantity must be positive")
                return
            if merchant.voyage is not None or merchant.port_id != port.id:
                self._reject(command, "merchant is not available at the requested port")
                return
            restriction = self._lawful_service_reason(port.id, "trade")
            if merchant.controller == "player" and restriction:
                self._reject(command, restriction)
                return
            if inventory.quantity < command.quantity:
                self._reject(command, "port has insufficient inventory")
                return
            if merchant.cargo_quantity + command.quantity > merchant.capacity:
                self._reject(command, "merchant cargo capacity would be exceeded")
                return
            if merchant.cash < inventory.ask_price * command.quantity:
                self._reject(command, "merchant has insufficient cash")
                return
            if not self._trade_access_allowed(command.merchant_id, command.port_id):
                self._emit(
                    "TradeDisrupted",
                    {
                        "merchant_id": command.merchant_id,
                        "port_id": command.port_id,
                        "setting_id": current_outlook_rule(self.world).setting_id,
                        "cause": self._trade_disruption_cause(command.merchant_id, command.port_id),
                    },
                )
                return
            unit_price, fortune_key = self._favorable_trade_price(
                command.merchant_id, inventory.ask_price, buying=True
            )
            reason = buy_cargo(
                self.world,
                command.merchant_id,
                command.port_id,
                command.commodity_id,
                command.quantity,
                dirty,
                unit_price,
            )
            if reason:
                self._reject(command, reason)
                return
            if fortune_key:
                self.world.fortune_consumed.append(fortune_key)
            self._emit(
                "CargoPurchased",
                {
                    "merchant_id": command.merchant_id,
                    "port_id": command.port_id,
                    "commodity_id": command.commodity_id,
                    "quantity": command.quantity,
                    "unit_price": unit_price,
                },
            )
            return

        if isinstance(command, SellCargo):
            merchant = self.world.merchants.get(command.merchant_id)
            port = self.world.ports.get(command.port_id)
            if (
                merchant is None
                or port is None
                or command.commodity_id not in self.world.commodities
            ):
                self._reject(command, "unknown merchant, port, or commodity")
                return
            inventory = port.inventories[command.commodity_id]
            if command.quantity <= 0:
                self._reject(command, "quantity must be positive")
                return
            if merchant.voyage is not None or merchant.port_id != port.id:
                self._reject(command, "merchant is not available at the requested port")
                return
            restriction = self._lawful_service_reason(port.id, "trade")
            if merchant.controller == "player" and restriction:
                self._reject(command, restriction)
                return
            if merchant.cargo.get(command.commodity_id, 0) < command.quantity:
                self._reject(command, "merchant has insufficient cargo")
                return
            clean_quantity = merchant.cargo.get(
                command.commodity_id, 0
            ) - merchant.stolen_cargo.get(command.commodity_id, 0)
            if command.quantity > clean_quantity:
                self._reject(
                    command,
                    "lawful buyers refuse stolen cargo; fence it at Blackwater Cay",
                )
                return
            if inventory.quantity + command.quantity > inventory.capacity:
                self._reject(command, "port storage capacity would be exceeded")
                return
            if port.treasury < inventory.bid_price * command.quantity:
                self._reject(command, "port treasury has insufficient cash")
                return
            unit_price = inventory.bid_price
            if not self._trade_access_allowed(command.merchant_id, command.port_id):
                self._emit(
                    "TradeDisrupted",
                    {
                        "merchant_id": command.merchant_id,
                        "port_id": command.port_id,
                        "setting_id": current_outlook_rule(self.world).setting_id,
                        "cause": self._trade_disruption_cause(command.merchant_id, command.port_id),
                    },
                )
                return
            unit_price, fortune_key = self._favorable_trade_price(
                command.merchant_id, unit_price, buying=False
            )
            reason, profit = sell_cargo(
                self.world,
                command.merchant_id,
                command.port_id,
                command.commodity_id,
                command.quantity,
                dirty,
                unit_price,
            )
            if reason:
                self._reject(command, reason)
                return
            if fortune_key:
                self.world.fortune_consumed.append(fortune_key)
            self._emit(
                "CargoSold",
                {
                    "merchant_id": command.merchant_id,
                    "port_id": command.port_id,
                    "commodity_id": command.commodity_id,
                    "quantity": command.quantity,
                    "unit_price": unit_price,
                    "profit": profit,
                },
            )
            return

        if isinstance(command, BeginVoyage):
            merchant = self.world.merchants.get(command.merchant_id)
            route = self.world.routes.get(command.route_id)
            if merchant is None or route is None:
                self._reject(command, "unknown merchant or route")
                return
            if merchant.voyage is not None or merchant.port_id is None:
                self._reject(command, "merchant is already travelling")
                return
            if merchant.port_id not in (route.port_a, route.port_b):
                self._reject(command, "merchant is not at a route endpoint")
                return
            if route.other_end(merchant.port_id) != command.destination_id:
                self._reject(command, "destination does not match the route")
                return
            if merchant.cash < route.operating_cost:
                self._reject(command, "merchant cannot afford the route cost")
                return
            if merchant.ship.condition <= 0:
                self._reject(command, "ship must be repaired before sailing")
                return
            if merchant.controller == "ai" and not merchant.cargo:
                self._reject(command, "AI merchants do not make empty voyages")
                return
            origin_id = merchant.port_id
            merchant.cash -= route.operating_cost
            merchant.realized_profit -= route.operating_cost
            self.world.ledger.operating_costs += route.operating_cost
            merchant.port_id = None
            merchant.voyage = Voyage(
                route_id=route.id,
                destination_id=command.destination_id,
                remaining_ticks=route.travel_ticks,
            )
            self._emit(
                "ShipDeparted",
                {
                    "merchant_id": merchant.id,
                    "origin_id": origin_id,
                    "destination_id": command.destination_id,
                    "route_id": route.id,
                    "travel_ticks": route.travel_ticks,
                    "operating_cost": route.operating_cost,
                },
            )

    def _advance_voyages(self) -> None:
        for merchant_id in sorted(self.world.merchants):
            merchant = self.world.merchants[merchant_id]
            if merchant.voyage is None:
                continue
            voyage = merchant.voyage
            voyage.hours_at_sea += 1
            if voyage.hours_at_sea % self.settings.ticks_per_day == 0:
                if merchant.ship.provisions > 0:
                    merchant.ship.provisions -= 1
                    self.world.ledger.provisions_consumed += 1
                condition_loss = 1 + (2 if merchant.ship.provisions == 0 else 0)
                if "reinforced_hull" in merchant.ship.upgrades:
                    condition_loss = max(1, condition_loss * 7 // 10)
                merchant.ship.condition = max(0, merchant.ship.condition - condition_loss)
            speed_bp = route_speed_multiplier(self.world, voyage.route_id)
            if "fine_sails" in merchant.ship.upgrades:
                speed_bp = speed_bp * 12_500 // 10_000
            if merchant.ship.provisions == 0:
                speed_bp = speed_bp * 7_500 // 10_000
            if merchant.ship.condition == 0:
                speed_bp = speed_bp * 5_000 // 10_000
            voyage.progress_remainder_bp += speed_bp
            while voyage.progress_remainder_bp >= 10_000 and voyage.remaining_ticks > 0:
                voyage.progress_remainder_bp -= 10_000
                voyage.remaining_ticks -= 1

            if not any(
                not encounter.resolved and encounter.merchant_id == merchant.id
                for encounter in self.world.pending_encounters.values()
            ):
                route = self.world.routes[voyage.route_id]
                if not voyage.navy_checked:
                    voyage.navy_checked = True
                    endpoint_heat = (
                        self.world.piracy_state.local_heat.get(route.port_a, 0)
                        + self.world.piracy_state.local_heat.get(route.port_b, 0)
                    ) // 2
                    stolen_value = sum(
                        quantity * self.world.commodities[commodity_id].base_price
                        for commodity_id, quantity in merchant.stolen_cargo.items()
                    )
                    navy_chance = route.navy_patrol_bp
                    navy_chance += endpoint_heat * 35
                    navy_chance += self.world.piracy_state.notoriety * 25
                    navy_chance += min(2_000, stolen_value // 10)
                    navy_chance = (
                        navy_chance
                        * max(
                            self.world.ports[route.port_a].enforcement_bp,
                            self.world.ports[route.port_b].enforcement_bp,
                        )
                        // 10_000
                    )
                    navy_chance = min(self.settings.navy_encounter_cap_bp, navy_chance)
                    if (
                        self.random_service.stream("navy_encounters").randint(1, 10_000)
                        <= navy_chance
                    ):
                        encounter_id = f"navy_{self.world.next_event_sequence}_{merchant.id}"
                        stolen_reference = sum(
                            quantity * self.world.commodities[item].base_price
                            for item, quantity in merchant.stolen_cargo.items()
                        )
                        encounter = PendingEncounter(
                            id=encounter_id,
                            merchant_id=merchant.id,
                            route_id=route.id,
                            severity_bp=max(2_000, navy_chance),
                            demand=1_000
                            + self.world.piracy_state.notoriety * 100
                            + stolen_reference * 20 // 100,
                            encounter_type="navy",
                            port_id=max(
                                (route.port_a, route.port_b),
                                key=lambda item: self.world.piracy_state.local_heat.get(item, 0),
                            ),
                        )
                        self.world.pending_encounters[encounter_id] = encounter
                        self._emit(
                            "NavyEncounterStarted",
                            {
                                "encounter_id": encounter.id,
                                "merchant_id": merchant.id,
                                "route_id": route.id,
                                "fine": encounter.demand,
                            },
                        )
                        if merchant.controller == "ai":
                            choice = "pay" if merchant.cash >= encounter.demand else "surrender"
                            self.world.pending_commands.append(
                                ResolveEncounter(merchant.id, encounter.id, choice)
                            )
                        continue
                risk_bp = route.risk_bp * route_risk_multiplier(self.world, route.id) // 10_000
                if "crows_nest" in merchant.ship.upgrades:
                    risk_bp = risk_bp * 9_000 // 10_000
                if merchant.controller == "player" and self.world.story_modifiers.get(
                    "retain_informant"
                ):
                    risk_bp = risk_bp * 8_500 // 10_000
                hourly_risk = max(1, risk_bp // self.settings.ticks_per_day)
                if self.random_service.stream("encounters").randint(1, 10_000) <= hourly_risk:
                    encounter_id = f"encounter_{self.world.next_event_sequence}_{merchant.id}"
                    encounter = PendingEncounter(
                        id=encounter_id,
                        merchant_id=merchant.id,
                        route_id=route.id,
                        severity_bp=self.random_service.stream("encounters").randint(2_000, 6_000),
                        demand=max(500, merchant.cash // 20),
                    )
                    self.world.pending_encounters[encounter_id] = encounter
                    self._emit(
                        "PirateEncounterStarted",
                        {
                            "encounter_id": encounter_id,
                            "merchant_id": merchant.id,
                            "route_id": route.id,
                            "severity_bp": encounter.severity_bp,
                        },
                    )
                    if merchant.controller == "ai":
                        choice = "pay" if merchant.cash >= encounter.demand else "surrender"
                        self.world.pending_commands.append(
                            ResolveEncounter(merchant.id, encounter.id, choice)
                        )

            if voyage.remaining_ticks == 0:
                destination_id = merchant.voyage.destination_id
                route_id = merchant.voyage.route_id
                merchant.port_id = destination_id
                merchant.voyage = None
                merchant.completed_voyages += 1
                self._progress_arrival_objectives(merchant, destination_id, route_id)
                if merchant.controller == "player":
                    learned_reports = merge_player_knowledge(
                        self.world,
                        destination_id,
                    )
                    for report in learned_reports:
                        self._emit(
                            "InformationReportReceived",
                            {
                                "destination_port_id": destination_id,
                                "report_id": report.id,
                                "headline": report.headline,
                                "information_kind": report.information_kind,
                                "event_id": report.event_id,
                                "urgent": report.priority == "urgent",
                                "created_tick": report.created_tick,
                                "port_id": report.port_id,
                                "route_id": report.route_id,
                                "commodity_id": report.commodity_id,
                            },
                        )
                if merchant.controller == "player":
                    maybe_offer_intro_story(
                        self.world,
                        destination_id,
                        self.settings,
                        self._emit,
                    )
                self._emit(
                    "ShipArrived",
                    {
                        "merchant_id": merchant.id,
                        "destination_id": destination_id,
                        "route_id": route_id,
                    },
                )

    def _progress_arrival_objectives(
        self, merchant: Merchant, destination_id: str, route_id: str
    ) -> None:
        for opportunity in self.world.opportunities.values():
            if (
                opportunity.status != "accepted"
                or opportunity.accepted_by != merchant.id
                or opportunity.objective_type
                not in {"reconnaissance", "evacuation", "protection", "smuggling"}
                or opportunity.destination_port_id != destination_id
                or (opportunity.route_id and opportunity.route_id != route_id)
            ):
                continue
            opportunity.progress = 1
            opportunity.status = "resolved"
            opportunity.chosen_option = "completed"
            opportunity.resolved_tick = self.world.tick
            merchant.mission_load.pop(opportunity.id, None)
            merchant.cash += opportunity.reward
            self.world.ledger.initial_cash += opportunity.reward
            self._emit(
                "OpportunityCompleted",
                {
                    "opportunity_id": opportunity.id,
                    "merchant_id": merchant.id,
                    "destination_id": destination_id,
                    "reward": opportunity.reward,
                    "progress": opportunity.progress,
                },
            )

    def _queue_merchant_actions(self) -> None:
        for merchant_id in sorted(self.world.merchants):
            merchant = self.world.merchants[merchant_id]
            if merchant.controller != "ai":
                continue
            if merchant.voyage is not None or merchant.port_id is None:
                continue
            if merchant.ship.wage_debt and merchant.cash:
                self.world.pending_commands.append(PayWages(merchant.id, merchant.port_id))
                continue
            if (
                merchant.ship.condition < 80
                and merchant.cash >= self.settings.repair_cost_per_point
            ):
                points = min(20, self.settings.condition_maximum - merchant.ship.condition)
                self.world.pending_commands.append(
                    RepairShip(merchant.id, merchant.port_id, points)
                )
                continue
            if (
                merchant.ship.provisions < 2
                and merchant.cargo_quantity < merchant.capacity
                and self.world.ports[merchant.port_id].inventories["grain"].quantity
            ):
                self.world.pending_commands.append(BuyProvisions(merchant.id, merchant.port_id, 1))
                continue
            if (
                "fine_sails" not in merchant.ship.upgrades
                and not merchant.ship.wage_debt
                and merchant.cash
                > self.settings.upgrade_fine_sails_cost + self.settings.merchant_starting_cash // 2
            ):
                self.world.pending_commands.append(
                    PurchaseUpgrade(merchant.id, merchant.port_id, "fine_sails")
                )
                continue
            if merchant.cargo:
                for commodity_id in sorted(merchant.cargo):
                    port = self.world.ports[merchant.port_id]
                    inventory = port.inventories[commodity_id]
                    quantity = min(
                        merchant.cargo[commodity_id],
                        inventory.capacity - inventory.quantity,
                        port.treasury // inventory.bid_price if inventory.bid_price else 0,
                    )
                    if quantity <= 0:
                        continue
                    self.world.pending_commands.append(
                        SellCargo(merchant.id, merchant.port_id, commodity_id, quantity)
                    )
                    self._emit(
                        "MerchantDecision",
                        {
                            "merchant_id": merchant.id,
                            "action": "sell",
                            "port_id": merchant.port_id,
                            "commodity_id": commodity_id,
                            "quantity": quantity,
                        },
                    )
                continue

            day = self.world.tick // self.settings.ticks_per_day
            access_key = f"{day}:{merchant.id}:{merchant.port_id}"
            if self.world.trade_access.get(access_key) is False:
                continue
            opportunity = best_merchant_opportunity(self.world, merchant, self.settings)
            if opportunity is None:
                continue
            self.world.pending_commands.extend(
                [
                    BuyCargo(
                        merchant_id=merchant.id,
                        port_id=opportunity.origin_id,
                        commodity_id=opportunity.commodity_id,
                        quantity=opportunity.quantity,
                    ),
                    BeginVoyage(
                        merchant_id=merchant.id,
                        route_id=opportunity.route_id,
                        destination_id=opportunity.destination_id,
                    ),
                ]
            )
            self._emit(
                "MerchantDecision",
                {
                    "merchant_id": merchant.id,
                    "action": "trade",
                    "origin_id": opportunity.origin_id,
                    "destination_id": opportunity.destination_id,
                    "route_id": opportunity.route_id,
                    "commodity_id": opportunity.commodity_id,
                    "quantity": opportunity.quantity,
                    "expected_profit": opportunity.expected_profit,
                    "score": opportunity.score,
                },
            )

    def _roll_weekly_outlook(self, dirty: DirtyPrices) -> bool:
        if self.world.tick == 0 or self.world.tick % self.settings.week_ticks:
            return False
        if (
            self.world.weekly_outlook is not None
            and self.world.weekly_outlook.start_tick == self.world.tick
        ):
            return False
        self.world.weekly_outlook = roll_weekly_outlook(
            self.world.tick,
            self.settings,
            self.random_service.stream("weekly_outlook"),
        )
        for port_id, port in self.world.ports.items():
            for commodity_id in port.inventories:
                dirty.setdefault((port_id, commodity_id), set()).add("weekly_outlook")
        rule = current_outlook_rule(self.world)
        if rule.total == 12:
            port_id = self.random_service.stream("opportunities").choice(sorted(self.world.ports))
            opportunity_id = f"fortune_{self.world.weekly_outlook.week_index}"
            self.world.opportunities[opportunity_id] = Opportunity(
                id=opportunity_id,
                kind="fortune",
                title="Fortune's Crown",
                description=(f"A rare sealed offer awaits at {self.world.ports[port_id].name}."),
                port_id=port_id,
                created_tick=self.world.tick,
                expiry_tick=self.world.weekly_outlook.end_tick,
                reward=5_000,
                options=["accept", "decline"],
            )
            self._emit(
                "OpportunityCreated",
                {"opportunity_id": opportunity_id, "port_id": port_id},
            )
        self._emit(
            "WeeklyOutlookRolled",
            {
                "week_index": self.world.weekly_outlook.week_index,
                "die_one": self.world.weekly_outlook.die_one,
                "die_two": self.world.weekly_outlook.die_two,
                "total": rule.total,
                "setting_id": rule.setting_id,
                "name": rule.name,
            },
        )
        return True

    def _apply_ship_payroll(self) -> None:
        for merchant in self.world.merchants.values():
            if self.world.tick + 1 < merchant.ship.next_wage_tick:
                continue
            merchant.ship.next_wage_tick += self.settings.week_ticks
            if merchant.cash >= self.settings.weekly_wage:
                merchant.cash -= self.settings.weekly_wage
                self.world.ledger.wages_paid += self.settings.weekly_wage
                self._emit(
                    "WagesPaid",
                    {"merchant_id": merchant.id, "amount": self.settings.weekly_wage},
                )
            else:
                merchant.ship.wage_debt += self.settings.weekly_wage
                self._emit(
                    "WagesAccrued",
                    {"merchant_id": merchant.id, "amount": self.settings.weekly_wage},
                )

    def _apply_event_loss(
        self,
        condition: Any,
        dirty: DirtyPrices,
    ) -> None:
        if condition.port_id is None or condition.event_type not in {
            "warehouse_fire",
            "hurricane",
        }:
            return
        port = self.world.ports[condition.port_id]
        if (
            condition.event_type == "warehouse_fire"
            and condition.capacity_reduction_bp
            and not condition.capacity_reduced
        ):
            for inventory in port.inventories.values():
                inventory.capacity = max(
                    inventory.target,
                    inventory.capacity * (10_000 - condition.capacity_reduction_bp) // 10_000,
                )
            condition.capacity_reduced = True
        commodity_ids = (
            sorted(port.inventories)
            if condition.event_type == "warehouse_fire"
            else [condition.commodity_id]
            if condition.commodity_id is not None
            else sorted(port.inventories)
        )
        loss_bp = (
            min(3_000, max(1_000, condition.severity_bp // 2))
            if condition.event_type == "warehouse_fire"
            else min(1_500, max(500, condition.severity_bp // 4))
        )
        if (
            condition.event_type == "hurricane"
            and condition.port_id == "whitecap_reach"
            and self.world.story_modifiers.get("build_sea_wall")
        ):
            loss_bp = loss_bp * 3 // 4
        for commodity_id in commodity_ids:
            inventory = port.inventories[commodity_id]
            lost = inventory.quantity * loss_bp // 10_000
            if condition.event_type == "warehouse_fire":
                lost = max(lost, inventory.quantity - inventory.capacity)
            if lost <= 0:
                continue
            inventory.quantity -= lost
            self.world.ledger.event_losses[commodity_id] += lost
            dirty.setdefault((port.id, commodity_id), set()).add(condition.event_type)
            self._emit(
                "InventoryLost",
                {
                    "event_id": condition.id,
                    "port_id": port.id,
                    "commodity_id": commodity_id,
                    "quantity": lost,
                },
            )

    def _restore_ending_capacity(self) -> None:
        for condition in self.world.active_conditions:
            if (
                condition.capacity_reduced
                and condition.port_id is not None
                and self.world.tick >= condition.end_tick
            ):
                for inventory in self.world.ports[condition.port_id].inventories.values():
                    inventory.capacity = max(
                        inventory.capacity,
                        inventory.target * self.settings.storage_capacity_multiplier,
                    )

    def _update_relief_calls(self) -> None:
        daily_events = [
            event for event in self._step_events if event.event_type == "DailyEconomyApplied"
        ]
        for event in daily_events:
            port_id = event.payload["port_id"]
            commodity_id = event.payload["commodity_id"]
            key = f"{port_id}:{commodity_id}"
            if event.payload["unmet_demand"] > 0:
                self.world.unmet_demand_streaks[key] = (
                    self.world.unmet_demand_streaks.get(key, 0) + 1
                )
            else:
                self.world.unmet_demand_streaks[key] = 0
            active_relief_calls = sum(
                condition.event_type == "relief_call" and self.world.tick < condition.end_tick
                for condition in self.world.active_conditions
            )
            port_cooldown_key = f"relief_call:{key}"
            if (
                self.world.unmet_demand_streaks[key] < self.settings.relief_call_streak_days
                or commodity_id not in {"grain", "medicine"}
                or active_relief_calls >= self.settings.maximum_active_relief_calls
                or self.world.tick < self.world.event_cooldowns.get("relief_call:global", 0)
                or self.world.tick < self.world.event_cooldowns.get(port_cooldown_key, 0)
                or any(
                    condition.event_type == "relief_call"
                    and condition.port_id == port_id
                    and condition.commodity_id == commodity_id
                    for condition in self.world.active_conditions
                )
            ):
                continue
            event_id = f"event_{self.world.next_event_sequence}_relief_call"
            condition = ActiveCondition(
                id=event_id,
                event_type="relief_call",
                stage="active",
                start_tick=self.world.tick,
                transition_tick=self.world.tick,
                recovery_tick=self.world.tick + 7 * self.settings.ticks_per_day,
                end_tick=self.world.tick + 8 * self.settings.ticks_per_day,
                severity_bp=2_500,
                port_id=port_id,
                commodity_id=commodity_id,
            )
            self.world.active_conditions.append(condition)
            self.world.unmet_demand_streaks[key] = 0
            self.world.event_cooldowns["relief_call:global"] = (
                self.world.tick + self.settings.relief_call_global_cooldown_ticks
            )
            self.world.event_cooldowns[port_cooldown_key] = (
                self.world.tick + self.settings.relief_call_port_cooldown_ticks
            )
            self._emit(
                "WorldEventStarted",
                {
                    "event_id": event_id,
                    "event_type": "relief_call",
                    "stage": "active",
                    "port_id": port_id,
                    "commodity_id": commodity_id,
                },
            )
            opportunity = create_event_opportunity(
                self.world,
                event_id,
                "relief_call",
                port_id,
                commodity_id,
                self.settings,
                self._emit,
            )
            if opportunity is not None:
                report = publish_event_report(
                    self.world,
                    port_id,
                    "Relief Call",
                    f"{self.world.ports[port_id].name} requests urgent "
                    f"{self.world.commodities[commodity_id].name}.",
                    "FACT",
                    event_id,
                    commodity_id=commodity_id,
                    public_effect_summary="Sustained unmet demand created a delivery contract.",
                    deadline_tick=opportunity.expiry_tick,
                    priority="urgent",
                    opportunity_ids=(opportunity.id,),
                )
                opportunity.related_report_id = report.id
                if self.world.merchants[self.world.player_id].port_id == port_id:
                    merge_player_knowledge(self.world, port_id)

    def _generate_world_event(self, dirty: DirtyPrices) -> None:
        condition = maybe_generate_event(
            self.world,
            self.settings,
            self.random_service.stream("world_events"),
            self._emit,
        )
        if condition is None:
            return
        if condition.stage == "active":
            self._apply_event_loss(condition, dirty)
        source_port = condition.port_id
        if source_port is None and condition.route_id is not None:
            source_port = self.world.routes[condition.route_id].port_a
        if source_port is None:
            source_port = sorted(self.world.ports)[0]
        headline = condition.event_type.replace("_", " ").title()
        opportunity = (
            create_event_opportunity(
                self.world,
                condition.id,
                condition.event_type,
                condition.port_id,
                condition.commodity_id,
                self.settings,
                self._emit,
            )
            if condition.port_id is not None
            else None
        )
        urgent_types = {
            "tropical_storm",
            "hurricane",
            "crop_blight",
            "warehouse_fire",
            "shipyard_commission",
            "relief_call",
        }
        report = publish_event_report(
            self.world,
            source_port,
            headline,
            f"{headline} reported near {self.world.ports[source_port].name}.",
            "FACT" if condition.stage == "active" else "ESTIMATE",
            condition.id,
            condition.route_id,
            condition.commodity_id,
            public_effect_summary=(
                f"{headline} changes production, demand, storage, travel, or risk "
                "through its active condition."
            ),
            deadline_tick=opportunity.expiry_tick if opportunity else condition.end_tick,
            priority="urgent" if condition.event_type in urgent_types else "normal",
            opportunity_ids=(opportunity.id,) if opportunity else (),
        )
        if opportunity is not None:
            opportunity.related_report_id = report.id
        player = self.world.merchants[self.world.player_id]
        if player.port_id == source_port:
            merge_player_knowledge(self.world, source_port)
            self._emit(
                "InformationReportReceived",
                {
                    "destination_port_id": source_port,
                    "report_id": report.id,
                    "headline": report.headline,
                    "information_kind": report.information_kind,
                    "event_id": report.event_id,
                    "urgent": report.priority == "urgent",
                },
            )

    def _generate_rumours(self) -> None:
        rule = current_outlook_rule(self.world)
        count = 2 if rule.total == 10 else 1
        stream = self.random_service.stream("rumours")
        for _ in range(count):
            if stream.randint(1, 10_000) > 2_500:
                continue
            source_id = stream.choice(sorted(self.world.ports))
            commodity_id = stream.choice(sorted(self.world.commodities))
            port_id = stream.choice(sorted(self.world.ports))
            is_true = stream.randint(1, 10_000) > rule.false_rumour_bp
            direction = stream.choice(("shortage", "surplus", "shipment"))
            publish_event_report(
                self.world,
                source_id,
                f"Rumour: {commodity_id.title()} {direction} at {self.world.ports[port_id].name}",
                "Dockside talk has not been independently confirmed.",
                "RUMOUR",
                commodity_id=commodity_id,
                is_true=is_true,
                target_port_id=port_id,
            )
            self._emit(
                "RumourPublished",
                {
                    "source_port_id": source_id,
                    "port_id": port_id,
                    "commodity_id": commodity_id,
                },
            )

    def _expire_opportunities(self) -> None:
        for opportunity in self.world.opportunities.values():
            if (
                opportunity.status in {"offered", "accepted"}
                and self.world.tick >= opportunity.expiry_tick
            ):
                was_accepted = opportunity.status == "accepted"
                opportunity.status = "expired"
                if opportunity.accepted_by in self.world.merchants:
                    merchant = self.world.merchants[opportunity.accepted_by]
                    merchant.mission_load.pop(opportunity.id, None)
                    if was_accepted:
                        penalty_assessed, penalty_paid = self._apply_mission_failure_penalty(
                            opportunity,
                            merchant,
                        )
                        opportunity.resolved_tick = self.world.tick
                        opportunity.failure_reason = "expired"
                        opportunity.penalty_assessed = penalty_assessed
                        opportunity.penalty_paid = penalty_paid
                        self._emit(
                            "OpportunityFailed",
                            {
                                "opportunity_id": opportunity.id,
                                "merchant_id": merchant.id,
                                "reason": "expired",
                                "penalty_assessed": penalty_assessed,
                                "penalty_paid": penalty_paid,
                            },
                        )
                self._emit(
                    "OpportunityExpired",
                    {
                        "opportunity_id": opportunity.id,
                        "was_accepted": was_accepted,
                    },
                )

    def _decay_piracy_pressure(self) -> None:
        state = self.world.piracy_state
        if self.world.tick + 1 < state.next_decay_tick:
            return
        for port_id in self.world.ports:
            if (
                self.world.tick + 1 - state.last_raid_ticks.get(port_id, -100_000)
                >= self.settings.heat_decay_delay_ticks
            ):
                state.local_heat[port_id] = max(
                    0,
                    state.local_heat.get(port_id, 0) - self.settings.heat_decay_per_day,
                )
        if (
            (self.world.tick + 1) % self.settings.week_ticks == 0
            and self.world.tick + 1 - state.last_global_raid_tick >= self.settings.week_ticks
        ):
            state.notoriety = max(0, state.notoriety - self.settings.notoriety_decay_per_week)
        state.next_decay_tick = self.world.tick + 1 + self.settings.ticks_per_day

    def _record_market_history(self) -> None:
        for port_id, port in self.world.ports.items():
            port_history = self.world.price_history.setdefault(port_id, {})
            for commodity_id, inventory in port.inventories.items():
                history = port_history.setdefault(commodity_id, [])
                history.append(
                    MarketSample(
                        tick=self.world.tick + 1,
                        quantity=inventory.quantity,
                        bid_price=inventory.bid_price,
                        ask_price=inventory.ask_price,
                    )
                )
                del history[: -self.settings.market_history_limit]

    def assert_invariants(self) -> None:
        errors: list[str] = []
        current_goods = {commodity_id: 0 for commodity_id in self.world.commodities}
        for port in self.world.ports.values():
            if port.treasury < 0:
                errors.append(f"port {port.id} has negative treasury")
            for commodity_id, inventory in port.inventories.items():
                current_goods[commodity_id] += inventory.quantity
                if not 0 <= inventory.quantity <= inventory.capacity:
                    errors.append(f"port {port.id} has invalid {commodity_id} inventory")
                if inventory.bid_price <= 0 or inventory.ask_price < inventory.bid_price:
                    errors.append(f"port {port.id} has invalid {commodity_id} quote")
        current_cash = sum(port.treasury for port in self.world.ports.values())
        for merchant in self.world.merchants.values():
            current_cash += merchant.cash
            if merchant.cash < 0 or merchant.cargo_quantity > merchant.capacity:
                errors.append(f"merchant {merchant.id} has invalid cash or cargo")
            if (
                merchant.ship.provisions < 0
                or merchant.ship.wage_debt < 0
                or not 0 <= merchant.ship.condition <= self.settings.condition_maximum
            ):
                errors.append(f"merchant {merchant.id} has invalid ship state")
            if (merchant.port_id is None) == (merchant.voyage is None):
                errors.append(f"merchant {merchant.id} has invalid location state")
            if merchant.voyage is not None and merchant.voyage.remaining_ticks <= 0:
                errors.append(f"merchant {merchant.id} has invalid travel duration")
            current_goods["grain"] += merchant.ship.provisions
            for commodity_id, quantity in merchant.cargo.items():
                current_goods[commodity_id] += quantity
                if quantity <= 0:
                    errors.append(f"merchant {merchant.id} has invalid cargo")
                if merchant.stolen_cargo.get(commodity_id, 0) > quantity:
                    errors.append(f"merchant {merchant.id} has invalid stolen cargo provenance")
                clean = quantity - merchant.stolen_cargo.get(commodity_id, 0)
                basis = merchant.cargo_cost_basis.get(commodity_id, 0)
                if basis < 0 or (clean == 0 and basis != 0):
                    errors.append(f"merchant {merchant.id} has invalid cargo cost basis")

        for commodity_id, quantity in current_goods.items():
            ledger_quantity = self.world.ledger.initial_goods[commodity_id]
            ledger_quantity += self.world.ledger.produced[commodity_id]
            ledger_quantity -= self.world.ledger.consumed[commodity_id]
            ledger_quantity -= self.world.ledger.overflow[commodity_id]
            ledger_quantity -= self.world.ledger.event_losses[commodity_id]
            ledger_quantity -= self.world.ledger.piracy_losses[commodity_id]
            ledger_quantity -= self.world.ledger.confiscated_goods[commodity_id]
            ledger_quantity += self.world.ledger.salvage_recovered[commodity_id]
            if commodity_id == "grain":
                ledger_quantity -= self.world.ledger.provisions_consumed
            if quantity != ledger_quantity:
                errors.append(
                    f"{commodity_id} conservation failed: "
                    f"state={quantity}, ledger={ledger_quantity}"
                )
        expected_cash = self.world.ledger.initial_cash
        expected_cash += self.world.ledger.local_consumption_revenue
        expected_cash -= self.world.ledger.operating_costs
        expected_cash -= self.world.ledger.wages_paid
        expected_cash -= self.world.ledger.raid_expenses
        expected_cash -= self.world.ledger.fines_paid
        expected_cash -= self.world.ledger.mission_penalties
        if current_cash != expected_cash:
            errors.append(f"cash conservation failed: state={current_cash}, ledger={expected_cash}")
        if errors:
            raise InvariantError("; ".join(errors))

    def _complete_event_batch(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._step_events)
        if self.event_sink:
            for event in events:
                self.event_sink(event)
        return events

    def execute_commands(self, commands: list[Command]) -> tuple[DomainEvent, ...]:
        """Execute player commands without advancing world time."""
        self._step_events = []
        dirty: DirtyPrices = {}
        for command in commands:
            self._apply_command(command, dirty)
        recalculate_prices(self.world, self.settings, dirty, self._emit)
        self.world.random_states = self.random_service.snapshot()
        if self.settings.check_invariants:
            try:
                self.assert_invariants()
            except InvariantError as error:
                self.invariant_failures += 1
                self._emit("InvariantFailed", {"message": str(error)})
                raise
        return self._complete_event_batch()

    def step(self) -> tuple[DomainEvent, ...]:
        self._step_events = []
        dirty: DirtyPrices = {}

        if self._roll_weekly_outlook(dirty):
            recalculate_prices(self.world, self.settings, dirty, self._emit)
            self.world.random_states = self.random_service.snapshot()
            return self._complete_event_batch()
        commands = self.world.pending_commands
        self.world.pending_commands = []
        for command in commands:
            self._apply_command(command, dirty)

        precursor_ids = {
            condition.id
            for condition in self.world.active_conditions
            if condition.stage == "precursor"
        }
        self._restore_ending_capacity()
        advance_conditions(self.world, self._emit)
        for condition in self.world.active_conditions:
            if condition.id in precursor_ids and condition.stage == "active":
                self._apply_event_loss(condition, dirty)

        self._advance_voyages()
        daily_update = (self.world.tick + 1) % self.settings.ticks_per_day == 0
        if daily_update:
            self._apply_ship_payroll()
            self._decay_piracy_pressure()
            apply_daily_economy(self.world, dirty, self._emit)
            self._update_relief_calls()
            self._generate_world_event(dirty)
            self._generate_rumours()
        maybe_generate_situation(
            self.world,
            self.settings,
            self.random_service.stream("situations"),
            self._emit,
        )
        recalculate_prices(self.world, self.settings, dirty, self._emit)
        if daily_update:
            self._record_market_history()
            publish_market_reports(self.world)
        deliver_reports(self.world, self._emit)
        self._expire_opportunities()
        self._queue_merchant_actions()
        self.world.random_states = self.random_service.snapshot()

        if self.settings.check_invariants:
            try:
                self.assert_invariants()
            except InvariantError as error:
                self.invariant_failures += 1
                self._emit("InvariantFailed", {"message": str(error)})
                raise

        self.world.tick += 1
        return self._complete_event_batch()

    def run(self) -> SimulationReport:
        target_tick = self.world.tick + self.settings.simulation_ticks
        while self.world.tick < target_tick:
            self.step()
        return SimulationReport(
            seed=self.world.seed,
            completed_ticks=self.world.tick,
            state_hash=self.world.state_hash(),
            event_counts=dict(sorted(self.event_counts.items())),
            rejected_commands=self.rejected_commands,
            invariant_failures=self.invariant_failures,
        )
