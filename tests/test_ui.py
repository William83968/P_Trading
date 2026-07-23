import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from pirate_trading.models import Commodity, DomainEvent, Inventory, MarketSample, WorldState
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world
from pirate_trading.ui import (
    PirateTradingUI,
    TimeController,
    build_cargo_holdings,
    build_news_items,
    market_status,
    price_trend,
)


def test_importing_ui_does_not_initialize_pygame() -> None:
    pygame.quit()
    assert not pygame.get_init()


def test_time_controller_schedules_fixed_ticks() -> None:
    controller = TimeController((4, 8, 16))
    assert controller.due_ticks(1.0, 64) == 0

    controller.paused = False
    assert controller.due_ticks(0.5, 64) == 2
    controller.faster()
    assert controller.due_ticks(0.5, 64) == 4


def test_market_status_and_trend_are_data_driven() -> None:
    inventory = Inventory(50, 100, 300, 0, 0, 100, 110)
    assert market_status(inventory) == "Shortage"
    inventory.quantity = 100
    assert market_status(inventory) == "Balanced"
    inventory.quantity = 200
    assert market_status(inventory) == "Surplus"
    assert price_trend([MarketSample(0, 100, 90, 100), MarketSample(24, 80, 100, 120)]) == "↑"


def test_ui_assets_load_and_quit_event_exits_cleanly(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    pygame.event.post(pygame.event.Event(pygame.QUIT))

    assert app.run(maximum_frames=2) == 0


def test_ui_trade_button_uses_immediate_player_command(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.selected_commodity_id = "grain"
        starting_tick = app.world.tick
        starting_cargo = app.player.cargo_quantity

        app._execute_trade(True)

        assert app.world.tick == starting_tick
        assert app.player.cargo_quantity == starting_cargo + 1
    finally:
        app.shutdown()


def test_arrival_automatically_pauses_and_selects_destination(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        destination = "blackwater_cay"
        app.selected_port_id = destination
        app._execute_sail()
        assert not app.time.paused

        app.update(20.0)

        assert app.time.paused
        assert app.player.port_id == destination
        assert app.selected_port_id == destination
    finally:
        app.shutdown()


def test_market_rendering_does_not_require_commodity_assets(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.world.commodities["spice"] = Commodity("spice", "Spice", 500, 250, 1250, 7500, 500)
        for port in app.world.ports.values():
            port.inventories["spice"] = Inventory(100, 100, 300, 5, 5, 475, 525)
            app.world.price_history[port.id]["spice"] = [MarketSample(0, 100, 475, 525)]
        app.selected_commodity_id = "spice"

        app.draw()

        assert "commodity:spice" in app.buttons
    finally:
        app.shutdown()


def test_cargo_holdings_derive_cost_value_profit_and_sell_limit() -> None:
    world = load_world(SETTINGS)
    player = world.merchants[world.player_id]
    inventory = world.ports[player.port_id].inventories["grain"]
    quantity = 3
    player.cargo["grain"] = quantity
    player.cargo_cost_basis["grain"] = 360

    holding = build_cargo_holdings(world, world.player_id, player.port_id)[0]

    assert holding.quantity == quantity
    assert holding.cost_basis == 360
    assert holding.average_unit_cost == 120
    assert holding.estimated_value == quantity * inventory.bid_price
    assert holding.estimated_profit == holding.estimated_value - 360
    assert holding.sellable_quantity == quantity


def test_hold_valuation_follows_selected_port_without_mutating_world() -> None:
    world = load_world(SETTINGS)
    player = world.merchants[world.player_id]
    player.cargo["grain"] = 2
    player.cargo_cost_basis["grain"] = 200
    before = world.to_primitive()

    local = build_cargo_holdings(world, world.player_id, "crowns_haven")[0]
    remote = build_cargo_holdings(world, world.player_id, "blackwater_cay")[0]

    assert local.bid_price != remote.bid_price
    assert remote.sellable_quantity == 0
    assert world.to_primitive() == before


def test_market_and_hold_sell_through_identical_command_path(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.selected_commodity_id = "grain"
        app.trade_quantity = 2
        app._execute_trade(True)
        before_sale = app.world.to_primitive()
        starting_tick = app.world.tick

        app.active_sidebar_tab = "market"
        app.trade_quantity = 1
        app._execute_trade(False)
        market_result = app.world.state_hash()

        app.world = WorldState.from_primitive(before_sale)
        app.engine = SimulationEngine(app.world, settings)
        app.active_sidebar_tab = "hold"
        app.trade_quantity = 1
        app._execute_trade(False)

        assert app.world.tick == starting_tick
        assert app.world.state_hash() == market_result
    finally:
        app.shutdown()


def test_hold_tab_selection_remote_and_travel_states(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.selected_commodity_id = "grain"
        app._execute_trade(True)
        selected_port = app.selected_port_id
        selected_commodity = app.selected_commodity_id

        app._activate_tab("hold")
        assert app.selected_port_id == selected_port
        assert app.selected_commodity_id == selected_commodity
        assert app._trade_unavailable_reason(buying=False) == ""

        app.selected_port_id = "blackwater_cay"
        assert app._trade_unavailable_reason(buying=False) == "Select your current port to trade."

        app._activate_tab("port")
        app._execute_sail()
        app._activate_tab("hold")
        assert app._trade_unavailable_reason(buying=False) == (
            "Trading is unavailable while travelling."
        )
    finally:
        app.shutdown()


def test_long_market_and_hold_lists_scroll_without_assets(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        for index in range(7):
            commodity_id = f"test_{index}"
            app.world.commodities[commodity_id] = Commodity(
                commodity_id,
                f"Test {index}",
                500,
                250,
                1250,
                7500,
                500,
            )
            app.player.cargo[commodity_id] = 1
            app.player.cargo_cost_basis[commodity_id] = 500
            for port in app.world.ports.values():
                port.inventories[commodity_id] = Inventory(100, 100, 300, 5, 5, 475, 525)
                app.world.price_history[port.id][commodity_id] = [MarketSample(0, 100, 475, 525)]

        app.active_sidebar_tab = "market"
        app._handle_scroll(-20, (1100, 300))
        app.draw()
        assert app.row_offsets["market"] > 0
        assert "commodity:test_6" in app.buttons

        app.active_sidebar_tab = "hold"
        app._handle_scroll(-20, (1100, 300))
        app.draw()
        assert app.row_offsets["hold"] > 0
        assert "holding:test_6" in app.buttons
    finally:
        app.shutdown()


def test_news_groups_noise_prioritizes_player_and_links_context() -> None:
    world = load_world(SETTINGS)
    world.recent_events = [
        DomainEvent(
            24,
            1,
            "PriceChanged",
            {"port_id": "san_cordelia", "commodity_id": "sugar"},
        ),
        DomainEvent(
            25,
            2,
            "PriceChanged",
            {"port_id": "san_cordelia", "commodity_id": "sugar"},
        ),
        DomainEvent(
            25,
            3,
            "MerchantDecision",
            {"merchant_id": "merchant_1"},
        ),
        DomainEvent(
            25,
            4,
            "DailyEconomyApplied",
            {
                "port_id": "blackwater_cay",
                "commodity_id": "timber",
                "unmet_demand": 5,
            },
        ),
        DomainEvent(
            25,
            5,
            "CargoPurchased",
            {
                "merchant_id": world.player_id,
                "port_id": "crowns_haven",
                "commodity_id": "grain",
                "quantity": 2,
            },
        ),
    ]

    items = build_news_items(world)

    assert [item.category for item in items] == ["PLAYER", "SHORTAGE", "MARKET"]
    assert items[-1].count == 2
    assert "2 reports" in items[-1].text
    assert {item.information_kind for item in items} == {"FACT"}
    assert all("MerchantDecision" not in item.text for item in items)


def test_news_unread_navigation_and_empty_information_overlays(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.selected_commodity_id = "grain"
        app._execute_trade(True)
        items = build_news_items(app.world, settings)
        assert any(item.latest_sequence > app.news_read_through_sequence for item in items)

        app._handle_game_action("news")
        assert app.news_read_through_sequence == max(item.latest_sequence for item in items)
        target = next(item for item in items if item.commodity_id == "grain")
        app._handle_news_action(f"news:{target.latest_sequence}")
        assert app.overlay is None
        assert app.active_sidebar_tab == "market"
        assert app.selected_port_id == "crowns_haven"
        assert app.selected_commodity_id == "grain"

        app._handle_game_action("opportunities")
        app.draw()
        assert app.overlay == "opportunities"
        app.overlay = None
        app._handle_game_action("journal")
        app.draw()
        assert app.overlay == "journal"
    finally:
        app.shutdown()


def test_load_resets_transient_ui_without_changing_saved_world(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app.selected_commodity_id = "grain"
        app._execute_trade(True)
        app.save()
        expected_hash = app.world.state_hash()

        app.active_sidebar_tab = "hold"
        app.overlay = "journal"
        app.row_offsets["market"] = 3
        app.news_read_through_sequence = -1
        app.continue_game()

        assert app.world.state_hash() == expected_hash
        assert app.active_sidebar_tab == "market"
        assert app.overlay is None
        assert app.row_offsets == {"market": 0, "hold": 0}
        assert app.news_read_through_sequence == max(
            event.sequence for event in app.world.recent_events
        )
    finally:
        app.shutdown()
