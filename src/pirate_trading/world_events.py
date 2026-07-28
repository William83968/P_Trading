"""Causal weekly outlook and world-event rules for Milestone 2."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pirate_trading.models import ActiveCondition, WeeklyOutlook, WorldState
from pirate_trading.settings import SimulationSettings

EventEmitter = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class OutlookRule:
    total: int
    setting_id: str
    name: str
    trade_access_bp: int
    opportunity_bp: int
    raid_bp: int
    spread_bp: int = 10_000
    voyage_bp: int = 10_000
    report_delay_bp: int = 10_000
    reward_bp: int = 10_000
    false_rumour_bp: int = 2_000
    description: str = ""


OUTLOOK_RULES = {
    2: OutlookRule(
        2,
        "black_flags",
        "Black Flags",
        7_500,
        7_000,
        17_500,
        false_rumour_bp=3_000,
        description="Raids and false reports flourish.",
    ),
    3: OutlookRule(
        3,
        "closed_ledgers",
        "Closed Ledgers",
        7_000,
        6_000,
        11_000,
        description="Customs inspections disrupt trade.",
    ),
    4: OutlookRule(
        4,
        "lean_harbors",
        "Lean Harbors",
        8_200,
        7_500,
        12_500,
        spread_bp=12_000,
        description="Thin markets widen every spread.",
    ),
    5: OutlookRule(
        5,
        "contrary_winds",
        "Contrary Winds",
        9_000,
        9_000,
        14_000,
        voyage_bp=9_000,
        description="Poor winds and prowling raiders slow travel.",
    ),
    6: OutlookRule(
        6,
        "uneasy_waters",
        "Uneasy Waters",
        9_200,
        8_500,
        11_500,
        report_delay_bp=12_500,
        description="Reports arrive late and merchants hesitate.",
    ),
    7: OutlookRule(
        7,
        "even_keel",
        "Even Keel",
        10_000,
        10_000,
        10_000,
        description="A steady week without a regional advantage.",
    ),
    8: OutlookRule(
        8,
        "merchant_winds",
        "Merchant Winds",
        10_000,
        11_500,
        9_000,
        voyage_bp=11_000,
        description="Favorable winds carry merchants quickly.",
    ),
    9: OutlookRule(
        9,
        "open_markets",
        "Open Markets",
        10_000,
        12_500,
        9_000,
        spread_bp=8_500,
        description="Competition narrows public-market spreads.",
    ),
    10: OutlookRule(
        10,
        "rumour_tide",
        "Rumour Tide",
        9_500,
        15_000,
        11_000,
        false_rumour_bp=4_000,
        description="Offers and unreliable rumours multiply.",
    ),
    11: OutlookRule(
        11,
        "golden_routes",
        "Golden Routes",
        10_000,
        15_000,
        7_500,
        reward_bp=11_000,
        description="Contracts pay well and routes are safer.",
    ),
    12: OutlookRule(
        12,
        "fortunes_crown",
        "Fortune's Crown",
        10_000,
        20_000,
        5_000,
        description="Rare opportunities and favorable bargains abound.",
    ),
}


def roll_weekly_outlook(
    tick: int,
    settings: SimulationSettings,
    stream: random.Random,
) -> WeeklyOutlook:
    die_one = stream.randint(1, 6)
    die_two = stream.randint(1, 6)
    total = die_one + die_two
    rule = OUTLOOK_RULES[total]
    week_index = tick // settings.week_ticks
    return WeeklyOutlook(
        week_index=week_index,
        start_tick=week_index * settings.week_ticks,
        end_tick=(week_index + 1) * settings.week_ticks,
        die_one=die_one,
        die_two=die_two,
        total=total,
        setting_id=rule.setting_id,
    )


def current_outlook_rule(world: WorldState) -> OutlookRule:
    total = world.weekly_outlook.total if world.weekly_outlook else 7
    return OUTLOOK_RULES[total]


def active_event_multiplier(
    world: WorldState,
    port_id: str,
    commodity_id: str,
    effect: str,
) -> int:
    multiplier = 10_000
    for condition in world.active_conditions:
        if condition.stage != "active":
            continue
        if condition.port_id not in (None, port_id):
            continue
        if condition.commodity_id not in (None, commodity_id):
            continue
        if effect == "production":
            if condition.event_type == "bumper_harvest":
                multiplier = multiplier * (10_000 + condition.severity_bp) // 10_000
            elif condition.event_type in {"crop_blight", "hurricane"}:
                multiplier = multiplier * max(2_000, 10_000 - condition.severity_bp) // 10_000
        elif effect == "consumption":
            if condition.event_type in {"festival", "shipyard_commission", "relief_call"}:
                multiplier = multiplier * (10_000 + condition.severity_bp) // 10_000
    return max(0, multiplier)


def route_speed_multiplier(world: WorldState, route_id: str) -> int:
    multiplier = current_outlook_rule(world).voyage_bp
    route = world.routes[route_id]
    for condition in world.active_conditions:
        if condition.stage != "active" or condition.event_type not in {
            "tropical_storm",
            "hurricane",
        }:
            continue
        affects_route = condition.route_id == route_id
        affects_port = condition.port_id in (route.port_a, route.port_b)
        if affects_route or affects_port:
            storm_speed = max(4_000, 10_000 - condition.severity_bp)
            multiplier = multiplier * storm_speed // 10_000
    return max(1_000, multiplier)


def route_risk_multiplier(world: WorldState, route_id: str) -> int:
    multiplier = current_outlook_rule(world).raid_bp
    route = world.routes[route_id]
    for condition in world.active_conditions:
        if condition.stage == "active" and condition.event_type in {
            "tropical_storm",
            "hurricane",
        }:
            if condition.route_id == route_id or condition.port_id in (route.port_a, route.port_b):
                multiplier = multiplier * 13_000 // 10_000
    return multiplier


def advance_conditions(world: WorldState, emit: EventEmitter) -> None:
    remaining: list[ActiveCondition] = []
    for condition in world.active_conditions:
        if condition.stage == "precursor" and world.tick >= condition.transition_tick:
            condition.stage = "active"
            emit(
                "WorldEventActivated",
                {"event_id": condition.id, "event_type": condition.event_type},
            )
        if condition.stage == "active" and world.tick >= condition.recovery_tick:
            condition.stage = "recovery"
            emit(
                "WorldEventRecovering",
                {"event_id": condition.id, "event_type": condition.event_type},
            )
        if world.tick >= condition.end_tick:
            emit("WorldEventEnded", {"event_id": condition.id, "event_type": condition.event_type})
        else:
            remaining.append(condition)
    world.active_conditions = remaining


def maybe_generate_event(
    world: WorldState,
    settings: SimulationSettings,
    stream: random.Random,
    emit: EventEmitter,
) -> ActiveCondition | None:
    active_major = sum(condition.stage != "recovery" for condition in world.active_conditions)
    if world.tick < world.next_world_event_tick or active_major >= settings.maximum_major_events:
        return None
    event_types = (
        "tropical_storm",
        "hurricane",
        "bumper_harvest",
        "crop_blight",
        "festival",
        "warehouse_fire",
        "shipyard_commission",
    )
    eligible_event_types = [
        event_type
        for event_type in event_types
        if world.event_cooldowns.get(event_type, 0) <= world.tick
    ]
    if not eligible_event_types:
        world.next_world_event_tick += settings.ticks_per_day
        return None
    event_type = stream.choice(eligible_event_types)
    port_id = stream.choice(sorted(world.ports))
    route_id: str | None = None
    commodity_id: str | None = None
    warning_days = 0
    active_days = stream.randint(3, 8)
    recovery_days = stream.randint(2, 5)
    severity_bp = stream.randint(2_500, 6_000)
    if event_type == "tropical_storm":
        route_id = stream.choice(sorted(world.routes))
        port_id = None
        warning_days = stream.randint(1, 2)
        active_days = stream.randint(1, 3)
        severity_bp = stream.randint(2_000, 4_000)
    elif event_type == "hurricane":
        warning_days = stream.randint(2, 3)
        active_days = stream.randint(1, 2)
        recovery_days = stream.randint(7, 14)
        severity_bp = 5_000
    elif event_type in {"bumper_harvest", "crop_blight"}:
        commodity_id = stream.choice(["grain", "sugar", "coffee"])
        active_days = stream.randint(5, 14)
        severity_bp = (
            stream.randint(5_000, 10_000)
            if event_type == "bumper_harvest"
            else stream.randint(5_000, 8_000)
        )
        if (
            event_type == "crop_blight"
            and port_id == "isla_esmeralda"
            and world.story_modifiers.get("fund_treatment")
        ):
            severity_bp = severity_bp * 3 // 4
    elif event_type == "festival":
        commodity_id = stream.choice(["rum", "sugar", "coffee"])
        warning_days = stream.randint(2, 4)
        active_days = stream.randint(2, 4)
        severity_bp = stream.randint(2_500, 7_500)
    elif event_type == "warehouse_fire":
        commodity_id = stream.choice(sorted(world.commodities))
        active_days = stream.randint(3, 7)
    elif event_type == "shipyard_commission":
        commodity_id = stream.choice(["timber", "cloth"])
        active_days = stream.randint(7, 14)
    else:
        commodity_id = stream.choice(["grain", "medicine"])
        active_days = stream.randint(3, 7)

    event_id = f"event_{world.next_event_sequence}_{event_type}"
    transition_tick = world.tick + warning_days * settings.ticks_per_day
    recovery_tick = transition_tick + active_days * settings.ticks_per_day
    end_tick = recovery_tick + recovery_days * settings.ticks_per_day
    condition = ActiveCondition(
        id=event_id,
        event_type=event_type,
        stage="precursor" if warning_days else "active",
        start_tick=world.tick,
        transition_tick=transition_tick,
        recovery_tick=recovery_tick,
        end_tick=end_tick,
        severity_bp=severity_bp,
        port_id=port_id,
        route_id=route_id,
        commodity_id=commodity_id,
        capacity_reduction_bp=2_500 if event_type == "warehouse_fire" else 0,
    )
    world.active_conditions.append(condition)
    world.event_cooldowns[event_type] = end_tick + 3 * settings.ticks_per_day
    gap_days = stream.randint(settings.event_gap_min_days, settings.event_gap_max_days)
    opportunity_weight = current_outlook_rule(world).opportunity_bp
    gap_days = max(2, gap_days * 10_000 // max(1, opportunity_weight))
    world.next_world_event_tick = world.tick + gap_days * settings.ticks_per_day
    emit(
        "WorldEventStarted",
        {
            "event_id": event_id,
            "event_type": event_type,
            "stage": condition.stage,
            "port_id": port_id,
            "route_id": route_id,
            "commodity_id": commodity_id,
            "severity_bp": severity_bp,
            "transition_tick": transition_tick,
            "end_tick": end_tick,
        },
    )
    return condition
