"""Acceptance tests for the map camera and deliberate raid-loot flow."""

import random

import pygame
import pytest

from pirate_trading.models import (
    BuyCargo,
    ClaimRaidLoot,
    FenceStolenCargo,
    RaidPort,
    SellCargo,
)
from pirate_trading.persistence import load_game, save_game
from pirate_trading.simulation import SimulationEngine, load_world
from pirate_trading.ui import Camera, UILayout, build_cargo_holdings


def test_camera_round_trip_cursor_zoom_pan_and_network_frame() -> None:
    viewport = pygame.Rect(64, 56, 896, 576)
    camera = Camera((1_000, 706), viewport)
    points = [(245.0, 375.0), (570.0, 585.0)]
    camera.frame_points(points, maximum_zoom=2.0)

    assert 1.0 <= camera.zoom <= 2.0
    screen = camera.world_to_screen(points[0])
    round_trip = camera.screen_to_world(screen)
    assert round_trip == pytest.approx(points[0])

    cursor = (420.0, 300.0)
    anchored = camera.screen_to_world(cursor)
    camera.zoom_at(1.15, cursor)
    assert camera.screen_to_world(cursor) == pytest.approx(anchored)
    camera.pan(10_000, 10_000)
    source = camera.source_rect()
    assert source.left >= 0 and source.top >= 0
    assert source.right <= 1_000 and source.bottom <= 706


def test_layout_regions_are_separate_and_inspector_can_collapse() -> None:
    expanded = UILayout.build((1_280, 720))
    collapsed = UILayout.build((1_280, 720), inspector_collapsed=True)

    assert expanded.regions_do_not_overlap()
    assert not expanded.map_viewport.colliderect(expanded.action_tray)
    assert expanded.inspector.width == 320
    assert collapsed.inspector.width == 0
    assert collapsed.map_viewport.width > expanded.map_viewport.width


def test_port_raid_requires_atomic_player_loot_selection() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.execute_commands([BuyCargo(player.id, port.id, "grain", 2)])
    engine.random_service._streams["player_raids"] = random.Random(1)
    starting_condition = player.ship.condition

    events = engine.execute_commands([RaidPort(player.id, port.id)])
    result = next(event for event in events if event.event_type == "PlayerRaidResolved")
    pending = world.pending_raid_loot[result.payload["raid_id"]]

    assert result.payload["success"]
    assert pending.maximum_haul == 15
    assert player.cargo.get("medicine", 0) == 0
    assert player.ship.condition == starting_condition - 3

    before = world.state_hash()
    rejected = engine.execute_commands([ClaimRaidLoot(player.id, pending.id, {"grain": 16})])
    assert any(event.event_type == "CommandRejected" for event in rejected)
    assert world.state_hash() != before  # the rejection event is recorded
    assert player.cargo["grain"] == 2
    assert pending.id in world.pending_raid_loot

    engine.execute_commands([ClaimRaidLoot(player.id, pending.id, {"grain": 8, "timber": 7})])
    assert player.cargo["grain"] == 10
    assert player.stolen_cargo == {"grain": 8, "timber": 7}
    assert player.ship.condition == starting_condition - 8
    assert pending.id not in world.pending_raid_loot
    engine.assert_invariants()


def test_clean_cargo_sells_but_stolen_port_loot_requires_blackwater() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.execute_commands([BuyCargo(player.id, port.id, "grain", 2)])
    engine.random_service._streams["player_raids"] = random.Random(1)
    raid = engine.execute_commands([RaidPort(player.id, port.id)])
    raid_id = next(
        event.payload["raid_id"] for event in raid if event.event_type == "PlayerRaidResolved"
    )
    engine.execute_commands([ClaimRaidLoot(player.id, raid_id, {"grain": 8})])

    world.tick = 24
    world.trade_access["1:player:crowns_haven"] = True
    sold = engine.execute_commands([SellCargo(player.id, port.id, "grain", 2)])
    assert any(event.event_type == "CargoSold" for event in sold)
    assert player.cargo["grain"] == 8
    assert player.stolen_cargo["grain"] == 8
    assert player.cargo_cost_basis["grain"] == 0

    rejected = engine.execute_commands([SellCargo(player.id, port.id, "grain", 1)])
    reason = next(
        event.payload["reason"] for event in rejected if event.event_type == "CommandRejected"
    )
    assert "stolen cargo" in reason
    assert player.cargo["grain"] == 8

    player.port_id = "blackwater_cay"
    fenced = engine.execute_commands([FenceStolenCargo(player.id, "blackwater_cay", "grain", 3)])
    assert any(event.event_type == "StolenCargoFenced" for event in fenced)
    assert player.cargo["grain"] == 5
    assert player.stolen_cargo["grain"] == 5
    engine.assert_invariants()


def test_holdings_expose_clean_and_stolen_quantities() -> None:
    world = load_world()
    player = world.merchants[world.player_id]
    player.cargo["medicine"] = 5
    player.cargo_cost_basis["medicine"] = 0
    player.stolen_cargo["medicine"] = 5

    holding = next(
        item
        for item in build_cargo_holdings(world, player.id, player.port_id)
        if item.commodity_id == "medicine"
    )

    assert holding.quantity == 5
    assert holding.clean_quantity == 0
    assert holding.stolen_quantity == 5
    assert holding.sellable_quantity == 0


def test_pending_raid_loot_survives_format_two_save(tmp_path) -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    engine.random_service._streams["player_raids"] = random.Random(1)
    raid = engine.execute_commands([RaidPort(player.id, player.port_id)])
    raid_id = next(
        event.payload["raid_id"] for event in raid if event.event_type == "PlayerRaidResolved"
    )
    path = tmp_path / "pending_raid.json"

    save_game(world, path)
    restored = load_game(path)

    assert raid_id in restored.pending_raid_loot
    assert restored.pending_raid_loot[raid_id].maximum_haul == 15


def test_incoming_pirate_loss_is_capped_below_aggressive_ship_haul() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.execute_commands([BuyCargo(player.id, port.id, "cloth", 10)])

    engine._lose_pirate_cargo(player, 50)

    assert player.cargo["cloth"] == 5
    assert world.ledger.piracy_losses["cloth"] == 5
    assert world.ledger.piracy_losses["cloth"] < engine.settings.ship_raid_haul_limit
    engine.assert_invariants()


def test_failed_port_raid_has_no_manifest_and_retains_failure_damage() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    engine.random_service._streams["player_raids"] = random.Random(6)

    events = engine.execute_commands([RaidPort(player.id, player.port_id)])
    result = next(event for event in events if event.event_type == "PlayerRaidResolved")

    assert not result.payload["success"]
    assert result.payload["raid_id"] is None
    assert player.ship.condition == 89
    assert not world.pending_raid_loot


def test_reinforced_hull_rounds_total_raid_damage_upward() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    player.ship.upgrades.append("reinforced_hull")
    engine.random_service._streams["player_raids"] = random.Random(1)
    raid = engine.execute_commands([RaidPort(player.id, player.port_id)])
    raid_id = next(
        event.payload["raid_id"] for event in raid if event.event_type == "PlayerRaidResolved"
    )

    engine.execute_commands([ClaimRaidLoot(player.id, raid_id, {"grain": 8, "timber": 7})])

    assert player.ship.condition == 94
