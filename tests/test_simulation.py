import json
from dataclasses import replace
from pathlib import Path

from pirate_trading.diagnostics import JsonlEventWriter, format_report
from pirate_trading.models import BeginVoyage, BuyCargo, BuyProvisions
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world


def test_merchants_complete_buy_travel_sell_cycles() -> None:
    settings = replace(SETTINGS, simulation_ticks=200, write_event_log=False)
    world = load_world(settings)
    report = SimulationEngine(world, settings).run()

    assert report.invariant_failures == 0
    ai_merchants = [
        merchant for merchant in world.merchants.values() if merchant.controller == "ai"
    ]
    assert all(merchant.completed_trades > 0 for merchant in ai_merchants)
    assert all(merchant.completed_voyages > 0 for merchant in ai_merchants)
    assert report.event_counts["CargoPurchased"] > 0
    assert report.event_counts["CargoSold"] > 0


def test_player_empty_voyage_takes_exact_route_duration() -> None:
    settings = replace(SETTINGS, simulation_ticks=1, write_event_log=False)
    world = load_world(settings)
    merchant = world.merchants[world.player_id]
    route = next(
        route for route in world.routes.values() if merchant.port_id in (route.port_a, route.port_b)
    )
    destination = route.other_end(merchant.port_id)
    engine = SimulationEngine(world, settings)

    events = engine.execute_commands(
        [
            BuyProvisions(merchant.id, merchant.port_id, 2),
            BeginVoyage(merchant.id, route.id, destination),
        ]
    )
    assert any(event.event_type == "ShipDeparted" for event in events)
    assert world.tick == 0
    assert merchant.voyage.remaining_ticks == route.travel_ticks

    for _ in range(route.travel_ticks - 1):
        engine.step()
    assert merchant.voyage is not None
    assert merchant.voyage.remaining_ticks == 1

    engine.step()
    assert merchant.voyage is None
    assert merchant.port_id == destination


def test_immediate_player_trade_does_not_advance_world_time() -> None:
    settings = replace(SETTINGS, write_event_log=False)
    world = load_world(settings)
    engine = SimulationEngine(world, settings)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    inventory = port.inventories["grain"]
    produced_before = dict(world.ledger.produced)
    quantity_before = inventory.quantity

    events = engine.execute_commands([BuyCargo(player.id, port.id, "grain", 1)])

    assert world.tick == 0
    assert world.ledger.produced == produced_before
    assert inventory.quantity == quantity_before - 1
    assert not world.pending_commands
    assert any(event.event_type == "CargoPurchased" for event in events)


def test_market_and_event_history_are_capped() -> None:
    settings = replace(
        SETTINGS,
        simulation_ticks=800,
        market_history_limit=5,
        recent_event_limit=7,
        write_event_log=False,
    )
    world = load_world(settings)

    SimulationEngine(world, settings).run()

    assert all(
        len(samples) == 5
        for port_history in world.price_history.values()
        for samples in port_history.values()
    )
    assert len(world.recent_events) == 7


def test_jsonl_events_and_human_report_are_inspectable(tmp_path: Path) -> None:
    settings = replace(SETTINGS, simulation_ticks=100, write_event_log=False)
    world = load_world(settings)
    event_path = tmp_path / "events.jsonl"
    with JsonlEventWriter(event_path) as writer:
        report = SimulationEngine(world, settings, event_sink=writer.write).run()

    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    event_types = {event["event_type"] for event in events}
    summary = format_report(report, world)

    assert {"MerchantDecision", "PriceChanged"} <= event_types
    assert "State hash:" in summary
    assert "Invariant failures: 0" in summary
