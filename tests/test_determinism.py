import json
from dataclasses import replace

from pirate_trading.models import WorldState
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import RandomService, SimulationEngine, load_world


def _run(seed: int = 2167):
    settings = replace(SETTINGS, world_seed=seed, write_event_log=False)
    world = load_world(settings)
    report = SimulationEngine(world, settings).run()
    return world, report


def test_named_random_streams_repeat_and_restore_independently() -> None:
    first = RandomService(2167)
    weather_value = first.stream("weather").random()
    merchant_value = first.stream("merchant_ai").random()
    snapshot = first.snapshot()
    expected_next = first.stream("merchant_ai").random()

    second = RandomService(2167)
    assert second.stream("weather").random() == weather_value
    assert second.stream("merchant_ai").random() == merchant_value
    assert weather_value != merchant_value
    second.restore(snapshot)
    assert second.stream("merchant_ai").random() == expected_next


def test_world_round_trip_preserves_hash() -> None:
    world = load_world()
    primitive = json.loads(json.dumps(world.to_primitive()))
    restored = WorldState.from_primitive(primitive)

    assert restored.state_hash() == world.state_hash()


def test_ten_thousand_ticks_are_deterministic_and_stable() -> None:
    first_world, first_report = _run()
    second_world, second_report = _run()

    assert first_report.state_hash == second_report.state_hash
    assert first_report.invariant_failures == 0
    assert all(
        merchant.completed_trades > 0
        for merchant in first_world.merchants.values()
        if merchant.controller == "ai"
    )

    for commodity_id, commodity in first_world.commodities.items():
        inventories = [port.inventories[commodity_id] for port in first_world.ports.values()]
        maximum_bid = commodity.maximum_price * 9_500 // 10_000
        assert not all(inventory.quantity == 0 for inventory in inventories)
        assert not all(inventory.bid_price == maximum_bid for inventory in inventories)


def test_different_seed_changes_preferences_and_final_hash() -> None:
    first_world, first_report = _run(2167)
    other_world, other_report = _run(2168)

    first_preferences = {
        merchant_id: merchant.preferences for merchant_id, merchant in first_world.merchants.items()
    }
    other_preferences = {
        merchant_id: merchant.preferences for merchant_id, merchant in other_world.merchants.items()
    }
    assert first_preferences != other_preferences
    assert first_report.state_hash != other_report.state_hash
