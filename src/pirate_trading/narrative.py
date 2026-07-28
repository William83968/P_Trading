"""Information propagation, opportunities, and introductory port stories."""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable
from typing import Any

from pirate_trading.models import (
    InformationReport,
    MarketReport,
    Opportunity,
    ScheduledReport,
    StoryProgress,
    WorldState,
)
from pirate_trading.settings import SimulationSettings
from pirate_trading.world_events import current_outlook_rule

EventEmitter = Callable[[str, dict[str, Any]], None]

STORY_CHAPTERS = {
    "crowns_haven": (
        "Weather Office",
        "Choose official forecasts or a private commercial rumour network.",
        ["official_station", "private_broker"],
    ),
    "san_cordelia": (
        "The Empty Slipway",
        "Allocate timber to merchant construction or coastal protection.",
        ["merchant_construction", "coastal_protection"],
    ),
    "blackwater_cay": (
        "The Informant",
        "Keep a pirate informant or turn the network over to Crown's Haven.",
        ["retain_informant", "turn_over_network"],
    ),
    "whitecap_reach": (
        "The Broken Sea Wall",
        "Build storm defenses or accept a discounted salt concession.",
        ["build_sea_wall", "salt_concession"],
    ),
    "isla_esmeralda": (
        "The Bitter Leaf",
        "Fund blight treatment or buy distressed coffee.",
        ["fund_treatment", "distressed_coffee"],
    ),
}


def initialize_information(world: WorldState) -> None:
    world.port_market_reports = {port_id: {} for port_id in world.ports}
    world.port_information_reports = {port_id: [] for port_id in world.ports}
    for observer_id in world.ports:
        world.port_market_reports[observer_id][observer_id] = {
            commodity_id: MarketReport(
                source_port_id=observer_id,
                commodity_id=commodity_id,
                created_tick=world.tick,
                quantity=inventory.quantity,
                bid_price=inventory.bid_price,
                ask_price=inventory.ask_price,
            )
            for commodity_id, inventory in world.ports[observer_id].inventories.items()
        }
    player = world.merchants[world.player_id]
    if player.port_id is not None:
        merge_player_knowledge(world, player.port_id)
    world.story_progress = {
        port_id: StoryProgress(port_id=port_id, chapter_id=f"{port_id}_intro")
        for port_id in world.ports
    }


def shortest_route_ticks(world: WorldState, origin_id: str) -> dict[str, int]:
    distances = {origin_id: 0}
    queue = [(0, origin_id)]
    while queue:
        distance, port_id = heapq.heappop(queue)
        if distance != distances[port_id]:
            continue
        for route in world.routes.values():
            if port_id not in (route.port_a, route.port_b):
                continue
            destination = route.other_end(port_id)
            candidate = distance + route.travel_ticks
            if candidate < distances.get(destination, 10**9):
                distances[destination] = candidate
                heapq.heappush(queue, (candidate, destination))
    return distances


def publish_market_reports(world: WorldState) -> None:
    delay_bp = current_outlook_rule(world).report_delay_bp
    for source_id in sorted(world.ports):
        port = world.ports[source_id]
        distances = shortest_route_ticks(world, source_id)
        for commodity_id in sorted(port.inventories):
            inventory = port.inventories[commodity_id]
            report = MarketReport(
                source_port_id=source_id,
                commodity_id=commodity_id,
                created_tick=world.tick + 1,
                quantity=inventory.quantity,
                bid_price=inventory.bid_price,
                ask_price=inventory.ask_price,
            )
            world.port_market_reports[source_id].setdefault(source_id, {})[commodity_id] = report
            for destination_id in sorted(world.ports):
                if destination_id == source_id:
                    continue
                delay = max(1, distances[destination_id] * delay_bp // 10_000)
                world.scheduled_reports.append(
                    ScheduledReport(
                        destination_port_id=destination_id,
                        delivery_tick=world.tick + 1 + delay,
                        report_type="market",
                        payload={
                            "source_port_id": source_id,
                            "commodity_id": commodity_id,
                            "created_tick": report.created_tick,
                            "quantity": report.quantity,
                            "bid_price": report.bid_price,
                            "ask_price": report.ask_price,
                        },
                    )
                )


def publish_event_report(
    world: WorldState,
    source_port_id: str,
    headline: str,
    body: str,
    information_kind: str,
    event_id: str | None = None,
    route_id: str | None = None,
    commodity_id: str | None = None,
    is_true: bool = True,
    target_port_id: str | None = None,
    public_effect_summary: str = "",
    deadline_tick: int | None = None,
    priority: str = "normal",
    opportunity_ids: tuple[str, ...] = (),
) -> InformationReport:
    report_id = f"report_{world.next_event_sequence}_{source_port_id}"
    report = InformationReport(
        id=report_id,
        created_tick=world.tick,
        source_port_id=source_port_id,
        information_kind=information_kind,
        headline=headline,
        body=body,
        sequence=world.next_event_sequence,
        event_id=event_id,
        port_id=target_port_id or source_port_id,
        route_id=route_id,
        commodity_id=commodity_id,
        is_true=is_true,
        public_effect_summary=public_effect_summary,
        deadline_tick=deadline_tick,
        priority=priority,
        opportunity_ids=opportunity_ids,
    )
    world.port_information_reports[source_port_id].append(report)
    distances = shortest_route_ticks(world, source_port_id)
    delay_bp = current_outlook_rule(world).report_delay_bp
    for destination_id in sorted(world.ports):
        if destination_id == source_port_id:
            continue
        delay = max(1, distances[destination_id] * delay_bp // 10_000)
        world.scheduled_reports.append(
            ScheduledReport(
                destination_port_id=destination_id,
                delivery_tick=world.tick + delay,
                report_type="information",
                payload={
                    "id": report.id,
                    "created_tick": report.created_tick,
                    "source_port_id": report.source_port_id,
                    "information_kind": report.information_kind,
                    "headline": report.headline,
                    "body": report.body,
                    "sequence": report.sequence,
                    "event_id": report.event_id,
                    "port_id": report.port_id,
                    "route_id": report.route_id,
                    "commodity_id": report.commodity_id,
                    "is_true": report.is_true,
                    "public_effect_summary": report.public_effect_summary,
                    "deadline_tick": report.deadline_tick,
                    "priority": report.priority,
                    "opportunity_ids": report.opportunity_ids,
                },
            )
        )
    return report


def deliver_reports(world: WorldState, emit: EventEmitter) -> None:
    remaining: list[ScheduledReport] = []
    delivered_ports: set[str] = set()
    for scheduled in world.scheduled_reports:
        if scheduled.delivery_tick > world.tick:
            remaining.append(scheduled)
            continue
        destination = scheduled.destination_port_id
        delivered_ports.add(destination)
        if scheduled.report_type == "market":
            report = MarketReport(**scheduled.payload)
            world.port_market_reports[destination].setdefault(report.source_port_id, {})[
                report.commodity_id
            ] = report
        else:
            report = InformationReport(**scheduled.payload)
            reports = world.port_information_reports[destination]
            if not any(existing.id == report.id for existing in reports):
                reports.append(report)
                del reports[:-200]
        emit(
            "ReportDelivered",
            {
                "destination_port_id": destination,
                "report_type": scheduled.report_type,
                "report_id": (
                    scheduled.payload.get("id") if scheduled.report_type == "information" else None
                ),
                "headline": (
                    scheduled.payload.get("headline")
                    if scheduled.report_type == "information"
                    else None
                ),
                "information_kind": (
                    scheduled.payload.get("information_kind")
                    if scheduled.report_type == "information"
                    else None
                ),
                "event_id": (
                    scheduled.payload.get("event_id")
                    if scheduled.report_type == "information"
                    else None
                ),
                "urgent": scheduled.payload.get("priority") == "urgent",
                "created_tick": scheduled.payload.get("created_tick"),
                "port_id": scheduled.payload.get("port_id"),
                "route_id": scheduled.payload.get("route_id"),
                "commodity_id": scheduled.payload.get("commodity_id"),
            },
        )
    world.scheduled_reports = remaining
    player = world.merchants[world.player_id]
    if player.port_id in delivered_ports:
        merge_player_knowledge(world, player.port_id)


def merge_player_knowledge(
    world: WorldState,
    port_id: str,
) -> tuple[InformationReport, ...]:
    for source_id, commodities in world.port_market_reports[port_id].items():
        player_source = world.player_market_reports.setdefault(source_id, {})
        for commodity_id, report in commodities.items():
            previous = player_source.get(commodity_id)
            if previous is None or report.created_tick >= previous.created_tick:
                player_source[commodity_id] = report
    known_ids = {report.id for report in world.player_information_reports}
    newly_learned: list[InformationReport] = []
    for report in world.port_information_reports[port_id]:
        if report.id not in known_ids:
            world.player_information_reports.append(report)
            newly_learned.append(report)
            known_ids.add(report.id)
            for opportunity_id in report.opportunity_ids:
                opportunity = world.opportunities.get(opportunity_id)
                if opportunity is not None:
                    opportunity.known_to_player = True
    del world.player_information_reports[:-200]
    return tuple(newly_learned)


def maybe_generate_situation(
    world: WorldState,
    settings: SimulationSettings,
    stream: random.Random,
    emit: EventEmitter,
) -> Opportunity | None:
    """Create one supportable situation from current world state."""
    if world.tick < world.next_situation_tick:
        return None
    world.next_situation_tick = world.tick + stream.randint(
        settings.situation_gap_min_ticks, settings.situation_gap_max_ticks
    )
    active = [
        item
        for item in world.opportunities.values()
        if item.kind == "situation" and item.status in {"offered", "accepted"}
    ]
    if len(active) >= settings.maximum_active_situations:
        return None

    candidates: list[tuple[str, str, str | None, str | None, str | None]] = []
    for condition in sorted(world.active_conditions, key=lambda item: item.id):
        if condition.event_type in {"tropical_storm", "hurricane"}:
            kind = "reconnaissance" if condition.stage == "precursor" else "evacuation"
            candidates.append(
                (
                    kind,
                    condition.port_id or "",
                    condition.route_id,
                    condition.commodity_id,
                    condition.id,
                )
            )
            if condition.route_id:
                candidates.append(
                    (
                        "protection",
                        condition.port_id or "",
                        condition.route_id,
                        condition.commodity_id,
                        condition.id,
                    )
                )
        elif condition.event_type == "warehouse_fire":
            commodity_id = condition.commodity_id
            recoverable = world.ledger.event_losses.get(
                commodity_id or "", 0
            ) - world.ledger.salvage_recovered.get(commodity_id or "", 0)
            if commodity_id is not None and recoverable > 0:
                candidates.append(
                    ("salvage", condition.port_id or "", None, commodity_id, condition.id)
                )
        elif condition.event_type == "festival":
            if condition.commodity_id is None and condition.port_id:
                condition.commodity_id = min(
                    ("coffee", "rum", "sugar"),
                    key=lambda commodity_id: (
                        world.ports[condition.port_id].inventories[commodity_id].quantity
                        * 10_000
                        // world.ports[condition.port_id].inventories[commodity_id].target,
                        commodity_id,
                    ),
                )
            candidates.append(
                (
                    "festival_supply",
                    condition.port_id or "",
                    None,
                    condition.commodity_id,
                    condition.id,
                )
            )
        elif condition.event_type == "bumper_harvest":
            candidates.append(
                (
                    "harvest_export",
                    condition.port_id or "",
                    None,
                    condition.commodity_id,
                    condition.id,
                )
            )
        elif condition.event_type == "shipyard_commission":
            candidates.append(
                ("delivery", condition.port_id or "", None, condition.commodity_id, condition.id)
            )
    if world.merchants[world.player_id].port_id != "blackwater_cay" and (
        world.piracy_state.notoriety >= 15
        or any(world.merchants[world.player_id].stolen_cargo.values())
    ):
        candidates.append(("smuggling", "blackwater_cay", None, None, None))
    for port_id, port in sorted(world.ports.items()):
        for commodity_id, inventory in sorted(port.inventories.items()):
            cooldown_key = f"delivery_situation:{port_id}:{commodity_id}"
            has_open_delivery = any(
                item.status in {"offered", "accepted"}
                and item.objective_type
                in {"delivery", "festival_supply", "harvest_export", "private_delivery"}
                and item.destination_port_id == port_id
                and item.commodity_id == commodity_id
                for item in world.opportunities.values()
            )
            if (
                inventory.quantity * 10_000 < inventory.target * 7_500
                and world.tick >= world.event_cooldowns.get(cooldown_key, 0)
                and not has_open_delivery
            ):
                candidates.append(("delivery", port_id, None, commodity_id, None))
    for route in sorted(world.routes.values(), key=lambda item: item.id):
        endpoint_heat = max(
            world.piracy_state.local_heat.get(route.port_a, 0),
            world.piracy_state.local_heat.get(route.port_b, 0),
        )
        if endpoint_heat >= 25:
            candidates.append(("protection", route.port_a, route.id, None, None))
    if not candidates:
        return None

    objective, origin, route_id, commodity_id, event_id = stream.choice(candidates)
    if not origin:
        if route_id:
            origin = world.routes[route_id].port_a
        else:
            return None
    destination = origin
    if objective == "reconnaissance" and route_id is None:
        connected = sorted(
            (route for route in world.routes.values() if origin in (route.port_a, route.port_b)),
            key=lambda route: (route.travel_ticks, route.id),
        )
        if not connected:
            return None
        route_id = connected[0].id
    if objective in {"reconnaissance", "protection"} and route_id:
        destination = world.routes[route_id].other_end(origin)
    elif objective == "smuggling":
        destination = "blackwater_cay"
        origin = world.merchants[world.player_id].port_id or "crowns_haven"
    elif objective == "evacuation":
        routes = sorted(
            (route for route in world.routes.values() if origin in (route.port_a, route.port_b)),
            key=lambda item: (item.travel_ticks, item.id),
        )
        if routes:
            route_id = routes[0].id
            destination = routes[0].other_end(origin)
        else:
            return None
    elif objective == "harvest_export":
        if commodity_id is None:
            return None
        destinations = [
            (route, route.other_end(origin))
            for route in world.routes.values()
            if origin in (route.port_a, route.port_b)
        ]
        if not destinations:
            return None
        route, destination = min(
            destinations,
            key=lambda pair: (
                world.ports[pair[1]].inventories[commodity_id].quantity
                * 10_000
                // world.ports[pair[1]].inventories[commodity_id].target,
                pair[1],
                pair[0].id,
            ),
        )
        route_id = route.id
    required = (
        5
        if objective in {"delivery", "festival_supply", "harvest_export", "private_delivery"}
        else 0
    )
    reserved = 5 if objective in {"evacuation", "smuggling"} else 0
    opportunity_id = f"situation_{world.next_event_sequence}_{objective}"
    labels = {
        "delivery": "Urgent Delivery",
        "reconnaissance": "Weather Reconnaissance",
        "evacuation": "Harbor Evacuation",
        "salvage": "Warehouse Salvage",
        "protection": "Route Protection",
        "smuggling": "Blackwater Passage",
        "festival_supply": "Festival Supply",
        "harvest_export": "Harvest Export",
    }
    commodity_name = world.commodities[commodity_id].name if commodity_id else ""
    description = {
        "festival_supply": (
            f"Supply 5 clean {commodity_name} for the festival at {world.ports[origin].name}."
        ),
        "harvest_export": (
            f"Carry 5 clean {commodity_name} from {world.ports[origin].name} "
            f"to {world.ports[destination].name}."
        ),
        "salvage": (
            f"Recover up to 5 {commodity_name} from the warehouse losses "
            f"at {world.ports[origin].name}."
        ),
    }.get(
        objective,
        f"{labels[objective]} arising from current conditions at {world.ports[origin].name}.",
    )
    effect_summary = f"Complete a real {objective.replace('_', ' ')} objective before the deadline."
    opportunity = Opportunity(
        id=opportunity_id,
        kind="situation",
        title=labels[objective],
        description=description,
        port_id=origin,
        created_tick=world.tick,
        expiry_tick=world.tick + 5 * settings.ticks_per_day,
        event_id=event_id,
        commodity_id=commodity_id,
        required_quantity=required,
        reward=4_000 + stream.randint(0, 4_000),
        options=["accept", "decline"],
        objective_type=objective,
        origin_port_id=origin,
        destination_port_id=destination,
        route_id=route_id,
        reserved_capacity=reserved,
        public_effect_summary=effect_summary,
        known_to_player=world.merchants[world.player_id].port_id == origin,
    )
    world.opportunities[opportunity_id] = opportunity
    if objective == "delivery" and commodity_id is not None:
        cooldown_key = f"delivery_situation:{destination}:{commodity_id}"
        world.event_cooldowns[cooldown_key] = (
            world.tick + settings.delivery_situation_cooldown_ticks
        )
    report = publish_event_report(
        world,
        origin,
        opportunity.title,
        opportunity.description,
        "opportunity",
        event_id=event_id,
        route_id=route_id,
        commodity_id=commodity_id,
        target_port_id=origin,
        public_effect_summary=opportunity.public_effect_summary,
        deadline_tick=opportunity.expiry_tick,
        priority="urgent",
        opportunity_ids=(opportunity.id,),
    )
    opportunity.related_report_id = report.id
    if opportunity.known_to_player:
        merge_player_knowledge(world, origin)
        emit(
            "InformationReportReceived",
            {
                "destination_port_id": origin,
                "report_id": report.id,
                "headline": report.headline,
                "information_kind": report.information_kind,
                "event_id": report.event_id,
                "urgent": True,
                "created_tick": report.created_tick,
                "port_id": report.port_id,
                "route_id": report.route_id,
                "commodity_id": report.commodity_id,
            },
        )
    world.situation_history.append(opportunity.id)
    emit(
        "OpportunityCreated",
        {
            "opportunity_id": opportunity.id,
            "port_id": origin,
            "objective_type": objective,
            "urgent": True,
        },
    )
    return opportunity


def maybe_offer_intro_story(
    world: WorldState,
    port_id: str,
    settings: SimulationSettings,
    emit: EventEmitter,
) -> Opportunity | None:
    progress = world.story_progress[port_id]
    if world.tick < 3 * settings.ticks_per_day or progress.status != "unseen":
        return None
    title, description, options = STORY_CHAPTERS[port_id]
    opportunity_id = f"story_{port_id}"
    opportunity = Opportunity(
        id=opportunity_id,
        kind="story",
        title=title,
        description=description,
        port_id=port_id,
        created_tick=world.tick,
        expiry_tick=world.tick + 30 * settings.ticks_per_day,
        options=list(options),
    )
    world.opportunities[opportunity_id] = opportunity
    progress.status = "offered"
    emit("OpportunityCreated", {"opportunity_id": opportunity_id, "port_id": port_id})
    return opportunity


def create_event_opportunity(
    world: WorldState,
    event_id: str,
    event_type: str,
    port_id: str,
    commodity_id: str | None,
    settings: SimulationSettings,
    emit: EventEmitter,
) -> Opportunity | None:
    if event_type not in {"shipyard_commission", "relief_call"}:
        return None
    commodity = commodity_id or ("timber" if event_type == "shipyard_commission" else "medicine")
    opportunity_id = f"contract_{event_id}"
    reward = world.commodities[commodity].base_price * 12
    reward = reward * current_outlook_rule(world).reward_bp // 10_000
    opportunity = Opportunity(
        id=opportunity_id,
        kind="contract",
        title="Shipyard Delivery" if event_type == "shipyard_commission" else "Relief Delivery",
        description=(
            f"Deliver 10 {world.commodities[commodity].name} to {world.ports[port_id].name}."
        ),
        port_id=port_id,
        created_tick=world.tick,
        expiry_tick=world.tick + 7 * settings.ticks_per_day,
        event_id=event_id,
        commodity_id=commodity,
        required_quantity=10,
        reward=reward,
        options=["accept", "decline"],
        objective_type="delivery",
        origin_port_id=port_id,
        destination_port_id=port_id,
        public_effect_summary="Deliver real cargo before the deadline.",
        known_to_player=world.merchants[world.player_id].port_id == port_id,
    )
    world.opportunities[opportunity_id] = opportunity
    emit("OpportunityCreated", {"opportunity_id": opportunity_id, "port_id": port_id})
    return opportunity
