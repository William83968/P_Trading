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
    BeginVoyage,
    BuyCargo,
    Command,
    DomainEvent,
    EconomicLedger,
    MarketSample,
    Merchant,
    SellCargo,
    SimulationReport,
    Voyage,
    WorldState,
)
from pirate_trading.settings import SETTINGS, SimulationSettings

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
    if settings.merchant_count != 3:
        raise ContentError("Milestone 0 requires exactly three merchants")
    if settings.ticks_per_day <= 0 or settings.simulation_ticks < 0:
        raise ContentError("Simulation tick settings must be positive")
    if settings.merchant_capacity <= 0 or settings.merchant_starting_cash < 0:
        raise ContentError("Merchant capacity and cash settings are invalid")

    loaded = load_content(settings)
    random_service = RandomService(settings.world_seed)
    preference_stream = random_service.stream("merchant_ai")
    merchants: dict[str, Merchant] = {}
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
    )

    initial_goods = {
        commodity_id: sum(port.inventories[commodity_id].quantity for port in loaded.ports.values())
        for commodity_id in loaded.commodities
    }
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
    )
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
    return world


class SimulationEngine:
    """Coordinate the small, fixed-order Milestone 0 simulation."""

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

    def _apply_command(self, command: Command, dirty: DirtyPrices) -> None:
        if isinstance(command, BuyCargo):
            reason = buy_cargo(
                self.world,
                command.merchant_id,
                command.port_id,
                command.commodity_id,
                command.quantity,
                dirty,
            )
            if reason:
                self._reject(command, reason)
                return
            inventory = self.world.ports[command.port_id].inventories[command.commodity_id]
            self._emit(
                "CargoPurchased",
                {
                    "merchant_id": command.merchant_id,
                    "port_id": command.port_id,
                    "commodity_id": command.commodity_id,
                    "quantity": command.quantity,
                    "unit_price": inventory.ask_price,
                },
            )
            return

        if isinstance(command, SellCargo):
            inventory = self.world.ports.get(command.port_id)
            unit_price = (
                inventory.inventories[command.commodity_id].bid_price
                if inventory and command.commodity_id in inventory.inventories
                else 0
            )
            reason, profit = sell_cargo(
                self.world,
                command.merchant_id,
                command.port_id,
                command.commodity_id,
                command.quantity,
                dirty,
            )
            if reason:
                self._reject(command, reason)
                return
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
            merchant.voyage.remaining_ticks -= 1
            if merchant.voyage.remaining_ticks == 0:
                destination_id = merchant.voyage.destination_id
                route_id = merchant.voyage.route_id
                merchant.port_id = destination_id
                merchant.voyage = None
                merchant.completed_voyages += 1
                self._emit(
                    "ShipArrived",
                    {
                        "merchant_id": merchant.id,
                        "destination_id": destination_id,
                        "route_id": route_id,
                    },
                )

    def _queue_merchant_actions(self) -> None:
        for merchant_id in sorted(self.world.merchants):
            merchant = self.world.merchants[merchant_id]
            if merchant.controller != "ai":
                continue
            if merchant.voyage is not None or merchant.port_id is None:
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
            if (merchant.port_id is None) == (merchant.voyage is None):
                errors.append(f"merchant {merchant.id} has invalid location state")
            if merchant.voyage is not None and merchant.voyage.remaining_ticks <= 0:
                errors.append(f"merchant {merchant.id} has invalid travel duration")
            for commodity_id, quantity in merchant.cargo.items():
                current_goods[commodity_id] += quantity
                if quantity <= 0:
                    errors.append(f"merchant {merchant.id} has invalid cargo")

        for commodity_id, quantity in current_goods.items():
            ledger_quantity = self.world.ledger.initial_goods[commodity_id]
            ledger_quantity += self.world.ledger.produced[commodity_id]
            ledger_quantity -= self.world.ledger.consumed[commodity_id]
            ledger_quantity -= self.world.ledger.overflow[commodity_id]
            if quantity != ledger_quantity:
                errors.append(
                    f"{commodity_id} conservation failed: "
                    f"state={quantity}, ledger={ledger_quantity}"
                )
        expected_cash = self.world.ledger.initial_cash - self.world.ledger.operating_costs
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

        commands = self.world.pending_commands
        self.world.pending_commands = []
        for command in commands:
            self._apply_command(command, dirty)

        self._advance_voyages()
        daily_update = (self.world.tick + 1) % self.settings.ticks_per_day == 0
        if daily_update:
            apply_daily_economy(self.world, dirty, self._emit)
        recalculate_prices(self.world, self.settings, dirty, self._emit)
        if daily_update:
            self._record_market_history()
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
        for _ in range(self.settings.simulation_ticks):
            self.step()
        return SimulationReport(
            seed=self.world.seed,
            completed_ticks=self.world.tick,
            state_hash=self.world.state_hash(),
            event_counts=dict(sorted(self.event_counts.items())),
            rejected_commands=self.rejected_commands,
            invariant_failures=self.invariant_failures,
        )
