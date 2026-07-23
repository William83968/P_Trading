from pirate_trading.economy import (
    apply_daily_economy,
    best_merchant_opportunity,
    buy_cargo,
    calculate_quote,
    sell_cargo,
)
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world


def test_price_rises_as_inventory_falls_and_stays_bounded() -> None:
    commodity = load_world().commodities["grain"]
    quotes = [
        calculate_quote(
            commodity.base_price,
            commodity.minimum_price,
            commodity.maximum_price,
            commodity.elasticity_bp,
            commodity.spread_bp,
            quantity,
            100,
            SETTINGS.basis_point_scale,
        )
        for quantity in (0, 100, 300)
    ]

    assert quotes[0][0] > quotes[1][0] > quotes[2][0]
    assert quotes[0][1] > quotes[0][0]
    assert quotes[2][0] >= commodity.minimum_price * 9_500 // 10_000
    assert quotes[0][1] <= (commodity.maximum_price * 10_500 + 9_999) // 10_000


def test_daily_economy_records_production_consumption_and_overflow() -> None:
    world = load_world()
    inventory = world.ports["crowns_haven"].inventories["grain"]
    inventory.quantity = inventory.capacity
    dirty = {}
    events = []

    apply_daily_economy(
        world, dirty, lambda event_type, payload: events.append((event_type, payload))
    )

    assert inventory.quantity == inventory.capacity
    assert world.ledger.produced["grain"] == 25
    assert world.ledger.consumed["grain"] == 20
    assert world.ledger.overflow["grain"] == 15
    assert events


def test_buy_and_sell_are_atomic_and_conserve_cash_and_goods() -> None:
    world = load_world()
    merchant = next(item for item in world.merchants.values() if item.port_id == "crowns_haven")
    dirty = {}
    before = world.to_primitive()

    reason = buy_cargo(world, merchant.id, "crowns_haven", "grain", 10_000, dirty)
    assert reason is not None
    assert world.to_primitive() == before

    assert buy_cargo(world, merchant.id, "crowns_haven", "grain", 1, dirty) is None
    reason, _ = sell_cargo(world, merchant.id, "crowns_haven", "grain", 1, dirty)
    assert reason is None
    SimulationEngine(world).assert_invariants()


def test_merchant_finds_a_profitable_executable_trade() -> None:
    world = load_world()
    merchant = world.merchants["merchant_1"]

    opportunity = best_merchant_opportunity(world, merchant, SETTINGS)

    assert opportunity is not None
    assert opportunity.quantity <= merchant.capacity
    assert opportunity.expected_profit > 0
