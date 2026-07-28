"""Integer-only economy rules and merchant opportunity scoring."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pirate_trading.models import Merchant, WorldState
from pirate_trading.settings import SimulationSettings
from pirate_trading.world_events import (
    active_event_multiplier,
    current_outlook_rule,
)

EventEmitter = Callable[[str, dict[str, Any]], None]
DirtyPrices = dict[tuple[str, str], set[str]]


@dataclass(frozen=True, slots=True)
class MerchantOpportunity:
    merchant_id: str
    commodity_id: str
    origin_id: str
    destination_id: str
    route_id: str
    quantity: int
    expected_profit: int
    score: int


def calculate_quote(
    base_price: int,
    minimum_price: int,
    maximum_price: int,
    elasticity_bp: int,
    spread_bp: int,
    quantity: int,
    target: int,
    scale: int,
    spread_multiplier_bp: int = 10_000,
) -> tuple[int, int]:
    """Calculate the bid and ask using deterministic basis-point arithmetic."""
    pressure_bp = ((target - quantity) * scale) // target
    adjustment_bp = (elasticity_bp * pressure_bp) // scale
    reference = (base_price * (scale + adjustment_bp)) // scale
    reference = min(max(reference, minimum_price), maximum_price)
    effective_spread = spread_bp * spread_multiplier_bp // scale
    bid = (reference * (scale - effective_spread)) // scale
    ask = (reference * (scale + effective_spread) + scale - 1) // scale
    return bid, ask


def initialize_prices(world: WorldState, settings: SimulationSettings) -> None:
    spread_multiplier = current_outlook_rule(world).spread_bp
    for port in world.ports.values():
        for commodity_id, inventory in port.inventories.items():
            commodity = world.commodities[commodity_id]
            inventory.bid_price, inventory.ask_price = calculate_quote(
                commodity.base_price,
                commodity.minimum_price,
                commodity.maximum_price,
                commodity.elasticity_bp,
                commodity.spread_bp,
                inventory.quantity,
                inventory.target,
                settings.basis_point_scale,
                spread_multiplier,
            )


def recalculate_prices(
    world: WorldState,
    settings: SimulationSettings,
    dirty: DirtyPrices,
    emit: EventEmitter,
) -> None:
    spread_multiplier = current_outlook_rule(world).spread_bp
    for (port_id, commodity_id), causes in sorted(dirty.items()):
        inventory = world.ports[port_id].inventories[commodity_id]
        old_bid, old_ask = inventory.bid_price, inventory.ask_price
        commodity = world.commodities[commodity_id]
        inventory.bid_price, inventory.ask_price = calculate_quote(
            commodity.base_price,
            commodity.minimum_price,
            commodity.maximum_price,
            commodity.elasticity_bp,
            commodity.spread_bp,
            inventory.quantity,
            inventory.target,
            settings.basis_point_scale,
            spread_multiplier,
        )
        if (old_bid, old_ask) != (inventory.bid_price, inventory.ask_price):
            emit(
                "PriceChanged",
                {
                    "port_id": port_id,
                    "commodity_id": commodity_id,
                    "old_bid": old_bid,
                    "old_ask": old_ask,
                    "new_bid": inventory.bid_price,
                    "new_ask": inventory.ask_price,
                    "quantity": inventory.quantity,
                    "target": inventory.target,
                    "causes": sorted(causes),
                },
            )


def apply_daily_economy(world: WorldState, dirty: DirtyPrices, emit: EventEmitter) -> None:
    for port_id in sorted(world.ports):
        port = world.ports[port_id]
        for commodity_id in sorted(port.inventories):
            inventory = port.inventories[commodity_id]
            production_bp = active_event_multiplier(world, port_id, commodity_id, "production")
            consumption_bp = active_event_multiplier(world, port_id, commodity_id, "consumption")
            produced = inventory.daily_production * production_bp // 10_000
            inventory.quantity += produced
            world.ledger.produced[commodity_id] += produced

            demand = inventory.daily_consumption * consumption_bp // 10_000
            consumed = min(inventory.quantity, demand)
            unmet = demand - consumed
            inventory.quantity -= consumed
            world.ledger.consumed[commodity_id] += consumed
            world.ledger.unmet_demand[commodity_id] += unmet
            household_spending = consumed * world.commodities[commodity_id].base_price
            port.treasury += household_spending
            world.ledger.local_consumption_revenue += household_spending

            overflow = max(0, inventory.quantity - inventory.capacity)
            inventory.quantity -= overflow
            world.ledger.overflow[commodity_id] += overflow
            if produced or consumed or unmet or overflow:
                dirty.setdefault((port_id, commodity_id), set()).add("daily_economy")
                emit(
                    "DailyEconomyApplied",
                    {
                        "port_id": port_id,
                        "commodity_id": commodity_id,
                        "produced": produced,
                        "consumed": consumed,
                        "unmet_demand": unmet,
                        "overflow": overflow,
                        "quantity": inventory.quantity,
                    },
                )


def buy_cargo(
    world: WorldState,
    merchant_id: str,
    port_id: str,
    commodity_id: str,
    quantity: int,
    dirty: DirtyPrices,
    unit_price: int | None = None,
) -> str | None:
    merchant = world.merchants.get(merchant_id)
    port = world.ports.get(port_id)
    if merchant is None or port is None or commodity_id not in world.commodities:
        return "unknown merchant, port, or commodity"
    if quantity <= 0:
        return "quantity must be positive"
    if merchant.voyage is not None or merchant.port_id != port_id:
        return "merchant is not available at the requested port"
    inventory = port.inventories[commodity_id]
    if inventory.quantity < quantity:
        return "port has insufficient inventory"
    if merchant.cargo_quantity + quantity > merchant.capacity:
        return "merchant cargo capacity would be exceeded"
    cost = (unit_price if unit_price is not None else inventory.ask_price) * quantity
    if merchant.cash < cost:
        return "merchant has insufficient cash"

    inventory.quantity -= quantity
    merchant.cash -= cost
    port.treasury += cost
    merchant.cargo[commodity_id] = merchant.cargo.get(commodity_id, 0) + quantity
    merchant.cargo_cost_basis[commodity_id] = merchant.cargo_cost_basis.get(commodity_id, 0) + cost
    dirty.setdefault((port_id, commodity_id), set()).add("cargo_purchase")
    return None


def sell_cargo(
    world: WorldState,
    merchant_id: str,
    port_id: str,
    commodity_id: str,
    quantity: int,
    dirty: DirtyPrices,
    unit_price: int | None = None,
) -> tuple[str | None, int]:
    merchant = world.merchants.get(merchant_id)
    port = world.ports.get(port_id)
    if merchant is None or port is None or commodity_id not in world.commodities:
        return "unknown merchant, port, or commodity", 0
    if quantity <= 0:
        return "quantity must be positive", 0
    if merchant.voyage is not None or merchant.port_id != port_id:
        return "merchant is not available at the requested port", 0
    held = merchant.cargo.get(commodity_id, 0)
    if held < quantity:
        return "merchant has insufficient cargo", 0
    clean_quantity = held - merchant.stolen_cargo.get(commodity_id, 0)
    if quantity > clean_quantity:
        return "lawful buyers refuse stolen cargo; fence it at Blackwater Cay", 0
    inventory = port.inventories[commodity_id]
    if inventory.quantity + quantity > inventory.capacity:
        return "port storage capacity would be exceeded", 0
    proceeds = (unit_price if unit_price is not None else inventory.bid_price) * quantity
    if port.treasury < proceeds:
        return "port treasury has insufficient cash", 0

    total_basis = merchant.cargo_cost_basis.get(commodity_id, 0)
    sold_basis = (
        total_basis if quantity == clean_quantity else (total_basis * quantity) // clean_quantity
    )
    inventory.quantity += quantity
    port.treasury -= proceeds
    merchant.cash += proceeds
    merchant.cargo[commodity_id] = held - quantity
    merchant.cargo_cost_basis[commodity_id] = total_basis - sold_basis
    if merchant.cargo[commodity_id] == 0:
        del merchant.cargo[commodity_id]
        del merchant.cargo_cost_basis[commodity_id]
    profit = proceeds - sold_basis
    merchant.realized_profit += profit
    merchant.completed_trades += 1
    dirty.setdefault((port_id, commodity_id), set()).add("cargo_sale")
    return None, profit


def best_merchant_opportunity(
    world: WorldState, merchant: Merchant, settings: SimulationSettings
) -> MerchantOpportunity | None:
    if merchant.port_id is None or merchant.voyage is not None or merchant.cargo:
        return None

    opportunities: list[MerchantOpportunity] = []
    for route in sorted(world.routes.values(), key=lambda item: item.id):
        if merchant.port_id not in (route.port_a, route.port_b):
            continue
        destination_id = route.other_end(merchant.port_id)
        origin = world.ports[merchant.port_id]
        destination = world.ports[destination_id]
        available_cash = merchant.cash - route.operating_cost
        if available_cash <= 0:
            continue
        for commodity_id in sorted(world.commodities):
            origin_inventory = origin.inventories[commodity_id]
            destination_inventory = destination.inventories[commodity_id]
            ask = origin_inventory.ask_price
            report = (
                world.port_market_reports.get(merchant.port_id, {})
                .get(destination_id, {})
                .get(commodity_id)
            )
            if world.port_market_reports and report is None:
                continue
            bid = report.bid_price if report is not None else destination_inventory.bid_price
            quantity = min(
                merchant.capacity - merchant.cargo_quantity,
                origin_inventory.quantity,
                available_cash // ask,
                destination.treasury // bid if bid else 0,
                destination_inventory.capacity - destination_inventory.quantity,
            )
            if quantity <= 0:
                continue
            expected_profit = (bid - ask) * quantity - route.operating_cost
            preference = merchant.preferences[commodity_id]
            score = ((bid - ask) * quantity * preference) // settings.basis_point_scale
            score -= route.operating_cost
            if score > 0 and expected_profit > 0:
                opportunities.append(
                    MerchantOpportunity(
                        merchant_id=merchant.id,
                        commodity_id=commodity_id,
                        origin_id=origin.id,
                        destination_id=destination_id,
                        route_id=route.id,
                        quantity=quantity,
                        expected_profit=expected_profit,
                        score=score,
                    )
                )
    if not opportunities:
        return None
    return sorted(
        opportunities,
        key=lambda item: (-item.score, item.commodity_id, item.route_id, item.destination_id),
    )[0]
