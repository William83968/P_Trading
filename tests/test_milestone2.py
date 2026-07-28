import json
import os
import random
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest

from pirate_trading.economy import apply_daily_economy
from pirate_trading.models import (
    ActiveCondition,
    BeginVoyage,
    BuyCargo,
    BuyProvisions,
    ClaimRaidLoot,
    DomainEvent,
    PayWages,
    PendingEncounter,
    PurchaseUpgrade,
    RaidPort,
    RaidShip,
    RepairShip,
    ResolveEncounter,
    RespondToOpportunity,
)
from pirate_trading.narrative import (
    create_event_opportunity,
    maybe_offer_intro_story,
    publish_market_reports,
)
from pirate_trading.persistence import SaveError, load_game, save_game
from pirate_trading.settings import SETTINGS
from pirate_trading.simulation import SimulationEngine, load_world
from pirate_trading.ui import PirateTradingUI
from pirate_trading.world_events import OUTLOOK_RULES


def test_classic_two_dice_distribution_and_settings_are_complete() -> None:
    counts = {total: 0 for total in range(2, 13)}
    for die_one in range(1, 7):
        for die_two in range(1, 7):
            counts[die_one + die_two] += 1

    assert counts == {
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 6,
        8: 5,
        9: 4,
        10: 3,
        11: 2,
        12: 1,
    }
    assert set(OUTLOOK_RULES) == set(counts)


def test_weekly_roll_interrupts_at_exact_boundary_without_advancing() -> None:
    settings = replace(SETTINGS, simulation_ticks=168, write_event_log=False)
    world = load_world(settings)
    engine = SimulationEngine(world, settings)

    report = engine.run()
    events = engine.step()

    assert report.completed_ticks == 168
    assert world.weekly_outlook.week_index == 1
    assert world.weekly_outlook.start_tick == 168
    assert world.weekly_outlook.end_tick == 336
    assert world.weekly_outlook.reveal_pending
    assert world.tick == 168
    assert any(event.event_type == "WeeklyOutlookRolled" for event in events)


def test_weekly_result_restores_without_load_time_reroll(tmp_path: Path) -> None:
    settings = replace(
        SETTINGS,
        simulation_ticks=168,
        quick_save_path=tmp_path / "save.json",
        write_event_log=False,
    )
    world = load_world(settings)
    engine = SimulationEngine(world, settings)
    engine.run()
    engine.step()
    rolled = (
        world.weekly_outlook.die_one,
        world.weekly_outlook.die_two,
        world.weekly_outlook.total,
    )
    save_game(world, settings=settings)

    restored = load_game(settings=settings)
    restored_engine = SimulationEngine(restored, settings)
    restored_engine.step()

    assert (
        restored.weekly_outlook.die_one,
        restored.weekly_outlook.die_two,
        restored.weekly_outlook.total,
    ) == rolled
    assert restored.tick == 169


def test_trade_disruption_is_cached_and_atomic_for_the_day() -> None:
    world = load_world()
    world.weekly_outlook.total = 2
    world.weekly_outlook.setting_id = "black_flags"
    engine = SimulationEngine(world)
    engine.random_service._streams["trade_access"] = random.Random(6)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    before_cash = player.cash
    before_stock = port.inventories["grain"].quantity
    command = BuyCargo(player.id, port.id, "grain", 1)

    first = engine.execute_commands([command])
    random_state = engine.random_service.stream("trade_access").getstate()
    second = engine.execute_commands([command])

    assert any(event.event_type == "TradeDisrupted" for event in first)
    assert any(event.event_type == "TradeDisrupted" for event in second)
    assert player.cash == before_cash
    assert port.inventories["grain"].quantity == before_stock
    assert engine.random_service.stream("trade_access").getstate() == random_state
    assert len(world.trade_access) == 1


def test_fortunes_crown_discount_is_once_daily_and_cash_conserving() -> None:
    world = load_world()
    world.weekly_outlook.total = 12
    world.weekly_outlook.setting_id = "fortunes_crown"
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    ask = port.inventories["grain"].ask_price
    combined_cash = player.cash + port.treasury

    first = engine.execute_commands([BuyCargo(player.id, port.id, "grain", 1)])
    second_regular_price = port.inventories["grain"].ask_price
    second = engine.execute_commands([BuyCargo(player.id, port.id, "grain", 1)])

    prices = [
        event.payload["unit_price"]
        for event in (*first, *second)
        if event.event_type == "CargoPurchased"
    ]
    assert prices == [ask * 9_500 // 10_000, second_regular_price]
    assert player.cash + port.treasury == combined_cash
    assert len(world.fortune_consumed) == 1


def test_ship_services_use_commands_and_preserve_invariants() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port_id = player.port_id
    player.ship.condition = 80
    player.ship.wage_debt = 500

    engine.execute_commands(
        [
            BuyProvisions(player.id, port_id, 1),
            PayWages(player.id, port_id),
            RepairShip(player.id, port_id, 10),
            PurchaseUpgrade(player.id, port_id, "crows_nest"),
        ]
    )

    assert player.ship.provisions == SETTINGS.player_initial_provisions + 1
    assert player.ship.wage_debt == 0
    assert player.ship.condition == 90
    assert "crows_nest" in player.ship.upgrades
    engine.assert_invariants()


def test_event_changes_production_counterfactually() -> None:
    baseline = load_world()
    affected = load_world()
    affected.active_conditions.append(
        ActiveCondition(
            id="harvest",
            event_type="bumper_harvest",
            stage="active",
            start_tick=0,
            transition_tick=0,
            recovery_tick=240,
            end_tick=264,
            severity_bp=5_000,
            port_id="crowns_haven",
            commodity_id="grain",
        )
    )

    apply_daily_economy(baseline, {}, lambda *_: None)
    apply_daily_economy(affected, {}, lambda *_: None)

    assert affected.ledger.produced["grain"] - baseline.ledger.produced["grain"] == 10


def test_relief_calls_require_sustained_need_and_respect_long_cooldown() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    port_id = "crowns_haven"
    commodity_id = "medicine"

    def report_unmet(day: int) -> None:
        world.tick = day * SETTINGS.ticks_per_day
        engine._step_events = [
            DomainEvent(
                tick=world.tick,
                sequence=day,
                event_type="DailyEconomyApplied",
                payload={
                    "port_id": port_id,
                    "commodity_id": commodity_id,
                    "unmet_demand": 1,
                },
            )
        ]
        engine._update_relief_calls()

    report_unmet(1)
    report_unmet(2)
    assert not any(item.event_type == "relief_call" for item in world.active_conditions)

    report_unmet(3)
    assert sum(item.event_type == "relief_call" for item in world.active_conditions) == 1

    world.active_conditions.clear()
    report_unmet(12)
    report_unmet(13)
    report_unmet(14)
    assert not world.active_conditions

    cooldown_tick = world.event_cooldowns[f"relief_call:{port_id}:{commodity_id}"]
    report_unmet(cooldown_tick // SETTINGS.ticks_per_day)
    assert sum(item.event_type == "relief_call" for item in world.active_conditions) == 1


def test_warehouse_fire_temporarily_reduces_and_restores_capacity() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    condition = ActiveCondition(
        id="fire",
        event_type="warehouse_fire",
        stage="active",
        start_tick=0,
        transition_tick=0,
        recovery_tick=48,
        end_tick=72,
        severity_bp=4_000,
        port_id="crowns_haven",
        capacity_reduction_bp=2_500,
    )
    world.active_conditions.append(condition)

    engine._apply_event_loss(condition, {})

    assert all(
        inventory.capacity == 225 for inventory in world.ports["crowns_haven"].inventories.values()
    )
    world.tick = condition.end_tick
    engine._restore_ending_capacity()
    assert all(
        inventory.capacity == 300 for inventory in world.ports["crowns_haven"].inventories.values()
    )
    engine.assert_invariants()


def test_reports_are_scheduled_by_route_time_and_stay_stale() -> None:
    world = load_world()
    publish_market_reports(world)
    report = next(
        item
        for item in world.scheduled_reports
        if item.destination_port_id == "san_cordelia"
        and item.payload["source_port_id"] == "crowns_haven"
        and item.payload["commodity_id"] == "grain"
    )

    rule = OUTLOOK_RULES[world.weekly_outlook.total]
    assert report.delivery_tick == 1 + 36 * rule.report_delay_bp // 10_000
    assert "san_cordelia" not in world.player_market_reports


@pytest.mark.parametrize(
    ("port_id", "choice"),
    [
        ("crowns_haven", "official_station"),
        ("san_cordelia", "coastal_protection"),
        ("blackwater_cay", "retain_informant"),
        ("whitecap_reach", "build_sea_wall"),
        ("isla_esmeralda", "fund_treatment"),
    ],
)
def test_each_port_story_is_offered_once_and_choice_persists(
    port_id: str,
    choice: str,
) -> None:
    world = load_world()
    world.tick = 3 * SETTINGS.ticks_per_day
    player = world.merchants[world.player_id]
    player.port_id = port_id
    events = []
    opportunity = maybe_offer_intro_story(
        world,
        port_id,
        SETTINGS,
        lambda event_type, payload: events.append((event_type, payload)),
    )
    assert opportunity is not None
    assert maybe_offer_intro_story(world, port_id, SETTINGS, lambda *_: None) is None

    engine = SimulationEngine(world)
    engine.execute_commands([RespondToOpportunity(player.id, opportunity.id, choice)])

    progress = world.story_progress[port_id]
    assert progress.status == "resolved"
    assert progress.choice == choice
    assert progress.journal_text


def test_contract_acceptance_survives_save_and_can_be_delivered_later(
    tmp_path: Path,
) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    world = load_world(settings)
    engine = SimulationEngine(world, settings)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.execute_commands([BuyCargo(player.id, port.id, "medicine", 10)])
    opportunity = create_event_opportunity(
        world,
        "relief_test",
        "relief_call",
        port.id,
        "medicine",
        settings,
        lambda *_: None,
    )

    accepted = engine.execute_commands([RespondToOpportunity(player.id, opportunity.id, "accept")])
    save_game(world, settings=settings)
    restored = load_game(settings=settings)
    restored_engine = SimulationEngine(restored, settings)
    restored_player = restored.merchants[restored.player_id]
    delivered = restored_engine.execute_commands(
        [RespondToOpportunity(restored_player.id, opportunity.id, "deliver")]
    )

    assert any(event.event_type == "OpportunityAccepted" for event in accepted)
    assert restored.opportunities[opportunity.id].status == "resolved"
    assert any(event.event_type == "OpportunityResolved" for event in delivered)
    assert any(event.event_type == "OpportunityCompleted" for event in delivered)
    assert restored_player.cargo.get("medicine", 0) == 0
    restored_engine.assert_invariants()


@pytest.mark.parametrize("failure", ["abandon", "expired"])
def test_accepted_mission_failure_charges_a_ledgered_penalty(failure: str) -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    opportunity = create_event_opportunity(
        world,
        f"failure_{failure}",
        "relief_call",
        player.port_id,
        "medicine",
        SETTINGS,
        lambda *_: None,
    )
    engine.execute_commands([RespondToOpportunity(player.id, opportunity.id, "accept")])
    cash_before = player.cash
    expected_penalty = max(
        SETTINGS.mission_failure_minimum,
        opportunity.reward * SETTINGS.mission_failure_penalty_bp // 10_000,
    )

    if failure == "abandon":
        events = engine.execute_commands(
            [RespondToOpportunity(player.id, opportunity.id, "abandon")]
        )
    else:
        world.tick = opportunity.expiry_tick
        engine._step_events = []
        engine._expire_opportunities()
        events = engine._complete_event_batch()

    failed = next(event for event in events if event.event_type == "OpportunityFailed")
    assert failed.payload["reason"] == ("abandoned" if failure == "abandon" else "expired")
    assert failed.payload["penalty_paid"] == expected_penalty
    assert player.cash == cash_before - expected_penalty
    assert world.ledger.mission_penalties == expected_penalty
    assert opportunity.id not in player.mission_load
    engine.assert_invariants()


def test_player_can_raid_ship_with_cost_damage_and_conserved_loot() -> None:
    world = load_world()
    world.weekly_outlook.total = 7
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    target = next(
        merchant
        for merchant in world.merchants.values()
        if merchant.controller == "ai" and merchant.port_id == player.port_id
    )
    route = world.routes["crowns_haven_san_cordelia"]
    engine.execute_commands([BuyCargo(target.id, player.port_id, "cloth", 3)])
    engine.execute_commands(
        [
            BeginVoyage(target.id, route.id, route.other_end(target.port_id)),
            BeginVoyage(player.id, route.id, route.other_end(player.port_id)),
        ]
    )
    engine.random_service._streams["player_raids"] = random.Random(1)
    starting_condition = player.ship.condition

    events = engine.execute_commands([RaidShip(player.id, target.id)])

    result = next(event for event in events if event.event_type == "PlayerRaidResolved")
    assert result.payload["success"]
    assert player.cargo.get("cloth", 0) == 0
    pending = world.pending_raid_loot[result.payload["raid_id"]]
    engine.execute_commands([ClaimRaidLoot(player.id, pending.id, {"cloth": 3})])
    assert player.cargo.get("cloth", 0) == 3
    assert player.stolen_cargo["cloth"] == 3
    assert player.ship.condition == starting_condition - 3
    assert world.ledger.raid_expenses == SETTINGS.ship_raid_cost
    engine.assert_invariants()


def test_player_port_raid_sets_alert_cooldown_and_cannot_be_spammed() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.random_service._streams["player_raids"] = random.Random(1)

    first = engine.execute_commands([RaidPort(player.id, port.id)])
    cash_after_first = player.cash
    second = engine.execute_commands([RaidPort(player.id, port.id)])

    assert any(event.event_type == "PlayerRaidResolved" for event in first)
    assert any(event.event_type == "CommandRejected" for event in second)
    assert player.cash == cash_after_first
    assert world.raid_cooldowns[f"{player.id}:{port.id}"] == SETTINGS.port_raid_cooldown_ticks
    day = world.tick // SETTINGS.ticks_per_day
    assert world.trade_access[f"{day}:{player.id}:{port.id}"] is False
    engine.assert_invariants()


def test_pirate_surrender_is_bounded_and_recorded() -> None:
    world = load_world()
    engine = SimulationEngine(world)
    player = world.merchants[world.player_id]
    port = world.ports[player.port_id]
    engine.execute_commands([BuyCargo(player.id, port.id, "cloth", 3)])
    encounter = PendingEncounter("test_raid", player.id, "crowns_haven_san_cordelia", 3_000, 500)
    world.pending_encounters[encounter.id] = encounter

    engine.execute_commands([ResolveEncounter(player.id, encounter.id, "surrender")])

    assert encounter.resolved
    assert 0 <= player.cargo.get("cloth", 0) < 3
    assert world.ledger.piracy_losses["cloth"] > 0
    engine.assert_invariants()


def test_weekly_reveal_animates_pauses_and_acknowledges(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        assert app.overlay == "weekly_roll"
        assert app.time.paused
        app.update(settings.weekly_reveal_seconds + 0.1)
        app.draw()
        assert "continue_week" in app.buttons
        app._dismiss_weekly_reveal()
        assert not app.world.weekly_outlook.reveal_pending
        assert app.overlay is None
    finally:
        app.shutdown()


def test_format_one_save_requires_a_new_game(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"save_format_version": 1}), encoding="utf-8")

    with pytest.raises(SaveError, match="Start a New Game"):
        load_game(path)


def test_weekly_heading_is_clickable(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app._dismiss_weekly_reveal()
        app.draw()
        button = app.buttons["weekly_heading"]
        app._handle_click(button.rect.center)
        assert app.overlay == "weekly_info"
    finally:
        app.shutdown()


def test_major_event_report_opens_a_pausing_popup(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app._dismiss_weekly_reveal()
        app.time.paused = False

        app._queue_major_event(
            {
                "report_id": "storm_report",
                "headline": "Hurricane",
                "information_kind": "ESTIMATE",
            }
        )
        app.draw()

        assert app.overlay == "major_event"
        assert app.time.paused
        assert "continue_major_event" in app.buttons
        app._dismiss_major_event()
        assert app.time.paused
    finally:
        app.shutdown()


def test_accepted_contract_remains_selected_when_new_offer_appears(
    tmp_path: Path,
) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app._dismiss_weekly_reveal()
        first = create_event_opportunity(
            app.world,
            "first",
            "relief_call",
            app.player.port_id,
            "medicine",
            settings,
            lambda *_: None,
        )
        app.engine.execute_commands([RespondToOpportunity(app.player.id, first.id, "accept")])
        create_event_opportunity(
            app.world,
            "second",
            "shipyard_commission",
            app.player.port_id,
            "timber",
            settings,
            lambda *_: None,
        )

        app._handle_game_action("opportunities")
        app.draw()

        assert app.selected_opportunity_id == first.id
        assert f"opportunity:{first.id}:deliver" in app.buttons
    finally:
        app.shutdown()


def test_pirate_encounter_starts_explosion_animation(tmp_path: Path) -> None:
    settings = replace(SETTINGS, quick_save_path=tmp_path / "save.json")
    app = PirateTradingUI(settings, enable_audio=False)
    try:
        app.new_game()
        app._dismiss_weekly_reveal()
        app.selected_port_id = "blackwater_cay"
        app._execute_sail()
        route_id = app.player.voyage.route_id
        app.world.routes[route_id] = replace(
            app.world.routes[route_id],
            risk_bp=240_000,
        )

        app.update(0.25)
        app.draw()

        assert app.overlay == "encounter"
        assert app.explosion_elapsed is not None
        assert app.assets.image("explode.png").get_size() == (128, 128)
    finally:
        app.shutdown()
