"""Pygame application shell for the playable trading game."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import pygame

from pirate_trading.models import (
    AbandonRaidLoot,
    AcknowledgeWeeklyOutlook,
    BeginVoyage,
    BuyCargo,
    BuyProvisions,
    ClaimRaidLoot,
    FenceStolenCargo,
    Inventory,
    MarketReport,
    MarketSample,
    Merchant,
    PayOffOfficials,
    PayWages,
    PurchaseUpgrade,
    RaidPort,
    RaidShip,
    RepairShip,
    ResolveEncounter,
    RespondToOpportunity,
    SellCargo,
    WorldState,
)
from pirate_trading.persistence import SaveError, load_game, save_game
from pirate_trading.settings import SETTINGS, SimulationSettings
from pirate_trading.simulation import SimulationEngine, load_world
from pirate_trading.ui.camera import Camera
from pirate_trading.ui.layout import UILayout, message_paper_content_rect
from pirate_trading.ui.overlays import draw_focused_overlay, projected_raid_damage
from pirate_trading.ui.panels import cargo_provenance, draw_context_panel
from pirate_trading.world_events import current_outlook_rule

BACKGROUND = (31, 24, 20)
PANEL = (47, 35, 28)
PANEL_LIGHT = (74, 55, 40)
INK = (38, 28, 22)
TEXT = (245, 230, 193)
MUTED = (185, 166, 134)
ACCENT = (212, 165, 75)
GOOD = (105, 176, 105)
BAD = (204, 91, 79)
SELECTED = (112, 82, 51)
ROUTE = (116, 70, 43)


class AssetError(RuntimeError):
    """Raised when a required visual asset cannot be loaded."""


class _AcknowledgementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[str] = []
        self._inside_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._inside_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._inside_link = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if self._inside_link and text:
            self.entries.append(text)


@dataclass(slots=True)
class TimeController:
    rates: tuple[int, ...]
    rate_index: int = 0
    paused: bool = True
    accumulator: float = 0.0

    @property
    def rate(self) -> int:
        return self.rates[self.rate_index]

    def slower(self) -> None:
        self.rate_index = max(0, self.rate_index - 1)

    def faster(self) -> None:
        self.rate_index = min(len(self.rates) - 1, self.rate_index + 1)

    def due_ticks(self, elapsed_seconds: float, maximum: int) -> int:
        if self.paused:
            return 0
        self.accumulator += elapsed_seconds * self.rate
        ticks = min(int(self.accumulator), maximum)
        self.accumulator -= ticks
        if ticks == maximum:
            self.accumulator = min(self.accumulator, 1.0)
        return ticks


@dataclass(frozen=True, slots=True)
class Button:
    name: str
    rect: pygame.Rect
    label: str = ""
    enabled: bool = True
    icon: str | None = None


SidebarTab = Literal["market", "hold", "port", "ship", "reputation"]


@dataclass(frozen=True, slots=True)
class CargoHoldingView:
    """Presentation-only valuation of one commodity in the player's hold."""

    commodity_id: str
    name: str
    quantity: int
    clean_quantity: int
    stolen_quantity: int
    cost_basis: int
    average_unit_cost: float
    bid_price: int
    estimated_value: int
    estimated_profit: int
    sellable_quantity: int
    information_kind: str = "FACT"
    quote_age_hours: int = 0
    quote_available: bool = True


@dataclass(frozen=True, slots=True)
class NewsItem:
    """A notable, optionally grouped, player-facing domain-event summary."""

    latest_sequence: int
    tick: int
    priority: int
    category: str
    text: str
    count: int = 1
    information_kind: str = "FACT"
    port_id: str | None = None
    commodity_id: str | None = None


@dataclass(frozen=True, slots=True)
class MissionProgressStep:
    label: str
    status: Literal["complete", "active", "pending", "blocked"]


@dataclass(frozen=True, slots=True)
class MissionProgressView:
    objective_label: str
    payload_label: str
    origin_name: str
    destination_name: str
    route_label: str
    steps: tuple[MissionProgressStep, ...]
    action_hint: str
    disabled_reason: str
    cargo_current: int
    cargo_required: int
    mission_load_current: int
    mission_load_required: int
    route_progress_percent: int | None
    deadline_hours: int
    reward: int
    penalty: int
    can_complete: bool


def market_status(inventory: Inventory, settings: SimulationSettings = SETTINGS) -> str:
    ratio = inventory.quantity * settings.basis_point_scale // inventory.target
    if ratio < settings.shortage_threshold_bp:
        return "Shortage"
    if ratio > settings.surplus_threshold_bp:
        return "Surplus"
    return "Balanced"


def price_trend(samples: list[MarketSample]) -> str:
    if len(samples) < 2 or samples[-1].ask_price == samples[-2].ask_price:
        return "—"
    return "↑" if samples[-1].ask_price > samples[-2].ask_price else "↓"


def game_clock_label(tick: int, ticks_per_day: int) -> str:
    """Format the deterministic hourly simulation clock for the status bar."""

    day = tick // ticks_per_day + 1
    hour = tick % ticks_per_day
    return f"Day {day}, {hour:02d}:00"


def maximum_buy_quantity(player: Merchant, inventory: Inventory) -> int:
    if inventory.ask_price <= 0:
        return 0
    return max(
        0,
        min(
            player.capacity - player.cargo_quantity,
            inventory.quantity,
            player.cash // inventory.ask_price,
        ),
    )


def sell_limit(
    player: Merchant, commodity_id: str, port_treasury: int, inventory: Inventory
) -> int:
    if inventory.bid_price <= 0:
        return 0
    return max(
        0,
        min(
            player.cargo.get(commodity_id, 0) - player.stolen_cargo.get(commodity_id, 0),
            inventory.capacity - inventory.quantity,
            port_treasury // inventory.bid_price,
        ),
    )


def known_market_report(
    world: WorldState,
    port_id: str,
    commodity_id: str,
) -> MarketReport | None:
    """Return live local data or the player's newest delivered remote report."""
    player = world.merchants[world.player_id]
    if player.port_id == port_id and player.voyage is None:
        inventory = world.ports[port_id].inventories[commodity_id]
        return MarketReport(
            port_id,
            commodity_id,
            world.tick,
            inventory.quantity,
            inventory.bid_price,
            inventory.ask_price,
        )
    return world.player_market_reports.get(port_id, {}).get(commodity_id)


def build_cargo_holdings(
    world: WorldState,
    player_id: str,
    selected_port_id: str,
) -> tuple[CargoHoldingView, ...]:
    """Derive stable cargo valuations without changing simulation state."""
    player = world.merchants[player_id]
    port = world.ports[selected_port_id]
    holdings: list[CargoHoldingView] = []
    for commodity_id in sorted(player.cargo):
        quantity = player.cargo[commodity_id]
        if quantity <= 0:
            continue
        cost_basis = player.cargo_cost_basis.get(commodity_id, 0)
        provenance = cargo_provenance(player, commodity_id)
        inventory = port.inventories[commodity_id]
        report = known_market_report(world, selected_port_id, commodity_id)
        bid_price = report.bid_price if report else 0
        estimated_value = quantity * bid_price
        holdings.append(
            CargoHoldingView(
                commodity_id=commodity_id,
                name=world.commodities[commodity_id].name,
                quantity=quantity,
                clean_quantity=provenance.clean,
                stolen_quantity=provenance.stolen,
                cost_basis=cost_basis,
                average_unit_cost=cost_basis / quantity,
                bid_price=bid_price,
                estimated_value=estimated_value,
                estimated_profit=estimated_value - cost_basis,
                sellable_quantity=(
                    sell_limit(player, commodity_id, port.treasury, inventory)
                    if player.port_id == selected_port_id and player.voyage is None
                    else 0
                ),
                information_kind="FACT" if player.port_id == selected_port_id else "ESTIMATE",
                quote_age_hours=max(0, world.tick - report.created_tick) if report else 0,
                quote_available=report is not None,
            )
        )
    return tuple(holdings)


def build_mission_progress(
    world: WorldState,
    player_id: str,
    opportunity: Any,
    settings: SimulationSettings = SETTINGS,
) -> MissionProgressView:
    """Derive one objective-specific mission checklist without mutating state."""

    player = world.merchants[player_id]
    objective = opportunity.objective_type
    objective_labels = {
        "delivery": "Delivery",
        "festival_supply": "Festival Supply",
        "harvest_export": "Harvest Export",
        "private_delivery": "Private Delivery",
        "reconnaissance": "Weather Reconnaissance",
        "evacuation": "Harbor Evacuation",
        "salvage": "Warehouse Salvage",
        "protection": "Route Protection",
        "smuggling": "Blackwater Passage",
    }
    origin_id = opportunity.origin_port_id or opportunity.port_id
    destination_id = opportunity.destination_port_id or opportunity.port_id
    origin_name = world.ports[origin_id].name
    destination_name = world.ports[destination_id].name
    commodity = (
        world.commodities.get(opportunity.commodity_id)
        if opportunity.commodity_id is not None
        else None
    )
    payload_label = commodity.name if commodity is not None else ""
    held = player.cargo.get(opportunity.commodity_id or "", 0)
    clean = held - player.stolen_cargo.get(opportunity.commodity_id or "", 0)
    mission_load = player.mission_load.get(opportunity.id, 0)
    deadline = max(0, opportunity.expiry_tick - world.tick)
    penalty = max(
        settings.mission_failure_minimum,
        opportunity.reward * settings.mission_failure_penalty_bp // 10_000,
    )
    route = world.routes.get(opportunity.route_id or "")
    route_label = f"{origin_name} → {destination_name}" if route is not None else ""

    voyage_percent: int | None = None
    if player.voyage is not None:
        voyage_route = world.routes[player.voyage.route_id]
        completed_bp = (
            voyage_route.travel_ticks - player.voyage.remaining_ticks
        ) * 10_000 + player.voyage.progress_remainder_bp
        voyage_percent = max(
            0,
            min(99, completed_bp * 100 // max(1, voyage_route.travel_ticks * 10_000)),
        )

    steps: list[MissionProgressStep] = [
        MissionProgressStep("Mission accepted", "complete"),
    ]
    action_hint = ""
    disabled_reason = ""
    can_complete = False
    route_progress: int | None = None
    cargo_objectives = {
        "delivery",
        "festival_supply",
        "harvest_export",
        "private_delivery",
    }
    exact_route_objectives = {"reconnaissance", "evacuation", "protection"}

    if opportunity.status == "resolved":
        steps.append(MissionProgressStep("Objective completed", "complete"))
        action_hint = "This objective has already been completed."
        disabled_reason = "objective already completed"
    elif objective in cargo_objectives:
        cargo_ready = (
            opportunity.commodity_id is not None and clean >= opportunity.required_quantity
        )
        cargo_detail = (
            f"Clean {payload_label}: {clean}/{opportunity.required_quantity}"
            if payload_label
            else f"Required cargo: {clean}/{opportunity.required_quantity}"
        )
        steps.append(
            MissionProgressStep(
                cargo_detail,
                "complete" if cargo_ready else "active",
            )
        )
        at_destination = player.port_id == destination_id and player.voyage is None
        if at_destination:
            destination_status = "complete"
            destination_detail = f"At destination: {destination_name}"
        elif player.voyage is not None and player.voyage.destination_id == destination_id:
            destination_status = "active"
            route_progress = voyage_percent
            destination_detail = f"En route to {destination_name}: {voyage_percent or 0}%"
        else:
            destination_status = "pending"
            destination_detail = f"Reach destination: {destination_name}"
        steps.append(MissionProgressStep(destination_detail, destination_status))
        can_complete = cargo_ready and at_destination
        if can_complete:
            action_hint = "Ready to hand over the contracted cargo."
        elif not cargo_ready:
            action_hint = (
                f"Acquire {max(0, opportunity.required_quantity - clean)} more clean "
                f"{payload_label or 'cargo'}."
            )
            disabled_reason = "insufficient clean cargo"
        else:
            action_hint = f"Travel to {destination_name}."
            disabled_reason = "wrong port"
    elif objective in exact_route_objectives:
        if objective == "evacuation":
            payload_label = "Passengers"
            steps.append(
                MissionProgressStep(
                    f"Reserved hold space: {opportunity.reserved_capacity}",
                    "complete",
                )
            )
            load_label = f"Passengers aboard: {mission_load}/{opportunity.reserved_capacity}"
            steps.append(
                MissionProgressStep(
                    load_label,
                    ("complete" if mission_load == opportunity.reserved_capacity else "blocked"),
                )
            )
        legacy_round_trip = (
            objective == "reconnaissance" and route is None and origin_id == destination_id
        )
        on_required_voyage = player.voyage is not None and (
            legacy_round_trip
            or (
                player.voyage.route_id == opportunity.route_id
                and player.voyage.destination_id == destination_id
            )
        )
        if on_required_voyage:
            route_progress = voyage_percent
            route_step = MissionProgressStep(
                (
                    f"Survey voyage in progress: {voyage_percent or 0}%"
                    if legacy_round_trip
                    else f"Sailing {route_label}: {voyage_percent or 0}%"
                ),
                "active",
            )
            action_hint = (
                f"Complete this leg, then return to {origin_name}."
                if legacy_round_trip
                else f"Continue to {destination_name}."
            )
        elif player.voyage is not None:
            route_step = MissionProgressStep(
                f"Off course; required route is {route_label}",
                "blocked",
            )
            action_hint = f"Return to {origin_name} and take the assigned route."
            disabled_reason = "wrong route"
        elif player.port_id == origin_id:
            route_step = MissionProgressStep(
                (
                    "Depart on any route, then return with the survey"
                    if legacy_round_trip
                    else f"Sail assigned route: {route_label}"
                ),
                "active",
            )
            action_hint = (
                f"Depart from {origin_name}, then return."
                if legacy_round_trip
                else f"Sail to {destination_name} on the assigned route."
            )
        else:
            route_step = MissionProgressStep(
                f"Return to mission origin: {origin_name}",
                "pending",
            )
            action_hint = f"Return to {origin_name}."
            disabled_reason = "wrong port"
        steps.append(route_step)
    elif objective == "smuggling":
        payload_label = "Contraband crates"
        steps.append(
            MissionProgressStep(
                f"Contraband crates aboard: {mission_load}/{opportunity.reserved_capacity}",
                ("complete" if mission_load == opportunity.reserved_capacity else "blocked"),
            )
        )
        if player.voyage is not None:
            route_progress = voyage_percent
            voyage_destination = world.ports[player.voyage.destination_id].name
            steps.append(
                MissionProgressStep(
                    f"Current leg to {voyage_destination}: {voyage_percent or 0}%",
                    "active",
                )
            )
            action_hint = f"Continue toward {destination_name} by any route."
        else:
            steps.append(
                MissionProgressStep(
                    f"Reach destination: {destination_name}",
                    "active" if player.port_id != destination_id else "complete",
                )
            )
            action_hint = f"Travel to {destination_name} by any route."
    elif objective == "salvage":
        payload_label = payload_label or "Cargo"
        recoverable = world.ledger.event_losses.get(
            opportunity.commodity_id or "", 0
        ) - world.ledger.salvage_recovered.get(opportunity.commodity_id or "", 0)
        at_site = player.port_id == destination_id and player.voyage is None
        steps.append(
            MissionProgressStep(
                (
                    f"At salvage site: {destination_name}"
                    if at_site
                    else f"Travel to salvage site: {destination_name}"
                ),
                "complete" if at_site else "pending",
            )
        )
        steps.append(
            MissionProgressStep(
                f"Recoverable {payload_label}: {max(0, min(5, recoverable))}",
                "active" if recoverable > 0 else "blocked",
            )
        )
        free_capacity = player.capacity - player.cargo_quantity
        can_complete = at_site and recoverable > 0 and free_capacity > 0
        if can_complete:
            action_hint = "Ready to recover the declared warehouse loss."
        elif not at_site:
            action_hint = f"Travel to {destination_name}."
            disabled_reason = "wrong port"
        elif recoverable <= 0:
            action_hint = "No declared event loss remains to recover."
            disabled_reason = "no recoverable loss"
        else:
            action_hint = "Free hold capacity before recovering salvage."
            disabled_reason = "insufficient hold capacity"

    return MissionProgressView(
        objective_label=objective_labels.get(objective, objective.replace("_", " ").title()),
        payload_label=payload_label,
        origin_name=origin_name,
        destination_name=destination_name,
        route_label=route_label,
        steps=tuple(steps),
        action_hint=action_hint,
        disabled_reason=disabled_reason,
        cargo_current=clean,
        cargo_required=opportunity.required_quantity,
        mission_load_current=mission_load,
        mission_load_required=opportunity.reserved_capacity,
        route_progress_percent=route_progress,
        deadline_hours=deadline,
        reward=opportunity.reward,
        penalty=penalty,
        can_complete=can_complete,
    )


def build_mission_history(
    world: WorldState,
    settings: SimulationSettings = SETTINGS,
) -> tuple[str, ...]:
    """Return newest-first summaries for accepted missions with recorded outcomes."""

    entries: list[tuple[int, str]] = []
    cargo_objectives = {
        "delivery",
        "festival_supply",
        "harvest_export",
        "private_delivery",
    }
    for opportunity in world.opportunities.values():
        if opportunity.accepted_tick is None or opportunity.resolved_tick is None:
            continue
        origin_id = opportunity.origin_port_id or opportunity.port_id
        destination_id = opportunity.destination_port_id or opportunity.port_id
        origin = world.ports[origin_id].name
        destination = world.ports[destination_id].name
        commodity = (
            world.commodities[opportunity.commodity_id].name
            if opportunity.commodity_id in world.commodities
            else "cargo"
        )
        if opportunity.objective_type in cargo_objectives:
            objective = f"{opportunity.required_quantity} {commodity} · {origin} → {destination}"
        elif opportunity.objective_type == "evacuation":
            objective = f"{opportunity.reserved_capacity} passengers · {origin} → {destination}"
        elif opportunity.objective_type == "smuggling":
            objective = (
                f"{opportunity.reserved_capacity} contraband crates · {origin} → {destination}"
            )
        elif opportunity.objective_type == "salvage":
            objective = f"{opportunity.progress} {commodity} recovered at {destination}"
        else:
            objective = f"{origin} → {destination}"
        if opportunity.status == "resolved":
            outcome = f"COMPLETED · reward ${opportunity.reward / 100:.2f}"
        else:
            reason = (opportunity.failure_reason or opportunity.status).upper()
            outcome = f"{reason} · penalty ${opportunity.penalty_paid / 100:.2f}"
        accepted = game_clock_label(opportunity.accepted_tick, settings.ticks_per_day)
        resolved = game_clock_label(opportunity.resolved_tick, settings.ticks_per_day)
        entries.append(
            (
                opportunity.resolved_tick,
                f"{opportunity.title} · {objective} · accepted {accepted} · "
                f"resolved {resolved} · {outcome}",
            )
        )
    return tuple(text for _, text in sorted(entries, key=lambda item: (-item[0], item[1])))


def build_news_items(
    world: WorldState,
    settings: SimulationSettings = SETTINGS,
) -> tuple[NewsItem, ...]:
    """Turn low-level events into prioritized, grouped readable news."""
    grouped: dict[tuple[int, str, str | None, str | None], NewsItem] = {}
    individual: list[NewsItem] = []
    for event in world.recent_events:
        payload = event.payload
        item: NewsItem | None = None
        if event.event_type == "CargoPurchased" and payload.get("merchant_id") == world.player_id:
            commodity_id = payload["commodity_id"]
            item = NewsItem(
                event.sequence,
                event.tick,
                3,
                "PLAYER",
                f"You bought {payload['quantity']} {world.commodities[commodity_id].name}.",
                port_id=payload["port_id"],
                commodity_id=commodity_id,
            )
        elif event.event_type == "CargoSold" and payload.get("merchant_id") == world.player_id:
            commodity_id = payload["commodity_id"]
            item = NewsItem(
                event.sequence,
                event.tick,
                3,
                "PLAYER",
                f"You sold {payload['quantity']} {world.commodities[commodity_id].name}.",
                port_id=payload["port_id"],
                commodity_id=commodity_id,
            )
        elif event.event_type == "ShipArrived" and payload.get("merchant_id") == world.player_id:
            port_id = payload["destination_id"]
            item = NewsItem(
                event.sequence,
                event.tick,
                3,
                "PLAYER",
                f"You arrived at {world.ports[port_id].name}.",
                port_id=port_id,
            )
        elif event.event_type == "DailyEconomyApplied" and payload.get("unmet_demand", 0) > 0:
            commodity_id = payload["commodity_id"]
            port_id = payload["port_id"]
            item = NewsItem(
                event.sequence,
                event.tick,
                2,
                "SHORTAGE",
                f"{world.ports[port_id].name} could not meet demand for "
                f"{world.commodities[commodity_id].name}.",
                port_id=port_id,
                commodity_id=commodity_id,
            )
        elif event.event_type == "PriceChanged":
            commodity_id = payload["commodity_id"]
            port_id = payload["port_id"]
            item = NewsItem(
                event.sequence,
                event.tick,
                1,
                "MARKET",
                f"{world.commodities[commodity_id].name} prices changed at "
                f"{world.ports[port_id].name}.",
                port_id=port_id,
                commodity_id=commodity_id,
            )
        elif event.event_type in {
            "WorldEventStarted",
            "WorldEventActivated",
            "WorldEventEnded",
        }:
            event_type = payload.get("event_type", "world_event")
            port_id = payload.get("port_id")
            item = NewsItem(
                event.sequence,
                event.tick,
                2,
                "WORLD",
                event_type.replace("_", " ").title(),
                port_id=port_id,
                commodity_id=payload.get("commodity_id"),
            )

        if item is None:
            continue
        if item.category == "PLAYER":
            individual.append(item)
            continue
        key = (
            item.tick // settings.ticks_per_day,
            item.category,
            item.port_id,
            item.commodity_id,
        )
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = item
        else:
            count = previous.count + 1
            grouped[key] = NewsItem(
                latest_sequence=max(previous.latest_sequence, item.latest_sequence),
                tick=max(previous.tick, item.tick),
                priority=item.priority,
                category=item.category,
                text=f"{item.text.removesuffix('.')} ({count} reports).",
                count=count,
                information_kind=item.information_kind,
                port_id=item.port_id,
                commodity_id=item.commodity_id,
            )
    known_report_ids = {
        event.payload.get("report_id")
        for event in world.recent_events
        if event.event_type == "InformationReportRead"
    }
    for report in world.player_information_reports:
        if report.id in known_report_ids:
            continue
        individual.append(
            NewsItem(
                latest_sequence=report.sequence,
                tick=report.created_tick,
                priority=2,
                category="RUMOUR" if report.information_kind == "RUMOUR" else "WORLD",
                text=report.headline,
                information_kind=report.information_kind,
                port_id=report.port_id,
                commodity_id=report.commodity_id,
            )
        )
    return tuple(
        sorted(
            (*individual, *grouped.values()),
            key=lambda item: (-item.priority, -item.tick, -item.latest_sequence),
        )
    )


def actor_map_position(world: WorldState, actor: Merchant) -> tuple[float, float]:
    if actor.port_id is not None:
        port = world.ports[actor.port_id]
        return float(port.map_x), float(port.map_y)
    if actor.voyage is None:
        return 0.0, 0.0
    route = world.routes[actor.voyage.route_id]
    destination = world.ports[actor.voyage.destination_id]
    origin = world.ports[route.other_end(actor.voyage.destination_id)]
    remaining = actor.voyage.remaining_ticks - actor.voyage.progress_remainder_bp / 10_000
    progress = 1.0 - remaining / route.travel_ticks
    return (
        origin.map_x + (destination.map_x - origin.map_x) * progress,
        origin.map_y + (destination.map_y - origin.map_y) * progress,
    )


class AssetCache:
    """Load, convert, and scale required assets once."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.images: dict[str, pygame.Surface] = {}
        self.fonts: dict[tuple[str, int], pygame.font.Font] = {}

    def image(self, filename: str, size: tuple[int, int] | None = None) -> pygame.Surface:
        key = f"{filename}:{size}"
        if key not in self.images:
            path = self.directory / filename
            try:
                image = pygame.image.load(path).convert_alpha()
            except (FileNotFoundError, pygame.error) as error:
                raise AssetError(f"Could not load required image {path}: {error}") from error
            if size is not None and image.get_size() != size:
                image = pygame.transform.smoothscale(image, size)
            self.images[key] = image
        return self.images[key]

    def font(self, filename: str, size: int) -> pygame.font.Font:
        key = (filename, size)
        if key not in self.fonts:
            path = self.directory / filename
            try:
                self.fonts[key] = pygame.font.Font(path, size)
            except (FileNotFoundError, pygame.error) as error:
                raise AssetError(f"Could not load required font {path}: {error}") from error
        return self.fonts[key]


class PirateTradingUI:
    """Own the pygame loop and transient presentation state."""

    def __init__(
        self,
        settings: SimulationSettings = SETTINGS,
        *,
        enable_audio: bool = True,
    ) -> None:
        self.settings = settings
        pygame.init()
        self.audio_available = False
        if enable_audio and settings.background_music is not None:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(settings.assets_directory / settings.background_music)
                pygame.mixer.music.set_volume(settings.music_volume)
                pygame.mixer.music.play(-1)
                self.audio_available = True
            except (FileNotFoundError, pygame.error):
                self.audio_available = False

        self.screen = pygame.display.set_mode((settings.window_width, settings.window_height))
        pygame.display.set_caption("Pirate Trading")
        self.clock = pygame.time.Clock()
        self.assets = AssetCache(settings.assets_directory)
        self._preload_assets()
        self.inspector_collapsed = False
        self.layout = UILayout.build(self.screen.get_size())
        self.camera = Camera(
            (settings.map_width, settings.map_height),
            self.layout.map_viewport,
            settings.camera_minimum_zoom,
            settings.camera_maximum_zoom,
        )
        self._map_cache_zoom = 0.0
        self._map_cache: pygame.Surface | None = None
        self._map_dragging = False
        self._map_drag_origin = (0, 0)
        self._map_drag_distance = 0.0

        self.running = True
        self.mode = "title"
        self.overlay: str | None = None
        self.world: WorldState | None = None
        self.engine: SimulationEngine | None = None
        self.selected_port_id = settings.player_starting_port
        self.selected_commodity_id = "grain"
        self.trade_quantity = 1
        self.active_sidebar_tab: SidebarTab = "market"
        self.row_offsets: dict[str, int] = {"market": 0, "hold": 0}
        self.overlay_scroll = 0
        self.opportunity_detail_scroll = 0
        self.opportunity_detail_max_scroll = 0
        self.opportunity_detail_rect = pygame.Rect(0, 0, 0, 0)
        self.news_read_through_sequence = -1
        self.status_message = ""
        self.buttons: dict[str, Button] = {}
        self.time = TimeController(settings.time_rates, settings.initial_time_rate_index)
        self.weekly_reveal_elapsed = 0.0
        self.resume_after_weekly_reveal = False
        self.selected_opportunity_id: str | None = None
        self.major_event_queue: list[dict[str, Any]] = []
        self.major_event_notice: dict[str, Any] | None = None
        self.seen_major_report_ids: set[str] = set()
        self.resume_after_major_event = False
        self.resume_after_news = False
        self.mission_notice_queue: list[dict[str, Any]] = []
        self.mission_notice: dict[str, Any] | None = None
        self.explosion_elapsed: float | None = None
        self.explosion_position = (0.0, 0.0)
        self.raid_loot_selection: dict[str, int] = {}
        self.raid_loot_commodity_id: str | None = None
        self.credits = self._load_credits()

    def _preload_assets(self) -> None:
        required_images = {
            "atlantic.png": (self.settings.map_width, self.settings.map_height),
            "message_paper.png": None,
            "player-marker.png": (48, 48),
            "play.png": (30, 30),
            "pause.png": (30, 30),
            "left-arrow.png": (28, 28),
            "right-arrow.png": (28, 28),
            "dollar.png": (24, 24),
            "information.png": (24, 24),
            "diploma.png": (24, 24),
            "explode.png": None,
        }
        for filename, size in required_images.items():
            self.assets.image(filename, size)
        for size in (14, 16, 18, 22, 30):
            self.assets.font("Jaapokki-Regular.otf", size)
        self.assets.font("dynalight.otf", 42)

    def _load_credits(self) -> list[str]:
        parser = _AcknowledgementParser()
        try:
            parser.feed(
                (self.settings.assets_directory / "acknowledgements.html").read_text(
                    encoding="utf-8"
                )
            )
        except OSError:
            return ["Acknowledgements file could not be loaded."]
        return parser.entries

    @property
    def player(self) -> Merchant:
        if self.world is None:
            raise RuntimeError("No game is active")
        return self.world.merchants[self.world.player_id]

    def _reset_transient_state(self, *, mark_existing_news_read: bool) -> None:
        self.overlay = None
        self.active_sidebar_tab = "market"
        self.row_offsets = {"market": 0, "hold": 0}
        self.overlay_scroll = 0
        self.opportunity_detail_scroll = 0
        self.opportunity_detail_max_scroll = 0
        self.opportunity_detail_rect = pygame.Rect(0, 0, 0, 0)
        self.trade_quantity = 1
        self.selected_opportunity_id = None
        self.major_event_queue = []
        self.major_event_notice = None
        self.seen_major_report_ids = set()
        self.resume_after_news = False
        self.mission_notice_queue = []
        self.mission_notice = None
        self.explosion_elapsed = None
        self.raid_loot_selection = {}
        self.raid_loot_commodity_id = None
        if self.world is None or not mark_existing_news_read:
            self.news_read_through_sequence = -1
        else:
            self.news_read_through_sequence = max(
                (event.sequence for event in self.world.recent_events),
                default=-1,
            )

    def _frame_port_network(self) -> None:
        if self.world is None:
            return
        self.camera.set_viewport(self.layout.map_viewport)
        self.camera.frame_points(
            [(float(port.map_x), float(port.map_y)) for port in self.world.ports.values()],
            maximum_zoom=self.settings.camera_initial_maximum_zoom,
        )

    def new_game(self) -> None:
        self.world = load_world(self.settings)
        self.engine = SimulationEngine(self.world, self.settings)
        self.mode = "game"
        self.selected_port_id = self.player.port_id or self.settings.player_starting_port
        self.selected_commodity_id = sorted(self.world.commodities)[0]
        self._reset_transient_state(mark_existing_news_read=False)
        self.time = TimeController(self.settings.time_rates, self.settings.initial_time_rate_index)
        self.status_message = "New voyage begun at Crown's Haven."
        self._frame_port_network()
        self._begin_weekly_reveal()

    def continue_game(self) -> None:
        try:
            world = load_game(settings=self.settings)
        except SaveError as error:
            self.status_message = str(error)
            self.overlay = "error"
            return
        self.world = world
        self.engine = SimulationEngine(world, self.settings)
        self.mode = "game"
        self.selected_port_id = self.player.port_id or self.player.voyage.destination_id
        self.selected_commodity_id = sorted(world.commodities)[0]
        self._reset_transient_state(mark_existing_news_read=True)
        self.time = TimeController(self.settings.time_rates, self.settings.initial_time_rate_index)
        self.status_message = "Quick-save loaded."
        self._frame_port_network()
        pending = next(
            (
                item
                for item in world.pending_raid_loot.values()
                if item.attacker_id == world.player_id and not item.resolved
            ),
            None,
        )
        if pending is not None:
            self._open_raid_loot(pending.id)
            return
        if world.weekly_outlook and world.weekly_outlook.reveal_pending:
            self._begin_weekly_reveal()

    def _begin_weekly_reveal(self) -> None:
        self.resume_after_weekly_reveal = not self.time.paused
        self.time.paused = True
        self.time.accumulator = 0
        self.weekly_reveal_elapsed = 0.0
        self.overlay = "weekly_roll"

    def _dismiss_weekly_reveal(self) -> None:
        if self.world is None or self.engine is None or self.world.weekly_outlook is None:
            return
        self.engine.execute_commands(
            [AcknowledgeWeeklyOutlook(self.world.weekly_outlook.week_index)]
        )
        self.overlay = None
        self.time.paused = not self.resume_after_weekly_reveal
        self.weekly_reveal_elapsed = 0.0
        self._show_next_major_event()
        self._show_next_mission_notice()

    def _queue_major_event(self, payload: dict[str, Any]) -> None:
        headline = str(payload.get("headline") or "")
        report_id = str(payload.get("report_id") or "")
        major_terms = {
            "Tropical Storm",
            "Hurricane",
            "Crop Blight",
            "Warehouse Fire",
            "Shipyard Commission",
            "Relief Call",
        }
        if headline not in major_terms and not payload.get("urgent"):
            return
        if self.world is not None and report_id:
            report = next(
                (item for item in self.world.player_information_reports if item.id == report_id),
                None,
            )
            if report is not None:
                payload = {
                    **payload,
                    "body": report.body,
                    "port_id": report.port_id,
                    "route_id": report.route_id,
                    "commodity_id": report.commodity_id,
                    "public_effect_summary": report.public_effect_summary,
                    "deadline_tick": report.deadline_tick,
                    "opportunity_ids": list(report.opportunity_ids),
                }
        if report_id and report_id in self.seen_major_report_ids:
            return
        identity = (payload.get("report_id"), headline)
        existing = {
            (item.get("report_id"), item.get("headline")) for item in self.major_event_queue
        }
        if self.major_event_notice is not None:
            existing.add(
                (
                    self.major_event_notice.get("report_id"),
                    self.major_event_notice.get("headline"),
                )
            )
        if identity not in existing:
            self.major_event_queue.append(dict(payload))
            if report_id:
                self.seen_major_report_ids.add(report_id)
        self._show_next_major_event()

    def _show_next_major_event(self) -> None:
        if not self.major_event_queue or self.overlay is not None:
            return
        self.major_event_notice = self.major_event_queue.pop(0)
        self.resume_after_major_event = not self.time.paused
        self.time.paused = True
        self.time.accumulator = 0
        self.overlay = "major_event"

    def _dismiss_major_event(self) -> None:
        self.major_event_notice = None
        self.overlay = None
        self.resume_after_major_event = False
        self.time.paused = True
        self.time.accumulator = 0
        self._show_next_major_event()
        self._show_next_mission_notice()

    def _open_news(self, *, resume_running: bool | None = None) -> None:
        self.resume_after_news = not self.time.paused if resume_running is None else resume_running
        self.time.paused = True
        self.time.accumulator = 0
        self.overlay_scroll = 0
        self.overlay = "news"
        if self.world is not None:
            self.news_read_through_sequence = max(
                (item.latest_sequence for item in build_news_items(self.world, self.settings)),
                default=self.news_read_through_sequence,
            )

    def _close_news(self) -> None:
        should_resume = self.resume_after_news
        self.resume_after_news = False
        self.overlay = None
        self.time.paused = not should_resume
        self._show_next_major_event()
        self._show_next_mission_notice()

    def _mission_notice_from_event(self, event: Any) -> dict[str, Any] | None:
        if self.world is None:
            return None
        if event.event_type not in {
            "OpportunityAccepted",
            "OpportunityCompleted",
            "OpportunityFailed",
        }:
            return None
        if event.payload.get("merchant_id") != self.world.player_id:
            return None
        opportunity = self.world.opportunities.get(event.payload.get("opportunity_id"))
        if opportunity is None:
            return None
        progress = build_mission_progress(
            self.world,
            self.world.player_id,
            opportunity,
            self.settings,
        )
        reward = f"${progress.reward / 100:.2f}"
        objective = opportunity.objective_type
        if event.event_type == "OpportunityAccepted":
            title = "MISSION ACCEPTED"
            if objective in {
                "delivery",
                "festival_supply",
                "harvest_export",
                "private_delivery",
            }:
                cargo_status = (
                    f"The required {progress.cargo_required} clean "
                    f"{progress.payload_label} are already aboard."
                    if progress.cargo_current >= progress.cargo_required
                    else f"Acquire {progress.cargo_required} clean "
                    f"{progress.payload_label or 'cargo'}; "
                    "contract goods are not supplied for free."
                )
                body = (
                    f"{cargo_status} Deliver them to {progress.destination_name} within "
                    f"{progress.deadline_hours}h to earn {reward}. "
                    f"Failure penalty: ${progress.penalty / 100:.2f}."
                )
            elif objective == "evacuation":
                body = (
                    f"{progress.mission_load_current} passengers are aboard as mission load. "
                    f"Carry them along {progress.route_label} to "
                    f"{progress.destination_name} within {progress.deadline_hours}h. "
                    f"Reward: {reward}; failure penalty: ${progress.penalty / 100:.2f}."
                )
            elif objective == "smuggling":
                body = (
                    f"{progress.mission_load_current} contraband crates are aboard as mission "
                    f"cargo. Reach {progress.destination_name} by any route within "
                    f"{progress.deadline_hours}h. Reward: {reward}; failure penalty: "
                    f"${progress.penalty / 100:.2f}."
                )
            elif objective == "salvage":
                body = (
                    f"Recover up to 5 {progress.payload_label} from the warehouse losses at "
                    f"{progress.destination_name} within {progress.deadline_hours}h. "
                    f"Reward: {reward}; failure penalty: ${progress.penalty / 100:.2f}."
                )
            else:
                route_instruction = (
                    f"Traverse {progress.route_label} and reach {progress.destination_name}"
                    if progress.route_label
                    else progress.action_hint
                )
                body = (
                    f"{route_instruction} within {progress.deadline_hours}h to complete "
                    f"{progress.objective_label}. Reward: {reward}; failure penalty: "
                    f"${progress.penalty / 100:.2f}."
                )
        elif event.event_type == "OpportunityCompleted":
            title = "MISSION COMPLETE"
            completion = {
                "delivery": (
                    f"Congratulations on delivering {opportunity.required_quantity} "
                    f"{progress.payload_label} to {progress.destination_name}."
                ),
                "festival_supply": (
                    f"The festival received {opportunity.required_quantity} clean "
                    f"{progress.payload_label}."
                ),
                "harvest_export": (
                    f"You delivered {opportunity.required_quantity} "
                    f"{progress.payload_label} to {progress.destination_name}."
                ),
                "private_delivery": (
                    f"Congratulations on completing the private delivery to "
                    f"{progress.destination_name}."
                ),
                "reconnaissance": (f"Your reconnaissance of {progress.route_label} is complete."),
                "evacuation": (f"The passengers arrived safely at {progress.destination_name}."),
                "salvage": (
                    f"You recovered {event.payload.get('progress', opportunity.progress)} "
                    f"{progress.payload_label} from the warehouse site."
                ),
                "protection": (f"The protected passage along {progress.route_label} is complete."),
                "smuggling": (f"The contraband crates reached {progress.destination_name}."),
            }.get(objective, f"The {objective.replace('_', ' ')} mission is complete.")
            body = f"{completion} Reward received: {reward}."
        else:
            title = "MISSION FAILED"
            reason = str(event.payload.get("reason") or "failed")
            penalty_paid = int(event.payload.get("penalty_paid") or 0)
            assessed = int(event.payload.get("penalty_assessed") or penalty_paid)
            penalty = f"${penalty_paid / 100:.2f}"
            assessment = (
                f" from the ${assessed / 100:.2f} amount assessed"
                if assessed != penalty_paid
                else ""
            )
            body = (
                f"{opportunity.title} was {reason}. A {penalty} penalty was collected{assessment}."
            )
        return {
            "title": title,
            "body": body,
            "opportunity_id": opportunity.id,
            "active": opportunity.status == "accepted",
        }

    def _queue_mission_notice(self, event: Any) -> None:
        notice = self._mission_notice_from_event(event)
        if notice is None:
            return
        self.mission_notice_queue.append(notice)
        self._show_next_mission_notice()

    def _show_next_mission_notice(self) -> None:
        if not self.mission_notice_queue or self.overlay is not None:
            return
        self.mission_notice = self.mission_notice_queue.pop(0)
        self.time.paused = True
        self.time.accumulator = 0
        self.overlay = "mission_notice"

    def _dismiss_mission_notice(self) -> None:
        self.mission_notice = None
        self.overlay = None
        self.time.paused = True
        self.time.accumulator = 0
        self._show_next_mission_notice()
        self._show_next_major_event()

    def _start_explosion(self, position: tuple[float, float]) -> None:
        self.explosion_position = position
        self.explosion_elapsed = 0.0

    def save(self) -> None:
        if self.world is None:
            return
        try:
            save_game(self.world, settings=self.settings)
            self.status_message = "Game saved."
        except SaveError as error:
            self.status_message = str(error)
            self.overlay = "error"

    def _add_button(
        self,
        name: str,
        rect: pygame.Rect,
        label: str = "",
        *,
        enabled: bool = True,
        icon: str | None = None,
    ) -> None:
        button = Button(name, rect, label, enabled, icon)
        self.buttons[name] = button
        color = PANEL_LIGHT if enabled else (55, 50, 45)
        pygame.draw.rect(self.screen, color, rect, border_radius=5)
        pygame.draw.rect(self.screen, ACCENT if enabled else MUTED, rect, 1, border_radius=5)
        if icon:
            image = self.assets.image(icon, (min(rect.width - 6, 28), min(rect.height - 6, 28)))
            self.screen.blit(image, image.get_rect(center=rect.center))
        elif label:
            self._text(label, 16, TEXT if enabled else MUTED, rect.center, center=True)

    def _text(
        self,
        value: str,
        size: int,
        color: tuple[int, int, int],
        position: tuple[int, int],
        *,
        center: bool = False,
        decorative: bool = False,
    ) -> pygame.Rect:
        filename = "dynalight.otf" if decorative else "Jaapokki-Regular.otf"
        surface = self.assets.font(filename, size).render(value, True, color)
        rect = surface.get_rect(center=position) if center else surface.get_rect(topleft=position)
        self.screen.blit(surface, rect)
        return rect

    def _wrap(self, text: str, width: int, font_size: int, maximum_lines: int) -> list[str]:
        font = self.assets.font("Jaapokki-Regular.otf", font_size)
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if font.size(candidate)[0] <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
                if len(lines) == maximum_lines:
                    break
        if current and len(lines) < maximum_lines:
            lines.append(current)
        return lines

    def _draw_title(self) -> None:
        self.screen.fill(BACKGROUND)
        title_map = self.assets.image("atlantic.png", (1000, 706))
        self.screen.blit(
            title_map,
            title_map.get_rect(midtop=(self.screen.get_rect().centerx, 7)),
        )
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((15, 10, 5, 145))
        self.screen.blit(shade, (0, 0))
        paper = self.assets.image("message_paper.png")
        paper_rect = paper.get_rect(center=self.screen.get_rect().center)
        self.screen.blit(paper, paper_rect)
        content = message_paper_content_rect(paper_rect)
        self._text(
            "Pirate Trading",
            42,
            INK,
            (content.centerx, content.y + 15),
            center=True,
            decorative=True,
        )
        self._text(
            "A Living-World Trading Sandbox",
            16,
            INK,
            (content.centerx, content.y + 50),
            center=True,
        )
        button_width = min(240, content.width - 20)
        button_x = content.centerx - button_width // 2
        first_button_y = content.bottom - 192
        self._add_button(
            "new",
            pygame.Rect(button_x, first_button_y, button_width, 42),
            "New Game",
        )
        self._add_button(
            "continue",
            pygame.Rect(button_x, first_button_y + 50, button_width, 42),
            "Continue",
            enabled=self.settings.quick_save_path.exists(),
        )
        self._add_button(
            "credits",
            pygame.Rect(button_x, first_button_y + 100, button_width, 42),
            "Credits",
        )
        self._add_button(
            "quit",
            pygame.Rect(button_x, first_button_y + 150, button_width, 42),
            "Quit",
        )

    def _draw_routes_and_ports(self) -> None:
        if self.world is None:
            return
        for route in self.world.routes.values():
            first = self.world.ports[route.port_a]
            second = self.world.ports[route.port_b]
            first_point = self.camera.world_to_screen((first.map_x, first.map_y))
            second_point = self.camera.world_to_screen((second.map_x, second.map_y))
            dangerous = any(
                condition.stage == "active"
                and condition.event_type in {"tropical_storm", "hurricane"}
                and (
                    condition.route_id == route.id
                    or condition.port_id in (route.port_a, route.port_b)
                )
                for condition in self.world.active_conditions
            )
            pygame.draw.line(
                self.screen,
                BAD if dangerous else ROUTE,
                first_point,
                second_point,
                3,
            )
        for port in self.world.ports.values():
            point = self.camera.world_to_screen((port.map_x, port.map_y))
            selected = port.id == self.selected_port_id
            affected = any(
                condition.stage == "active" and condition.port_id == port.id
                for condition in self.world.active_conditions
            )
            marker_color = BAD if affected else ACCENT if selected else PANEL
            pygame.draw.circle(self.screen, marker_color, point, 11)
            pygame.draw.circle(self.screen, TEXT, point, 11, 2)
            self._text(port.name, 14, INK, (point[0] + 14, point[1] - 9))

        for merchant in self.world.merchants.values():
            x, y = actor_map_position(self.world, merchant)
            point = self.camera.world_to_screen((x, y))
            if merchant.controller == "player":
                marker = self.assets.image("player-marker.png", (48, 48))
                self.screen.blit(marker, marker.get_rect(center=(round(point[0]), round(point[1]))))
            else:
                pygame.draw.circle(
                    self.screen,
                    ACCENT,
                    (round(point[0]), round(point[1])),
                    4,
                )

        for encounter in self.world.pending_encounters.values():
            if encounter.resolved or encounter.encounter_type != "navy":
                continue
            merchant = self.world.merchants.get(encounter.merchant_id)
            if merchant is None:
                continue
            x, y = actor_map_position(self.world, merchant)
            point = self.camera.world_to_screen((x, y))
            stamp = self.assets.image("stamp.png", (32, 32))
            stamp_rect = stamp.get_rect(center=(round(point[0] + 24), round(point[1] - 24)))
            self.screen.blit(stamp, stamp_rect)

        outlook = self.world.weekly_outlook
        if outlook is not None:
            rule = current_outlook_rule(self.world)
            self._add_button(
                "weekly_heading",
                pygame.Rect(
                    self.layout.map_viewport.x + 18,
                    self.layout.map_viewport.y + 12,
                    min(610, self.layout.map_viewport.width - 36),
                    42,
                ),
                f"WEEK {outlook.week_index + 1} · {rule.name.upper()} · ROLL {outlook.total}",
            )

    def _trade_limits(self) -> tuple[int, int]:
        if self.world is None or self.player.port_id != self.selected_port_id:
            return 0, 0
        port = self.world.ports[self.selected_port_id]
        inventory = port.inventories[self.selected_commodity_id]
        return (
            maximum_buy_quantity(self.player, inventory),
            sell_limit(self.player, self.selected_commodity_id, port.treasury, inventory),
        )

    def _trade_unavailable_reason(self, *, buying: bool) -> str:
        if self.world is None:
            return "No game is active."
        if self.player.voyage is not None:
            return "Trading is unavailable while travelling."
        if self.player.port_id != self.selected_port_id:
            return "Select your current port to trade."
        inventory = self.world.ports[self.selected_port_id].inventories[self.selected_commodity_id]
        if buying:
            if inventory.quantity <= 0:
                return "The market has no stock available."
            if self.player.cargo_quantity >= self.player.capacity:
                return "The cargo hold is full."
            if self.player.cash < inventory.ask_price:
                return "You cannot afford one unit."
        else:
            total = self.player.cargo.get(self.selected_commodity_id, 0)
            stolen = self.player.stolen_cargo.get(self.selected_commodity_id, 0)
            if total <= 0:
                return "You do not hold this commodity."
            if total - stolen <= 0:
                return "This cargo is stolen; fence it at Blackwater Cay."
            if inventory.quantity >= inventory.capacity:
                return "The port has no storage space."
            if self.world.ports[self.selected_port_id].treasury < inventory.bid_price:
                return "The port cannot afford one unit."
        return ""

    def _activate_tab(self, tab: SidebarTab) -> None:
        self.active_sidebar_tab = tab
        self.trade_quantity = 1
        if self.world is None:
            return
        if tab == "market":
            self._reveal_selected("market", sorted(self.world.commodities), 5)
        elif tab == "hold":
            held_ids = [item.commodity_id for item in self._holding_views()]
            if held_ids and self.selected_commodity_id not in held_ids:
                self.selected_commodity_id = held_ids[0]
            self._reveal_selected("hold", held_ids, 4)

    def _reveal_selected(self, list_name: str, item_ids: list[str], visible_count: int) -> None:
        if self.selected_commodity_id not in item_ids:
            return
        selected_index = item_ids.index(self.selected_commodity_id)
        offset = self.row_offsets[list_name]
        if selected_index < offset:
            self.row_offsets[list_name] = selected_index
        elif selected_index >= offset + visible_count:
            self.row_offsets[list_name] = selected_index - visible_count + 1

    def _holding_views(self) -> tuple[CargoHoldingView, ...]:
        if self.world is None:
            return ()
        return build_cargo_holdings(
            self.world,
            self.world.player_id,
            self.selected_port_id,
        )

    def _draw_tabs(self, sidebar_x: int) -> None:
        tabs = (
            ("market", "Market"),
            ("hold", "Hold"),
            ("port", "Port"),
            ("ship", "Ship"),
            ("reputation", "Heat"),
        )
        for index, (tab, label) in enumerate(tabs):
            rect = pygame.Rect(sidebar_x + 5 + index * 60, 164, 57, 32)
            if tab == self.active_sidebar_tab:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=4)
            self._add_button(f"tab:{tab}", rect, label)

    def _draw_market(self, sidebar_x: int) -> None:
        if self.world is None:
            return
        port = self.world.ports[self.selected_port_id]
        self._text(port.name, 18, TEXT, (sidebar_x + 10, 203))
        self._add_button(
            "history",
            pygame.Rect(sidebar_x + 232, 200, 38, 30),
            icon="information.png",
        )
        self._text("Commodity", 13, MUTED, (sidebar_x + 10, 229))
        self._text("Stock", 13, MUTED, (sidebar_x + 105, 229))
        self._text("Bid", 13, MUTED, (sidebar_x + 168, 229))
        self._text("Ask", 13, MUTED, (sidebar_x + 220, 229))

        commodity_ids = sorted(self.world.commodities)
        visible_count = 5
        if self.row_offsets["market"] == 0:
            self._reveal_selected("market", commodity_ids, visible_count)
        maximum_offset = max(0, len(commodity_ids) - visible_count)
        self.row_offsets["market"] = min(self.row_offsets["market"], maximum_offset)
        visible_ids = commodity_ids[
            self.row_offsets["market"] : self.row_offsets["market"] + visible_count
        ]
        list_rect = pygame.Rect(sidebar_x + 5, 246, 270, 158)
        old_clip = self.screen.get_clip()
        self.screen.set_clip(list_rect)
        for index, commodity_id in enumerate(visible_ids):
            commodity = self.world.commodities[commodity_id]
            inventory = port.inventories[commodity_id]
            report = known_market_report(self.world, port.id, commodity_id)
            y = 246 + index * 31
            rect = pygame.Rect(sidebar_x + 6, y, 268, 29)
            if commodity_id == self.selected_commodity_id:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=4)
            else:
                pygame.draw.rect(self.screen, PANEL_LIGHT, rect, 1, border_radius=4)
            self.buttons[f"commodity:{commodity_id}"] = Button(f"commodity:{commodity_id}", rect)
            known_quantity = report.quantity if report else None
            known_inventory = Inventory(
                known_quantity if known_quantity is not None else inventory.target,
                inventory.target,
                inventory.capacity,
                0,
                0,
            )
            status = market_status(known_inventory, self.settings)
            status_color = BAD if status == "Shortage" else GOOD if status == "Surplus" else TEXT
            trend = (
                price_trend(self.world.price_history[port.id][commodity_id])
                if self.player.port_id == port.id
                else ""
            )
            self._text(commodity.name, 13, TEXT, (sidebar_x + 10, y + 6))
            self._text(
                str(known_quantity) if known_quantity is not None else "?",
                13,
                status_color if report else MUTED,
                (sidebar_x + 108, y + 6),
            )
            self._text(
                f"{report.bid_price / 100:.2f}" if report else "—",
                13,
                TEXT if report else MUTED,
                (sidebar_x + 163, y + 6),
            )
            self._text(
                f"{report.ask_price / 100:.2f}{trend}" if report else "—",
                13,
                TEXT if report else MUTED,
                (sidebar_x + 214, y + 6),
            )
        self.screen.set_clip(old_clip)

        commodity = self.world.commodities[self.selected_commodity_id]
        held = self.player.cargo.get(self.selected_commodity_id, 0)
        report = known_market_report(self.world, port.id, self.selected_commodity_id)
        info_label = "No report"
        if report is not None:
            age = max(0, self.world.tick - report.created_tick)
            kind = "FACT" if self.player.port_id == port.id else "ESTIMATE"
            info_label = f"{kind} · {age}h"
        self._text(
            f"{commodity.name} · Held {held} · {info_label}",
            13,
            TEXT,
            (sidebar_x + 10, 409),
        )
        self._add_button("minus", pygame.Rect(sidebar_x + 10, 435, 36, 31), "−")
        self._text(str(self.trade_quantity), 16, TEXT, (sidebar_x + 65, 441))
        self._add_button("plus", pygame.Rect(sidebar_x + 102, 435, 36, 31), "+")

        buy_limit, sell_max = self._trade_limits()
        self._add_button(
            "buy",
            pygame.Rect(sidebar_x + 10, 474, 92, 34),
            "Buy",
            enabled=0 < self.trade_quantity <= buy_limit,
        )
        self._add_button(
            "max_buy",
            pygame.Rect(sidebar_x + 110, 474, 98, 34),
            "Max Buy",
            enabled=buy_limit > 0,
        )
        self._add_button(
            "sell",
            pygame.Rect(sidebar_x + 10, 516, 92, 34),
            "Sell",
            enabled=0 < self.trade_quantity <= sell_max,
        )
        self._add_button(
            "max_sell",
            pygame.Rect(sidebar_x + 110, 516, 98, 34),
            "Max Sell",
            enabled=sell_max > 0,
        )
        day = self.world.tick // self.settings.ticks_per_day
        access_key = f"{day}:{self.player.id}:{port.id}"
        access = self.world.trade_access.get(access_key)
        access_label = "Unknown" if access is None else "Passed" if access else "Blocked"
        chance = current_outlook_rule(self.world).trade_access_bp // 100
        self._text(
            f"Market clearance: {access_label} · {chance}%",
            12,
            GOOD if access else BAD if access is False else MUTED,
            (sidebar_x + 10, 558),
        )
        if buy_limit == 0 and sell_max == 0:
            reason = self._trade_unavailable_reason(buying=held == 0)
            self._text(reason, 12, MUTED, (sidebar_x + 10, 575))

    def _draw_hold(self, sidebar_x: int) -> None:
        if self.world is None:
            return
        holdings = self._holding_views()
        self._text(
            f"Hold {self.player.cargo_quantity}/{self.player.capacity}",
            18,
            TEXT,
            (sidebar_x + 10, 203),
        )
        self._add_button(
            "history",
            pygame.Rect(sidebar_x + 232, 200, 38, 30),
            icon="information.png",
        )
        if not holdings:
            self._text("Your cargo hold is empty.", 16, TEXT, (sidebar_x + 10, 250))
            self._text(
                f"{self.player.capacity} units available.",
                14,
                MUTED,
                (sidebar_x + 10, 278),
            )
            return

        held_ids = [item.commodity_id for item in holdings]
        if self.selected_commodity_id not in held_ids:
            self.selected_commodity_id = held_ids[0]
        visible_count = 4
        maximum_offset = max(0, len(holdings) - visible_count)
        self.row_offsets["hold"] = min(self.row_offsets["hold"], maximum_offset)
        visible = holdings[self.row_offsets["hold"] : self.row_offsets["hold"] + visible_count]
        list_rect = pygame.Rect(sidebar_x + 5, 235, 270, 148)
        old_clip = self.screen.get_clip()
        self.screen.set_clip(list_rect)
        for index, holding in enumerate(visible):
            y = 235 + index * 37
            rect = pygame.Rect(sidebar_x + 6, y, 268, 34)
            if holding.commodity_id == self.selected_commodity_id:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=4)
            else:
                pygame.draw.rect(self.screen, PANEL_LIGHT, rect, 1, border_radius=4)
            self.buttons[f"holding:{holding.commodity_id}"] = Button(
                f"holding:{holding.commodity_id}",
                rect,
            )
            self._text(holding.name, 14, TEXT, (sidebar_x + 10, y + 8))
            cargo_label = f"{holding.clean_quantity} clean"
            if holding.stolen_quantity:
                cargo_label += f" · {holding.stolen_quantity} stolen"
            self._text(
                cargo_label,
                12,
                BAD if holding.stolen_quantity else TEXT,
                (sidebar_x + 130, y + 9),
            )
        self.screen.set_clip(old_clip)

        holding = next(item for item in holdings if item.commodity_id == self.selected_commodity_id)
        profit_color = GOOD if holding.estimated_profit >= 0 else BAD
        self._text(f"Cost: ${holding.cost_basis / 100:,.2f}", 13, TEXT, (sidebar_x + 10, 390))
        self._text(
            f"Average: ${holding.average_unit_cost / 100:,.2f} each",
            13,
            TEXT,
            (sidebar_x + 10, 408),
        )
        quote = (
            f"${holding.bid_price / 100:,.2f} · {holding.information_kind} · "
            f"{holding.quote_age_hours}h"
            if holding.quote_available
            else "No delivered report"
        )
        self._text(
            f"{self.world.ports[self.selected_port_id].name}: {quote}",
            13,
            TEXT if holding.quote_available else MUTED,
            (sidebar_x + 10, 426),
        )
        self._text(
            f"Value: ${holding.estimated_value / 100:,.2f}",
            13,
            TEXT,
            (sidebar_x + 10, 444),
        )
        result_label = "Gain" if holding.estimated_profit >= 0 else "Loss"
        self._text(
            f"Est. {result_label}: ${abs(holding.estimated_profit) / 100:,.2f}",
            13,
            profit_color,
            (sidebar_x + 10, 462),
        )
        self._text(
            f"Clean {holding.clean_quantity} · Stolen {holding.stolen_quantity}",
            13,
            BAD if holding.stolen_quantity else MUTED,
            (sidebar_x + 150, 462),
        )
        self._add_button("minus", pygame.Rect(sidebar_x + 10, 486, 36, 31), "−")
        self._text(str(self.trade_quantity), 16, TEXT, (sidebar_x + 65, 492))
        self._add_button("plus", pygame.Rect(sidebar_x + 102, 486, 36, 31), "+")
        self._add_button(
            "sell",
            pygame.Rect(sidebar_x + 10, 526, 92, 34),
            "Sell",
            enabled=0 < self.trade_quantity <= holding.sellable_quantity,
        )
        self._add_button(
            "max_sell",
            pygame.Rect(sidebar_x + 110, 526, 98, 34),
            "Max",
            enabled=holding.sellable_quantity > 0,
        )
        reason = self._trade_unavailable_reason(buying=False)
        at_blackwater = (
            self.player.port_id == "blackwater_cay"
            and self.selected_port_id == "blackwater_cay"
            and self.player.voyage is None
        )
        if at_blackwater and holding.stolen_quantity:
            inventory = self.world.ports["blackwater_cay"].inventories[holding.commodity_id]
            fence_price = inventory.bid_price * self.settings.fence_price_bp // 10_000
            fence_maximum = min(
                holding.stolen_quantity,
                inventory.capacity - inventory.quantity,
                (self.world.ports["blackwater_cay"].treasury // fence_price if fence_price else 0),
            )
            self._add_button(
                "fence_stolen",
                pygame.Rect(sidebar_x + 10, 564, 92, 24),
                "Fence",
                enabled=0 < self.trade_quantity <= fence_maximum,
            )
            self._add_button(
                "max_fence",
                pygame.Rect(sidebar_x + 110, 564, 98, 24),
                "Max Fence",
                enabled=fence_maximum > 0,
            )
        elif reason:
            self._text(reason, 13, MUTED, (sidebar_x + 10, 566))

    def _draw_port(self, sidebar_x: int) -> None:
        if self.world is None:
            return
        port = self.world.ports[self.selected_port_id]
        self._text(port.name, 20, TEXT, (sidebar_x + 10, 203))
        if self.player.voyage is not None:
            relationship = (
                f"At sea · destination {self.world.ports[self.player.voyage.destination_id].name}"
            )
        elif self.player.port_id == port.id:
            relationship = "Docked here"
        else:
            relationship = f"Selected from {self.world.ports[self.player.port_id].name}"
        self._text(relationship, 14, MUTED, (sidebar_x + 10, 232))

        status_counts = {"Shortage": 0, "Balanced": 0, "Surplus": 0}
        for commodity_id, inventory in port.inventories.items():
            report = known_market_report(self.world, port.id, commodity_id)
            known_quantity = report.quantity if report else inventory.target
            status_counts[
                market_status(
                    Inventory(known_quantity, inventory.target, inventory.capacity, 0, 0),
                    self.settings,
                )
            ] += 1
        self._text("Market conditions", 15, TEXT, (sidebar_x + 10, 263))
        self._text(
            f"SHORTAGE {status_counts['Shortage']}  ·  BALANCED "
            f"{status_counts['Balanced']}  ·  SURPLUS {status_counts['Surplus']}",
            12,
            MUTED,
            (sidebar_x + 10, 287),
        )
        heat = self.world.piracy_state.local_heat.get(port.id, 0)
        status = (
            "Clear"
            if heat < 25
            else "Watched"
            if heat < 50
            else "Wanted"
            if heat < 75
            else "Hunted"
        )
        patrol = max(
            (
                route.navy_patrol_bp
                for route in self.world.routes.values()
                if port.id in (route.port_a, route.port_b)
            ),
            default=0,
        )
        self._text(
            f"Enforce {port.enforcement_bp / 100:.0f}% · patrol {patrol / 100:.1f}% · "
            f"heat {heat} ({status})",
            11,
            BAD if heat >= 50 else MUTED,
            (sidebar_x + 10, 305),
        )
        self._text("Connected routes", 15, TEXT, (sidebar_x + 10, 326))
        y = 347
        for route in sorted(self.world.routes.values(), key=lambda item: item.id):
            if port.id not in (route.port_a, route.port_b):
                continue
            destination = self.world.ports[route.other_end(port.id)]
            self._text(destination.name, 14, TEXT, (sidebar_x + 10, y))
            self._text(
                f"{route.travel_ticks}h · ${route.operating_cost / 100:.2f} · "
                f"threat {route.risk_bp / 100:.0f}%",
                12,
                MUTED,
                (sidebar_x + 125, y + 1),
            )
            y += 21

        ship = self.player.ship
        self._text(
            f"Ship: condition {ship.condition}% · provisions {ship.provisions} · "
            f"debt ${ship.wage_debt / 100:.2f}",
            12,
            TEXT,
            (sidebar_x + 10, 432),
        )
        docked = self.player.port_id == port.id and self.player.voyage is None
        self._add_button(
            "buy_provision",
            pygame.Rect(sidebar_x + 10, 452, 82, 30),
            "+ Provision",
            enabled=docked,
        )
        self._add_button(
            "repair_ship",
            pygame.Rect(sidebar_x + 98, 452, 82, 30),
            "Repair 10",
            enabled=docked and ship.condition < self.settings.condition_maximum,
        )
        self._add_button(
            "pay_wages",
            pygame.Rect(sidebar_x + 186, 452, 84, 30),
            "Pay Wages",
            enabled=docked and ship.wage_debt > 0,
        )
        raid_target = self._raidable_ship()
        if self.player.voyage is not None:
            self._add_button(
                "raid_ship",
                pygame.Rect(sidebar_x + 10, 488, 260, 28),
                (
                    f"Raid {raid_target.name} · $5"
                    if raid_target is not None
                    else "No ship within raiding range"
                ),
                enabled=raid_target is not None
                and self.player.cash >= self.settings.ship_raid_cost,
            )
        else:
            self._add_button(
                "upgrades",
                pygame.Rect(sidebar_x + 10, 488, 126, 28),
                f"Upgrades ({len(ship.upgrades)}/4)",
                enabled=docked,
            )
            cooldown_key = f"{self.player.id}:{port.id}"
            raid_ready = self.world.raid_cooldowns.get(cooldown_key, 0) <= self.world.tick
            self._add_button(
                "raid_port",
                pygame.Rect(sidebar_x + 142, 488, 128, 28),
                "Raid Port · $25",
                enabled=(
                    docked
                    and raid_ready
                    and self.player.cash >= self.settings.port_raid_cost
                    and ship.condition > 30
                ),
            )

        sail_enabled = False
        sail_label = "Select a connected destination"
        reason = ""
        if self.player.voyage is not None:
            sail_label = "Voyage already underway"
        elif self.player.port_id == self.selected_port_id:
            reason = "Choose another connected port on the map."
        else:
            route = self._route_between(self.player.port_id, self.selected_port_id)
            if route is None:
                sail_label = "No direct route"
            elif self.player.cash < route.operating_cost:
                sail_label = "Cannot afford voyage"
            else:
                sail_enabled = True
                sail_label = f"Sail {route.travel_ticks}h · ${route.operating_cost / 100:.2f}"
        self._add_button(
            "sail",
            pygame.Rect(sidebar_x + 10, 526, 260, 38),
            sail_label,
            enabled=sail_enabled,
        )
        if reason:
            self._text(reason, 13, MUTED, (sidebar_x + 10, 566))
        if docked:
            if port.id == "blackwater_cay":
                stolen = sum(self.player.stolen_cargo.values())
                self._add_button(
                    "open_stolen_hold",
                    pygame.Rect(sidebar_x + 10, 564, 126, 22),
                    f"Review Stolen ({stolen})",
                    enabled=stolen > 0,
                )
                self._add_button(
                    "lay_low",
                    pygame.Rect(sidebar_x + 142, 564, 128, 22),
                    "Lay Low · $100",
                    enabled=self.player.cash >= self.settings.lay_low_cost,
                )
            elif port.enforcement_bp and heat < 75:
                self._add_button(
                    "pay_officials",
                    pygame.Rect(sidebar_x + 10, 564, 260, 22),
                    "Pay Officials · remove up to 20 heat",
                    enabled=heat > 0,
                )

    def _draw_ship(self, sidebar_x: int) -> None:
        ship = self.player.ship
        self._text("Your Ship", 20, TEXT, (sidebar_x + 10, 203))
        details = (
            f"Condition: {ship.condition}/{self.settings.condition_maximum}",
            f"Hold: {self.player.cargo_quantity}/{self.player.capacity}",
            f"Provisions: {ship.provisions}",
            f"Wage debt: ${ship.wage_debt / 100:.2f}",
            f"Upgrades: {', '.join(ship.upgrades) or 'none'}",
        )
        for index, line in enumerate(details):
            self._text(line, 14, TEXT, (sidebar_x + 10, 245 + index * 28))
        if self.player.port_id is not None:
            self._add_button(
                "buy_provision",
                pygame.Rect(sidebar_x + 10, 410, 88, 32),
                "Provision",
            )
            self._add_button(
                "repair_ship",
                pygame.Rect(sidebar_x + 106, 410, 88, 32),
                "Repair 10",
                enabled=ship.condition < self.settings.condition_maximum,
            )
            self._add_button(
                "upgrades",
                pygame.Rect(sidebar_x + 202, 410, 88, 32),
                "Upgrades",
            )

    def _draw_reputation(self, sidebar_x: int) -> None:
        state = self.world.piracy_state
        self._text("Piracy & Enforcement", 20, TEXT, (sidebar_x + 10, 203))
        self._text(
            f"Global notoriety: {state.notoriety}/100",
            15,
            BAD if state.notoriety >= 50 else TEXT,
            (sidebar_x + 10, 242),
        )
        y = 278
        for port_id, heat in sorted(state.local_heat.items()):
            label = (
                "Clear"
                if heat < 25
                else "Watched"
                if heat < 50
                else "Wanted"
                if heat < 75
                else "Hunted"
            )
            self._text(
                f"{self.world.ports[port_id].name}: {heat} · {label}",
                13,
                BAD if heat >= 50 else MUTED,
                (sidebar_x + 10, y),
            )
            y += 24
        stolen = sum(self.player.stolen_cargo.values())
        self._text(
            f"Stolen cargo aboard: {stolen}",
            14,
            BAD if stolen else TEXT,
            (sidebar_x + 10, y + 14),
        )
        if self.player.port_id == "blackwater_cay" and stolen:
            self._add_button(
                "open_stolen_hold",
                pygame.Rect(sidebar_x + 10, y + 48, 180, 32),
                "Open Contraband Hold",
            )

    def _draw_sidebar(self) -> None:
        if self.world is None or self.inspector_collapsed:
            return
        sidebar_x = self.layout.inspector.x
        pygame.draw.rect(
            self.screen,
            PANEL,
            self.layout.inspector,
        )
        self._text("CURRENT LOCATION", 11, MUTED, (sidebar_x + 10, 62))
        location = (
            self.world.ports[self.player.port_id].name
            if self.player.port_id
            else f"To {self.world.ports[self.player.voyage.destination_id].name} "
            f"({self.player.voyage.remaining_ticks}h)"
        )
        self._text(location, 14, TEXT, (sidebar_x + 10, 80))
        local_port = self.player.port_id or self.player.voyage.destination_id
        heat = self.world.piracy_state.local_heat.get(local_port, 0)
        level = max(heat, self.world.piracy_state.notoriety)
        status = (
            "CLEAR"
            if level < 25
            else "WATCHED"
            if level < 50
            else "WANTED"
            if level < 75
            else "HUNTED"
        )
        self._text(
            f"{status} · heat {heat} · notoriety {self.world.piracy_state.notoriety}",
            12,
            BAD if level >= 50 else ACCENT,
            (sidebar_x + 10, 100),
        )

        self._add_button(
            "slower",
            pygame.Rect(sidebar_x + 12, 122, 38, 36),
            icon="left-arrow.png",
        )
        self._add_button(
            "play_pause",
            pygame.Rect(sidebar_x + 58, 122, 38, 36),
            icon="play.png" if self.time.paused else "pause.png",
        )
        self._add_button(
            "faster",
            pygame.Rect(sidebar_x + 104, 122, 38, 36),
            icon="right-arrow.png",
        )
        speed_label = "Paused" if self.time.paused else f"{self.time.rate} h/s"
        self._text(speed_label, 16, TEXT, (sidebar_x + 156, 130))

        self._draw_tabs(sidebar_x)
        draw_context_panel(self, sidebar_x)

        controls_y = self.settings.window_height - 90
        self._add_button("save", pygame.Rect(sidebar_x + 10, controls_y, 82, 32), "Save")
        self._add_button("load", pygame.Rect(sidebar_x + 98, controls_y, 84, 32), "Load")
        self._add_button("menu", pygame.Rect(sidebar_x + 188, controls_y, 82, 32), "Menu")
        for line_index, line in enumerate(
            self._wrap(self.status_message, 260, 14, maximum_lines=2)
        ):
            self._text(
                line,
                14,
                MUTED,
                (sidebar_x + 10, self.settings.window_height - 46 + line_index * 18),
            )

    def _route_between(self, first: str, second: str) -> Any | None:
        if self.world is None:
            return None
        for route in self.world.routes.values():
            if {route.port_a, route.port_b} == {first, second}:
                return route
        return None

    def _raidable_ship(self) -> Merchant | None:
        if self.world is None or self.player.voyage is None:
            return None
        candidates = [
            merchant
            for merchant in self.world.merchants.values()
            if merchant.controller == "ai"
            and merchant.voyage is not None
            and merchant.voyage.route_id == self.player.voyage.route_id
        ]
        return min(candidates, key=lambda merchant: merchant.id, default=None)

    def _draw_game(self) -> None:
        self.screen.fill(BACKGROUND)
        viewport = self.layout.map_viewport
        old_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        if self._map_cache is None or self._map_cache_zoom != self.camera.zoom:
            source = self.assets.image(
                "atlantic.png",
                (self.settings.map_width, self.settings.map_height),
            )
            self._map_cache = pygame.transform.smoothscale(
                source,
                (
                    round(self.settings.map_width * self.camera.zoom),
                    round(self.settings.map_height * self.camera.zoom),
                ),
            )
            self._map_cache_zoom = self.camera.zoom
        top_left = self.camera.world_to_screen((0.0, 0.0))
        self.screen.blit(self._map_cache, (round(top_left[0]), round(top_left[1])))
        self._draw_routes_and_ports()
        self._draw_explosion()
        self.screen.set_clip(old_clip)
        self._draw_sidebar()
        pygame.draw.rect(self.screen, PANEL, self.layout.status_bar)
        pygame.draw.rect(self.screen, PANEL, self.layout.navigation_rail)
        pygame.draw.rect(self.screen, PANEL, self.layout.action_tray)
        self._draw_application_shell()

    def _draw_application_shell(self) -> None:
        if self.world is None:
            return
        self._text("PIRATE TRADING", 18, TEXT, (14, 17))
        self._text(
            f"{game_clock_label(self.world.tick, self.settings.ticks_per_day)} · "
            f"${self.player.cash / 100:,.2f}",
            16,
            TEXT,
            (220, 18),
        )
        self._add_button(
            "open_hold",
            pygame.Rect(480, 10, 150, 34),
            f"Hold {self.player.cargo_quantity}/{self.player.capacity}",
        )
        navigation = (
            ("news", "News"),
            ("opportunities", "Jobs"),
            ("journal", "Log"),
            ("history", "Chart"),
        )
        unread = sum(
            item.latest_sequence > self.news_read_through_sequence
            for item in build_news_items(self.world, self.settings)
        )
        for index, (name, label) in enumerate(navigation):
            if name == "news" and unread:
                label = f"News {unread}"
            self._add_button(
                name,
                pygame.Rect(5, 76 + index * 54, 54, 42),
                label,
            )
        self._add_button(
            "toggle_inspector",
            pygame.Rect(self.screen.get_width() - 54, 10, 44, 34),
            "Panel",
        )
        follow_label = "Following" if self.camera.following else "Free Camera"
        self._text(
            f"{follow_label} · wheel zoom · drag pan · F follow · Home frame",
            13,
            MUTED,
            (self.layout.action_tray.x + 14, self.layout.action_tray.y + 12),
        )
        for index, line in enumerate(
            self._wrap(self.status_message, self.layout.action_tray.width - 28, 14, 2)
        ):
            self._text(
                line,
                14,
                TEXT,
                (self.layout.action_tray.x + 14, self.layout.action_tray.y + 38 + index * 18),
            )

    def _draw_explosion(self) -> None:
        if self.explosion_elapsed is None:
            return
        progress = min(
            1.0,
            self.explosion_elapsed / self.settings.explosion_animation_seconds,
        )
        pulse = math.sin(progress * math.pi)
        size = max(18, round(42 + pulse * 92))
        source = self.assets.image("explode.png")
        image = pygame.transform.smoothscale(source, (size, size))
        image = pygame.transform.rotate(image, progress * 80)
        image.set_alpha(round(255 * (1.0 - progress**2)))
        x, y = self.explosion_position
        screen_point = self.camera.world_to_screen((x, y))
        self.screen.blit(
            image,
            image.get_rect(center=(round(screen_point[0]), round(screen_point[1]))),
        )

    def _draw_overlay(self) -> None:
        if self.overlay is None:
            return
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((15, 10, 5, 150))
        self.screen.blit(shade, (0, 0))
        paper_width = min(self.screen.get_width() - 180, 880)
        paper_height = min(self.screen.get_height() - 60, 680)
        paper = self.assets.image("message_paper.png", (paper_width, paper_height))
        paper_rect = paper.get_rect(center=self.screen.get_rect().center)
        self.screen.blit(paper, paper_rect)
        content = message_paper_content_rect(paper_rect)
        if self.overlay not in {
            "weekly_roll",
            "encounter",
            "major_event",
            "mission_notice",
            "raid_loot",
        }:
            self._add_button(
                "close_overlay",
                pygame.Rect(content.right - 48, content.y, 42, 30),
                "Close",
            )
        if self.overlay == "credits":
            self._text("Credits", 30, INK, (content.centerx, content.y + 20), center=True)
            for index, entry in enumerate(self.credits[:13]):
                self._text(entry, 14, INK, (content.x, content.y + 50 + index * 18))
        elif not draw_focused_overlay(self, content):
            self._text("Notice", 30, INK, (content.centerx, content.y + 30), center=True)
            for index, line in enumerate(
                self._wrap(self.status_message, content.width - 30, 16, 8)
            ):
                self._text(line, 16, INK, (content.x + 15, content.y + 75 + index * 24))

    def _pending_raid_loot(self) -> Any | None:
        if self.world is None:
            return None
        return next(
            (
                item
                for item in self.world.pending_raid_loot.values()
                if item.attacker_id == self.world.player_id and not item.resolved
            ),
            None,
        )

    def _open_raid_loot(self, raid_id: str) -> None:
        if self.world is None:
            return
        pending = self.world.pending_raid_loot.get(raid_id)
        if pending is None or pending.resolved:
            return
        self.time.paused = True
        self.time.accumulator = 0
        self.raid_loot_selection = {
            commodity_id: 0 for commodity_id in sorted(pending.available_cargo)
        }
        self.raid_loot_commodity_id = next(iter(self.raid_loot_selection), None)
        self.overlay_scroll = 0
        self.overlay = "raid_loot"

    def _draw_raid_loot(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        pending = self._pending_raid_loot()
        if pending is None:
            self.overlay = None
            return
        self._text("CLAIM RAID LOOT", 26, BAD, (content.centerx, content.y + 18), center=True)
        free = self.player.capacity - self.player.cargo_quantity
        selected = sum(self.raid_loot_selection.values())
        damage = projected_raid_damage(pending, self.player, selected)
        self._text(
            f"{pending.raid_type.title()} raid · selected {selected}/{pending.maximum_haul} · "
            f"free hold {free} · total damage {damage}",
            12,
            INK,
            (content.x + 8, content.y + 51),
        )
        commodity_ids = sorted(pending.available_cargo)
        for index, commodity_id in enumerate(
            commodity_ids[self.overlay_scroll : self.overlay_scroll + 5]
        ):
            y = content.y + 77 + index * 27
            rect = pygame.Rect(content.x + 5, y, content.width - 10, 24)
            if commodity_id == self.raid_loot_commodity_id:
                pygame.draw.rect(self.screen, (221, 194, 137), rect, border_radius=3)
            commodity = self.world.commodities[commodity_id]
            chosen = self.raid_loot_selection.get(commodity_id, 0)
            self._add_button(
                f"raid_loot_select:{commodity_id}",
                rect,
                f"{commodity.name} · available {pending.available_cargo[commodity_id]} · "
                f"selected {chosen} · value ${commodity.base_price / 100:.2f}",
            )
        controls_y = content.bottom - 76
        self._add_button(
            "raid_loot_minus",
            pygame.Rect(content.x + 8, controls_y, 42, 30),
            "−",
        )
        self._add_button(
            "raid_loot_plus",
            pygame.Rect(content.x + 56, controls_y, 42, 30),
            "+",
        )
        self._add_button(
            "raid_loot_max",
            pygame.Rect(content.x + 104, controls_y, 62, 30),
            "Max",
        )
        self._add_button(
            "raid_loot_claim",
            pygame.Rect(content.x + 8, content.bottom - 38, 166, 32),
            "Claim Cargo",
            enabled=selected > 0,
        )
        self._add_button(
            "raid_loot_leave",
            pygame.Rect(content.x + 184, content.bottom - 38, 166, 32),
            "Leave It",
        )

    def _draw_empty_information(self, content: pygame.Rect, title: str, message: str) -> None:
        self._text(title, 30, INK, (content.centerx, content.y + 24), center=True)
        for index, line in enumerate(self._wrap(message, content.width - 40, 16, 8)):
            self._text(line, 16, INK, (content.x + 20, content.y + 78 + index * 24))

    def _draw_die(self, center: tuple[int, int], value: int, size: int, angle: float = 0) -> None:
        face = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.rect(face, (242, 226, 181), face.get_rect(), border_radius=max(4, size // 8))
        pygame.draw.rect(face, INK, face.get_rect(), 2, border_radius=max(4, size // 8))
        points = {
            1: ((0.5, 0.5),),
            2: ((0.27, 0.27), (0.73, 0.73)),
            3: ((0.27, 0.27), (0.5, 0.5), (0.73, 0.73)),
            4: ((0.27, 0.27), (0.73, 0.27), (0.27, 0.73), (0.73, 0.73)),
            5: ((0.27, 0.27), (0.73, 0.27), (0.5, 0.5), (0.27, 0.73), (0.73, 0.73)),
            6: (
                (0.27, 0.23),
                (0.73, 0.23),
                (0.27, 0.5),
                (0.73, 0.5),
                (0.27, 0.77),
                (0.73, 0.77),
            ),
        }
        for x_ratio, y_ratio in points[value]:
            pygame.draw.circle(
                face,
                INK,
                (round(x_ratio * size), round(y_ratio * size)),
                max(3, size // 12),
            )
        rotated = pygame.transform.rotozoom(face, angle, 1)
        self.screen.blit(rotated, rotated.get_rect(center=center))

    def _draw_weekly_outlook(self, content: pygame.Rect, *, reveal: bool) -> None:
        if self.world is None or self.world.weekly_outlook is None:
            return
        outlook = self.world.weekly_outlook
        rule = current_outlook_rule(self.world)
        settled = not reveal or self.weekly_reveal_elapsed >= self.settings.weekly_reveal_seconds
        if settled:
            die_one, die_two = outlook.die_one, outlook.die_two
            angle = 0.0
        else:
            frame = int(self.weekly_reveal_elapsed * 18)
            die_one = frame % 6 + 1
            die_two = (frame * 5 + 2) % 6 + 1
            angle = math.sin(self.weekly_reveal_elapsed * 18) * 18
        self._text(
            f"Week {outlook.week_index + 1}: {rule.name}",
            27,
            INK,
            (content.centerx, content.y + 25),
            center=True,
        )
        self._draw_die((content.centerx - 55, content.y + 105), die_one, 62, angle)
        self._draw_die((content.centerx + 55, content.y + 105), die_two, 62, -angle)
        if settled:
            self._text(
                f"ROLL {outlook.total}",
                22,
                INK,
                (content.centerx, content.y + 158),
                center=True,
            )
            effects = (
                f"Trade access {rule.trade_access_bp / 100:.0f}% · "
                f"opportunities {rule.opportunity_bp / 100:.0f}% · "
                f"raid risk {rule.raid_bp / 100:.0f}%."
            )
            for index, line in enumerate(
                self._wrap(f"{rule.description} {effects}", content.width - 30, 14, 4)
            ):
                self._text(line, 14, INK, (content.x + 15, content.y + 188 + index * 20))
            if reveal:
                self._add_button(
                    "continue_week",
                    pygame.Rect(content.centerx - 70, content.bottom - 35, 140, 34),
                    "Continue",
                )

    def _draw_opportunities(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        self._text("Opportunities", 28, INK, (content.centerx, content.y + 22), center=True)
        active = [
            item
            for item in self.world.opportunities.values()
            if item.status in {"offered", "accepted"} and item.known_to_player
        ]
        active.sort(
            key=lambda item: (
                item.status != "accepted",
                item.port_id != self.player.port_id,
                item.expiry_tick,
                item.id,
            )
        )
        if not active:
            self._text(
                "No current contracts or local choices.", 15, INK, (content.x + 15, content.y + 75)
            )
            return
        active_ids = [item.id for item in active]
        if self.selected_opportunity_id not in active_ids:
            self.selected_opportunity_id = active_ids[0]
        visible_count = min(5, max(3, (content.height - 250) // 29))
        self.overlay_scroll = min(
            self.overlay_scroll,
            max(0, len(active) - visible_count),
        )
        for index, item in enumerate(
            active[self.overlay_scroll : self.overlay_scroll + visible_count]
        ):
            rect = pygame.Rect(
                content.x + 5,
                content.y + 52 + index * 29,
                content.width - 10,
                26,
            )
            if item.id == self.selected_opportunity_id:
                pygame.draw.rect(self.screen, (221, 194, 137), rect, border_radius=3)
            self._add_button(
                f"opportunity_select:{item.id}",
                rect,
                f"{'MISSION · ' if item.status == 'accepted' else ''}"
                f"{item.title} · {self.world.ports[item.port_id].name}",
            )
        opportunity = self.world.opportunities[self.selected_opportunity_id]
        detail_y = content.y + 62 + visible_count * 29
        remaining = max(0, opportunity.expiry_tick - self.world.tick)
        status = (
            "ACTIVE MISSION" if opportunity.status == "accepted" else opportunity.status.upper()
        )
        detail_lines = [
            f"{opportunity.title} · {status} · {remaining}h left",
            *self._wrap(opportunity.description, content.width - 30, 13, 20),
        ]
        mission_progress: MissionProgressView | None = None
        if opportunity.kind in {"contract", "situation"}:
            mission_progress = build_mission_progress(
                self.world,
                self.world.player_id,
                opportunity,
                self.settings,
            )
            detail_lines.extend(
                self._wrap(
                    f"Objective: {mission_progress.objective_label} · "
                    f"Origin: {mission_progress.origin_name} · "
                    f"Destination: {mission_progress.destination_name}",
                    content.width - 30,
                    13,
                    10,
                )
            )
            if mission_progress.route_label:
                detail_lines.append(f"Route: {mission_progress.route_label}")
            detail_lines.append(
                f"Reward ${mission_progress.reward / 100:.2f} · "
                f"Failure penalty ${mission_progress.penalty / 100:.2f}"
            )
            if opportunity.status == "accepted":
                markers = {
                    "complete": "✓",
                    "active": "→",
                    "pending": "·",
                    "blocked": "!",
                }
                detail_lines.extend(
                    f"[{markers[step.status]}] {step.label}" for step in mission_progress.steps
                )
                detail_lines.append(f"Next: {mission_progress.action_hint}")
                if mission_progress.disabled_reason:
                    detail_lines.append(f"Action unavailable: {mission_progress.disabled_reason}.")
        if opportunity.public_effect_summary:
            detail_lines.extend(
                self._wrap(
                    f"World effect: {opportunity.public_effect_summary}",
                    content.width - 30,
                    13,
                    10,
                )
            )
        options = (
            (
                ["abandon"]
                if opportunity.objective_type
                in {"reconnaissance", "evacuation", "protection", "smuggling"}
                else [
                    "recover" if opportunity.objective_type == "salvage" else "deliver",
                    "abandon",
                ]
            )
            if opportunity.status == "accepted"
            else opportunity.options
        )
        button_y = content.bottom - 38
        self.opportunity_detail_rect = pygame.Rect(
            content.x + 5,
            detail_y,
            content.width - 10,
            max(30, button_y - detail_y - 8),
        )
        visible_detail_lines = max(1, self.opportunity_detail_rect.height // 19)
        self.opportunity_detail_max_scroll = max(
            0,
            len(detail_lines) - visible_detail_lines,
        )
        self.opportunity_detail_scroll = min(
            self.opportunity_detail_scroll,
            self.opportunity_detail_max_scroll,
        )
        old_clip = self.screen.get_clip()
        self.screen.set_clip(self.opportunity_detail_rect)
        for index, line in enumerate(
            detail_lines[
                self.opportunity_detail_scroll : self.opportunity_detail_scroll
                + visible_detail_lines
            ]
        ):
            self._text(
                line,
                16 if index == 0 and self.opportunity_detail_scroll == 0 else 13,
                INK,
                (content.x + 10, detail_y + index * 19),
            )
        self.screen.set_clip(old_clip)
        if self.opportunity_detail_max_scroll:
            self._text(
                f"Details {self.opportunity_detail_scroll + 1}–"
                f"{min(len(detail_lines), self.opportunity_detail_scroll + visible_detail_lines)}"
                f"/{len(detail_lines)} · scroll",
                11,
                INK,
                (
                    self.opportunity_detail_rect.right - 175,
                    self.opportunity_detail_rect.bottom - 14,
                ),
            )
        button_gap = 10
        width = (content.width - 20 - button_gap) // 2 if len(options) > 1 else content.width - 20
        for index, option in enumerate(options[:2]):
            at_port = self.player.port_id == (
                opportunity.destination_port_id
                if opportunity.status == "accepted"
                else opportunity.origin_port_id or opportunity.port_id
            )
            if opportunity.status == "accepted" and option in {"deliver", "recover"}:
                enabled = bool(mission_progress and mission_progress.can_complete)
            else:
                enabled = option == "abandon" or (at_port and self.player.voyage is None)
            self._add_button(
                f"opportunity:{opportunity.id}:{option}",
                pygame.Rect(
                    content.x + 10 + index * (width + button_gap),
                    button_y,
                    width,
                    32,
                ),
                option.replace("_", " ").title(),
                enabled=enabled,
            )

    def _draw_journal(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        self._text("Journal", 28, INK, (content.centerx, content.y + 22), center=True)
        entries = [
            ("STORY", progress.journal_text)
            for _, progress in sorted(self.world.story_progress.items())
            if progress.journal_text
        ]
        entries.extend(
            ("MISSION", summary) for summary in build_mission_history(self.world, self.settings)
        )
        if not entries:
            self._text(
                "No story or mission outcome has been recorded.",
                15,
                INK,
                (content.x + 15, content.y + 75),
            )
            return
        visible_count = 4
        self.overlay_scroll = min(
            self.overlay_scroll,
            max(0, len(entries) - visible_count),
        )
        y = content.y + 65
        for category, entry in entries[self.overlay_scroll : self.overlay_scroll + visible_count]:
            self._text(category, 11, ACCENT, (content.x + 10, y))
            y += 15
            for line in self._wrap(entry, content.width - 20, 13, 3):
                self._text(line, 13, INK, (content.x + 10, y))
                y += 17
            y += 6

    def _draw_encounter(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        encounter = next(
            (
                item
                for item in self.world.pending_encounters.values()
                if item.merchant_id == self.world.player_id and not item.resolved
            ),
            None,
        )
        if encounter is None:
            self.overlay = None
            return
        title = "NAVY INSPECTION" if encounter.encounter_type == "navy" else "PIRATES AHEAD"
        self._text(title, 30, BAD, (content.centerx, content.y + 30), center=True)
        route = self.world.routes[encounter.route_id]
        self._text(
            f"{'Patrol' if encounter.encounter_type == 'navy' else 'Threat'} on "
            f"{self.world.ports[route.port_a].name} route · "
            f"{'fine' if encounter.encounter_type == 'navy' else 'demand'} "
            f"${encounter.demand / 100:.2f}",
            14,
            INK,
            (content.x + 10, content.y + 78),
        )
        for index, choice in enumerate(("flee", "pay", "surrender", "bluff")):
            self._add_button(
                f"encounter:{encounter.id}:{choice}",
                pygame.Rect(
                    content.x + 20,
                    content.y + 120 + index * 42,
                    content.width - 40,
                    34,
                ),
                choice.replace("_", " ").title(),
            )

    def _draw_major_event(self, content: pygame.Rect) -> None:
        notice = self.major_event_notice
        if notice is None:
            return
        self.screen.blit(
            self.assets.image("diploma.png", (52, 52)),
            (content.centerx - 26, content.y + 12),
        )
        headline = str(notice.get("headline") or "Major Event")
        self._text(
            headline.upper(),
            26,
            BAD,
            (content.centerx, content.y + 86),
            center=True,
        )
        source = str(notice.get("information_kind") or "FACT")
        age = max(0, self.world.tick - int(notice.get("created_tick") or self.world.tick))
        default_effect = "Routes, inventories, demand, or opportunities may change."
        target_port = notice.get("port_id")
        target = (
            self.world.ports[target_port].name
            if target_port in self.world.ports
            else str(notice.get("route_id") or "regional waters")
        )
        deadline = notice.get("deadline_tick")
        duration = (
            f" Deadline or known end in {max(0, int(deadline) - self.world.tick)}h."
            if deadline is not None
            else ""
        )
        message = (
            f"{source} · {age}h old · affected: {target}. "
            f"{notice.get('body') or 'A major world condition has been reported.'} "
            f"{notice.get('public_effect_summary') or default_effect}{duration}"
        )
        for index, line in enumerate(self._wrap(message, content.width - 30, 15, 12)):
            self._text(line, 15, INK, (content.x + 15, content.y + 125 + index * 22))
        buttons = [("major_context", "View Target"), ("major_news", "Open News")]
        if notice.get("opportunity_ids"):
            buttons.append(("major_opportunity", "Opportunity"))
        buttons.append(("continue_major_event", "Acknowledge"))
        width = (content.width - 20) // len(buttons)
        for index, (name, label) in enumerate(buttons):
            self._add_button(
                name,
                pygame.Rect(content.x + 10 + index * width, content.bottom - 38, width - 5, 34),
                label,
            )

    def _draw_mission_notice(self, content: pygame.Rect) -> None:
        notice = self.mission_notice
        if notice is None:
            return
        self.screen.blit(
            self.assets.image("diploma.png", (52, 52)),
            (content.centerx - 26, content.y + 12),
        )
        color = (
            GOOD
            if notice["title"] == "MISSION COMPLETE"
            else BAD
            if notice["title"] == "MISSION FAILED"
            else ACCENT
        )
        self._text(
            str(notice["title"]),
            26,
            color,
            (content.centerx, content.y + 86),
            center=True,
        )
        for index, line in enumerate(self._wrap(str(notice["body"]), content.width - 30, 15, 10)):
            self._text(line, 15, INK, (content.x + 15, content.y + 125 + index * 22))
        buttons = [("continue_mission_notice", "Acknowledge")]
        if notice.get("active"):
            buttons.insert(0, ("mission_opportunity", "View Mission"))
        gap = 10
        button_width = (content.width - 20 - gap * (len(buttons) - 1)) // len(buttons)
        for index, (name, label) in enumerate(buttons):
            self._add_button(
                name,
                pygame.Rect(
                    content.x + 10 + index * (button_width + gap),
                    content.bottom - 38,
                    button_width,
                    34,
                ),
                label,
            )

    def _draw_upgrades(self, content: pygame.Rect) -> None:
        self._text("Ship Upgrades", 28, INK, (content.centerx, content.y + 22), center=True)
        upgrades = (
            ("expanded_hold", "Expanded Hold · $250"),
            ("fine_sails", "Fine Sails · $300"),
            ("reinforced_hull", "Reinforced Hull · $300"),
            ("crows_nest", "Crow's Nest · $200"),
        )
        for index, (upgrade_id, label) in enumerate(upgrades):
            installed = upgrade_id in self.player.ship.upgrades
            self._add_button(
                f"upgrade:{upgrade_id}",
                pygame.Rect(
                    content.x + 20,
                    content.y + 70 + index * 48,
                    content.width - 40,
                    36,
                ),
                f"{label}{' · Installed' if installed else ''}",
                enabled=not installed,
            )

    def _draw_news(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        self._text("Recent News", 30, INK, (content.centerx, content.y + 20), center=True)
        items = build_news_items(self.world, self.settings)
        if not items:
            self._text("No notable events yet.", 16, INK, (content.x + 20, content.y + 80))
            return
        visible_count = 6
        self.overlay_scroll = min(self.overlay_scroll, max(0, len(items) - visible_count))
        for index, item in enumerate(
            items[self.overlay_scroll : self.overlay_scroll + visible_count]
        ):
            y = content.y + 55 + index * 38
            rect = pygame.Rect(content.x + 5, y, content.width - 10, 34)
            pygame.draw.rect(self.screen, (231, 210, 160), rect, border_radius=3)
            pygame.draw.rect(self.screen, INK, rect, 1, border_radius=3)
            self.buttons[f"news:{item.latest_sequence}"] = Button(
                f"news:{item.latest_sequence}",
                rect,
                enabled=item.port_id is not None,
            )
            day = item.tick // self.settings.ticks_per_day + 1
            category_color = BAD if item.category == "SHORTAGE" else INK
            self._text(
                f"{item.information_kind} · {item.category} · D{day}",
                11,
                category_color,
                (rect.x + 5, rect.y + 3),
            )
            line = self._wrap(item.text, content.width - 25, 12, 1)
            if line:
                self._text(line[0], 12, INK, (rect.x + 5, rect.y + 17))

    def _draw_history(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        commodity = self.world.commodities[self.selected_commodity_id]
        if self.player.port_id == self.selected_port_id:
            samples = self.world.price_history[self.selected_port_id][self.selected_commodity_id]
        else:
            report = known_market_report(
                self.world,
                self.selected_port_id,
                self.selected_commodity_id,
            )
            samples = (
                [
                    MarketSample(
                        report.created_tick,
                        report.quantity,
                        report.bid_price,
                        report.ask_price,
                    )
                ]
                if report
                else []
            )
        self._text(
            f"{commodity.name} Price History",
            25,
            INK,
            (content.centerx, content.y + 24),
            center=True,
        )
        chart = pygame.Rect(content.x + 10, content.y + 70, content.width - 20, 180)
        pygame.draw.rect(self.screen, (231, 210, 160), chart)
        pygame.draw.rect(self.screen, INK, chart, 1)
        values = [value for sample in samples for value in (sample.bid_price, sample.ask_price)]
        if len(samples) >= 2 and values:
            low, high = min(values), max(values)
            span = max(1, high - low)
            points_bid = []
            points_ask = []
            for index, sample in enumerate(samples):
                x = chart.x + index * chart.width // (len(samples) - 1)
                bid_y = chart.bottom - (sample.bid_price - low) * chart.height // span
                ask_y = chart.bottom - (sample.ask_price - low) * chart.height // span
                points_bid.append((x, bid_y))
                points_ask.append((x, ask_y))
            pygame.draw.lines(self.screen, GOOD, False, points_bid, 2)
            pygame.draw.lines(self.screen, BAD, False, points_ask, 2)
        self._text("Bid", 14, GOOD, (chart.x, chart.bottom + 10))
        self._text("Ask", 14, BAD, (chart.x + 55, chart.bottom + 10))
        inventory = self.world.ports[self.selected_port_id].inventories[self.selected_commodity_id]
        known_quantity = samples[-1].quantity if samples else inventory.target
        status = market_status(
            Inventory(known_quantity, inventory.target, inventory.capacity, 0, 0),
            self.settings,
        )
        self._text(
            f"{status} · {price_trend(samples)}",
            14,
            INK,
            (chart.x + 125, chart.bottom + 10),
        )

    def draw(self) -> None:
        self.buttons = {}
        if self.mode == "title":
            self._draw_title()
        else:
            self._draw_game()
        self._draw_overlay()
        pygame.display.flip()

    def _handle_title_action(self, name: str) -> None:
        if name == "new":
            self.new_game()
        elif name == "continue":
            self.continue_game()
        elif name == "credits":
            self.overlay = "credits"
        elif name == "quit":
            self.running = False

    def _execute_trade(self, buying: bool) -> None:
        if self.world is None or self.engine is None or self.player.port_id is None:
            return
        command_type = BuyCargo if buying else SellCargo
        events = self.engine.execute_commands(
            [
                command_type(
                    self.player.id,
                    self.player.port_id,
                    self.selected_commodity_id,
                    self.trade_quantity,
                )
            ]
        )
        rejected = next((event for event in events if event.event_type == "CommandRejected"), None)
        disrupted = next((event for event in events if event.event_type == "TradeDisrupted"), None)
        if rejected:
            self.status_message = rejected.payload["reason"]
        elif disrupted:
            if disrupted.payload.get("cause") == "local_raid_alert":
                self.status_message = (
                    "Local raid alert: this port refuses your trades today; "
                    "your cargo remains in the hold."
                )
            else:
                rule = current_outlook_rule(self.world)
                self.status_message = (
                    f"Weekly market disruption under {rule.name}; this market "
                    "is closed to you today."
                )
        else:
            verb = "Bought" if buying else "Sold"
            self.status_message = (
                f"{verb} {self.trade_quantity} "
                f"{self.world.commodities[self.selected_commodity_id].name}."
            )

    def _execute_sail(self) -> None:
        if (
            self.world is None
            or self.engine is None
            or self.player.port_id is None
            or self.selected_port_id == self.player.port_id
        ):
            return
        route = self._route_between(self.player.port_id, self.selected_port_id)
        if route is None:
            return
        events = self.engine.execute_commands(
            [BeginVoyage(self.player.id, route.id, self.selected_port_id)]
        )
        rejected = next((event for event in events if event.event_type == "CommandRejected"), None)
        if rejected:
            self.status_message = rejected.payload["reason"]
        else:
            self.status_message = f"Sailing to {self.world.ports[self.selected_port_id].name}."
            self.time.paused = False
            self.camera.follow(actor_map_position(self.world, self.player))

    def _execute_service(self, service: str) -> None:
        if self.world is None or self.engine is None or self.player.port_id is None:
            return
        port_id = self.player.port_id
        if service == "buy_provision":
            command = BuyProvisions(self.player.id, port_id, 1)
        elif service == "repair_ship":
            command = RepairShip(self.player.id, port_id, 10)
        elif service == "pay_wages":
            command = PayWages(self.player.id, port_id)
        else:
            command = PurchaseUpgrade(self.player.id, port_id, service)
        events = self.engine.execute_commands([command])
        rejected = next((event for event in events if event.event_type == "CommandRejected"), None)
        self.status_message = (
            rejected.payload["reason"]
            if rejected
            else f"{service.replace('_', ' ').title()} completed."
        )

    def _execute_player_raid(self, *, port: bool) -> None:
        if self.world is None or self.engine is None:
            return
        if port:
            if self.player.port_id is None:
                return
            command: RaidPort | RaidShip = RaidPort(
                self.player.id,
                self.player.port_id,
            )
            explosion_position = actor_map_position(self.world, self.player)
        else:
            target = self._raidable_ship()
            if target is None:
                self.status_message = "No merchant ship is within raiding range."
                return
            command = RaidShip(self.player.id, target.id)
            explosion_position = actor_map_position(self.world, target)
        events = self.engine.execute_commands([command])
        rejected = next(
            (event for event in events if event.event_type == "CommandRejected"),
            None,
        )
        result = next(
            (event for event in events if event.event_type == "PlayerRaidResolved"),
            None,
        )
        if rejected:
            self.status_message = rejected.payload["reason"]
            return
        if result:
            self._start_explosion(explosion_position)
            outcome = "succeeded" if result.payload["success"] else "failed"
            raid_id = result.payload.get("raid_id")
            if result.payload["success"] and raid_id:
                self.status_message = (
                    f"Raid {outcome}. Choose up to {result.payload['maximum_haul']} "
                    "units from the captured manifest."
                )
                self._open_raid_loot(raid_id)
            else:
                self.status_message = f"Raid {outcome}. The ship took damage."

    def _handle_game_action(self, name: str) -> None:
        if self.world is None:
            return
        if name.startswith("tab:"):
            self._activate_tab(name.split(":", 1)[1])
        elif name == "open_hold":
            self._activate_tab("hold")
        elif name == "open_stolen_hold":
            stolen_ids = sorted(
                item for item, quantity in self.player.stolen_cargo.items() if quantity > 0
            )
            if stolen_ids:
                self.selected_commodity_id = stolen_ids[0]
            self._activate_tab("hold")
        elif name.startswith(("commodity:", "holding:")):
            self.selected_commodity_id = name.split(":", 1)[1]
            self.trade_quantity = 1
        elif name == "play_pause":
            self.time.paused = not self.time.paused
        elif name == "slower":
            self.time.slower()
        elif name == "faster":
            self.time.faster()
        elif name == "minus":
            self.trade_quantity = max(1, self.trade_quantity - 1)
        elif name == "plus":
            self.trade_quantity += 1
        elif name == "max_buy":
            buy_limit, _ = self._trade_limits()
            self.trade_quantity = max(1, buy_limit)
        elif name == "max_sell":
            _, sell_max = self._trade_limits()
            self.trade_quantity = max(1, sell_max)
        elif name == "max_fence":
            port = self.world.ports["blackwater_cay"]
            inventory = port.inventories[self.selected_commodity_id]
            price = inventory.bid_price * self.settings.fence_price_bp // 10_000
            maximum = min(
                self.player.stolen_cargo.get(self.selected_commodity_id, 0),
                inventory.capacity - inventory.quantity,
                port.treasury // price if price else 0,
            )
            self.trade_quantity = max(1, maximum)
        elif name == "buy":
            self._execute_trade(True)
        elif name == "sell":
            self._execute_trade(False)
        elif name == "sail":
            self._execute_sail()
        elif name in {"buy_provision", "repair_ship", "pay_wages"}:
            self._execute_service(name)
        elif name == "upgrades":
            self.overlay = "upgrades"
        elif name == "raid_ship":
            self._execute_player_raid(port=False)
        elif name == "raid_port":
            self._execute_player_raid(port=True)
        elif name == "fence_stolen":
            commodity_id = self.selected_commodity_id
            if commodity_id and self.engine is not None and self.player.port_id is not None:
                events = self.engine.execute_commands(
                    [
                        FenceStolenCargo(
                            self.player.id,
                            self.player.port_id,
                            commodity_id,
                            self.trade_quantity,
                        )
                    ]
                )
                rejected = next(
                    (event for event in events if event.event_type == "CommandRejected"),
                    None,
                )
                self.status_message = (
                    rejected.payload["reason"]
                    if rejected
                    else f"Fenced {self.trade_quantity} "
                    f"{self.world.commodities[commodity_id].name}."
                )
        elif name in {"lay_low", "pay_officials"}:
            if self.engine is not None and self.player.port_id is not None:
                method = "lay_low" if name == "lay_low" else "officials"
                self.engine.execute_commands(
                    [PayOffOfficials(self.player.id, self.player.port_id, 20, method)]
                )
        elif name == "save":
            self.save()
        elif name == "load":
            self.continue_game()
        elif name == "news":
            self._open_news()
        elif name == "opportunities":
            self.time.paused = True
            self.time.accumulator = 0
            self.overlay_scroll = 0
            self.opportunity_detail_scroll = 0
            self.overlay = "opportunities"
        elif name == "journal":
            self.overlay_scroll = 0
            self.overlay = "journal"
        elif name == "history":
            self.overlay_scroll = 0
            self.overlay = "history"
        elif name == "weekly_heading":
            self.overlay = "weekly_info"
        elif name == "toggle_inspector":
            self.inspector_collapsed = not self.inspector_collapsed
            self.layout = UILayout.build(
                self.screen.get_size(),
                inspector_collapsed=self.inspector_collapsed,
            )
            self.camera.set_viewport(self.layout.map_viewport)
            self._map_cache = None
        elif name == "menu":
            self.mode = "title"
            self.time.paused = True

    def _handle_news_action(self, name: str) -> None:
        if self.world is None:
            return
        sequence = int(name.split(":", 1)[1])
        item = next(
            (
                candidate
                for candidate in build_news_items(self.world, self.settings)
                if candidate.latest_sequence == sequence
            ),
            None,
        )
        if item is None or item.port_id is None:
            return
        self.selected_port_id = item.port_id
        if item.commodity_id is not None:
            self.selected_commodity_id = item.commodity_id
            self._activate_tab("market")
        else:
            self._activate_tab("port")
        self._close_news()

    def _handle_click(self, position: tuple[int, int]) -> None:
        if self.overlay is not None:
            for button in reversed(tuple(self.buttons.values())):
                if not button.enabled or not button.rect.collidepoint(position):
                    continue
                if button.name == "close_overlay":
                    if self.overlay == "news":
                        self._close_news()
                    else:
                        self.overlay = None
                elif button.name.startswith("news:"):
                    self._handle_news_action(button.name)
                elif button.name == "continue_week":
                    self._dismiss_weekly_reveal()
                elif button.name == "continue_major_event":
                    self._dismiss_major_event()
                elif button.name == "major_news":
                    should_resume = self.resume_after_major_event
                    self.major_event_notice = None
                    self.resume_after_major_event = False
                    self._open_news(resume_running=should_resume)
                elif button.name == "major_opportunity":
                    notice = self.major_event_notice or {}
                    ids = notice.get("opportunity_ids") or []
                    self.selected_opportunity_id = ids[0] if ids else None
                    self.opportunity_detail_scroll = 0
                    self.major_event_notice = None
                    self.overlay = "opportunities"
                    self.resume_after_major_event = False
                    self.time.paused = True
                    self.time.accumulator = 0
                elif button.name == "major_context":
                    notice = self.major_event_notice or {}
                    port_id = notice.get("port_id")
                    if port_id in self.world.ports:
                        self.selected_port_id = port_id
                    commodity_id = notice.get("commodity_id")
                    if commodity_id in self.world.commodities:
                        self.selected_commodity_id = commodity_id
                        self._activate_tab("market")
                    else:
                        self._activate_tab("port")
                    self.major_event_notice = None
                    self.overlay = None
                    self.resume_after_major_event = False
                    self.time.paused = True
                    self.time.accumulator = 0
                elif button.name == "continue_mission_notice":
                    self._dismiss_mission_notice()
                elif button.name == "mission_opportunity":
                    notice = self.mission_notice or {}
                    self.selected_opportunity_id = notice.get("opportunity_id")
                    self.mission_notice = None
                    self.overlay = "opportunities"
                    self.time.paused = True
                    self.time.accumulator = 0
                elif button.name.startswith("upgrade:"):
                    self._execute_service(button.name.split(":", 1)[1])
                elif button.name.startswith("encounter:"):
                    _, encounter_id, choice = button.name.split(":", 2)
                    if self.engine is not None:
                        self.engine.execute_commands(
                            [ResolveEncounter(self.player.id, encounter_id, choice)]
                        )
                    self.overlay = None
                elif button.name.startswith("opportunity:"):
                    _, opportunity_id, option = button.name.split(":", 2)
                    if self.engine is not None:
                        events = self.engine.execute_commands(
                            [RespondToOpportunity(self.player.id, opportunity_id, option)]
                        )
                        rejected = next(
                            (event for event in events if event.event_type == "CommandRejected"),
                            None,
                        )
                        self.status_message = (
                            rejected.payload["reason"]
                            if rejected
                            else "Your decision has been recorded in the Journal."
                        )
                    self.overlay = None
                    if self.engine is not None:
                        for event in events:
                            self._queue_mission_notice(event)
                    self._show_next_mission_notice()
                elif button.name.startswith("opportunity_select:"):
                    self.selected_opportunity_id = button.name.split(":", 1)[1]
                    self.opportunity_detail_scroll = 0
                elif button.name.startswith("raid_loot_select:"):
                    self.raid_loot_commodity_id = button.name.split(":", 1)[1]
                elif button.name in {
                    "raid_loot_minus",
                    "raid_loot_plus",
                    "raid_loot_max",
                }:
                    pending = self._pending_raid_loot()
                    commodity_id = self.raid_loot_commodity_id
                    if pending is not None and commodity_id is not None:
                        current = self.raid_loot_selection.get(commodity_id, 0)
                        other = sum(self.raid_loot_selection.values()) - current
                        maximum = min(
                            pending.available_cargo.get(commodity_id, 0),
                            pending.maximum_haul - other,
                            self.player.capacity - self.player.cargo_quantity - other,
                        )
                        if button.name == "raid_loot_minus":
                            current = max(0, current - 1)
                        elif button.name == "raid_loot_plus":
                            current = min(maximum, current + 1)
                        else:
                            current = maximum
                        self.raid_loot_selection[commodity_id] = current
                elif button.name in {"raid_loot_claim", "raid_loot_leave"}:
                    pending = self._pending_raid_loot()
                    if pending is not None and self.engine is not None:
                        command = (
                            ClaimRaidLoot(
                                self.player.id,
                                pending.id,
                                dict(self.raid_loot_selection),
                            )
                            if button.name == "raid_loot_claim"
                            else AbandonRaidLoot(self.player.id, pending.id)
                        )
                        events = self.engine.execute_commands([command])
                        rejected = next(
                            (event for event in events if event.event_type == "CommandRejected"),
                            None,
                        )
                        if rejected:
                            self.status_message = rejected.payload["reason"]
                        else:
                            quantity = sum(self.raid_loot_selection.values())
                            self.status_message = (
                                f"Claimed {quantity} stolen cargo."
                                if button.name == "raid_loot_claim"
                                else "Left the raid cargo behind."
                            )
                            self.overlay = None
                return
            return
        for button in reversed(tuple(self.buttons.values())):
            if button.enabled and button.rect.collidepoint(position):
                if self.mode == "title":
                    self._handle_title_action(button.name)
                else:
                    self._handle_game_action(button.name)
                return
        if (
            self.mode == "game"
            and self.world is not None
            and self.layout.map_viewport.collidepoint(position)
        ):
            for port in self.world.ports.values():
                distance = math.dist(
                    position,
                    self.camera.world_to_screen((port.map_x, port.map_y)),
                )
                if distance <= 16:
                    self.selected_port_id = port.id
                    return

    def _handle_scroll(self, wheel_y: int, position: tuple[int, int]) -> None:
        if self.world is None or wheel_y == 0:
            return
        if self.overlay == "news":
            maximum = max(0, len(build_news_items(self.world, self.settings)) - 6)
            self.overlay_scroll = min(maximum, max(0, self.overlay_scroll - wheel_y))
            return
        if self.overlay == "opportunities":
            if self.opportunity_detail_rect.collidepoint(position):
                self.opportunity_detail_scroll = min(
                    self.opportunity_detail_max_scroll,
                    max(0, self.opportunity_detail_scroll - wheel_y),
                )
                return
            active_count = sum(
                item.status in {"offered", "accepted"} and item.known_to_player
                for item in self.world.opportunities.values()
            )
            maximum = max(0, active_count - 4)
            self.overlay_scroll = min(
                maximum,
                max(0, self.overlay_scroll - wheel_y),
            )
            return
        if self.overlay == "journal":
            story_count = sum(
                bool(progress.journal_text) for progress in self.world.story_progress.values()
            )
            mission_count = len(build_mission_history(self.world, self.settings))
            maximum = max(0, story_count + mission_count - 4)
            self.overlay_scroll = min(
                maximum,
                max(0, self.overlay_scroll - wheel_y),
            )
            return
        if self.overlay == "raid_loot":
            pending = self._pending_raid_loot()
            maximum = max(0, len(pending.available_cargo) - 5) if pending is not None else 0
            self.overlay_scroll = min(
                maximum,
                max(0, self.overlay_scroll - wheel_y),
            )
            return
        if self.overlay is not None or not self.layout.inspector.collidepoint(position):
            return
        if self.active_sidebar_tab == "market":
            maximum = max(0, len(self.world.commodities) - 5)
            self.row_offsets["market"] = min(
                maximum,
                max(0, self.row_offsets["market"] - wheel_y),
            )
        elif self.active_sidebar_tab == "hold":
            maximum = max(0, len(self._holding_views()) - 4)
            self.row_offsets["hold"] = min(
                maximum,
                max(0, self.row_offsets["hold"] - wheel_y),
            )

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if (
                self.mode == "game"
                and self.overlay is None
                and self.layout.map_viewport.collidepoint(event.pos)
            ):
                self._map_dragging = True
                self._map_drag_origin = event.pos
                self._map_drag_distance = 0.0
            else:
                self._handle_click(event.pos)
        elif event.type == pygame.MOUSEMOTION and self._map_dragging:
            dx, dy = event.rel
            self._map_drag_distance += math.hypot(dx, dy)
            self.camera.pan(dx, dy)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._map_dragging:
            self._map_dragging = False
            if self._map_drag_distance < 5:
                self._handle_click(event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            position = pygame.mouse.get_pos()
            if (
                self.mode == "game"
                and self.overlay is None
                and self.layout.map_viewport.collidepoint(position)
            ):
                self.camera.zoom_at(1.15 if event.y > 0 else 1 / 1.15, position)
                self._map_cache = None
            else:
                self._handle_scroll(event.y, position)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_m and self.audio_available:
                muted = pygame.mixer.music.get_volume() > 0
                pygame.mixer.music.set_volume(0 if muted else self.settings.music_volume)
            elif event.key == pygame.K_ESCAPE:
                if self.overlay in {
                    "weekly_roll",
                    "encounter",
                    "major_event",
                    "mission_notice",
                    "raid_loot",
                }:
                    return
                if self.overlay == "news":
                    self._close_news()
                elif self.overlay:
                    self.overlay = None
                elif self.mode == "game":
                    self.mode = "title"
                    self.time.paused = True
            elif (
                self.mode == "game"
                and self.overlay == "weekly_roll"
                and event.key in {pygame.K_SPACE, pygame.K_RETURN}
                and self.weekly_reveal_elapsed >= self.settings.weekly_reveal_seconds
            ):
                self._dismiss_weekly_reveal()
            elif self.mode == "game" and self.overlay is None and event.key == pygame.K_SPACE:
                self.time.paused = not self.time.paused
            elif self.mode == "game" and event.key == pygame.K_LEFT:
                self.time.slower()
            elif self.mode == "game" and event.key == pygame.K_RIGHT:
                self.time.faster()
            elif self.mode == "game" and event.key == pygame.K_f and self.world is not None:
                self.camera.follow(actor_map_position(self.world, self.player))
            elif self.mode == "game" and event.key == pygame.K_HOME:
                self._frame_port_network()

    def update(self, elapsed_seconds: float) -> None:
        if self.mode != "game" or self.engine is None or self.world is None:
            return
        if self.camera.following:
            focus = actor_map_position(self.world, self.player)
            if self.player.voyage is not None:
                destination = self.world.ports[self.player.voyage.destination_id]
                focus = (
                    (focus[0] + destination.map_x) / 2,
                    (focus[1] + destination.map_y) / 2,
                )
            self.camera.follow(focus, min(1.0, elapsed_seconds * 4))
        if self.explosion_elapsed is not None:
            self.explosion_elapsed += elapsed_seconds
            if self.explosion_elapsed >= self.settings.explosion_animation_seconds:
                self.explosion_elapsed = None
        self._show_next_major_event()
        self._show_next_mission_notice()
        if self.overlay == "weekly_roll":
            self.weekly_reveal_elapsed += elapsed_seconds
            return
        for _ in range(self.time.due_ticks(elapsed_seconds, self.settings.max_ticks_per_frame)):
            events = self.engine.step()
            for event in events:
                self._queue_mission_notice(event)
                if event.event_type == "InformationReportReceived":
                    self._queue_major_event(event.payload)
                elif (
                    event.event_type == "ReportDelivered"
                    and event.payload.get("report_type") == "information"
                    and event.payload.get("destination_port_id") == self.player.port_id
                ):
                    self._queue_major_event(event.payload)
            weekly = next(
                (event for event in events if event.event_type == "WeeklyOutlookRolled"),
                None,
            )
            if weekly:
                self._begin_weekly_reveal()
                break
            encounter = next(
                (
                    event
                    for event in events
                    if event.event_type in {"PirateEncounterStarted", "NavyEncounterStarted"}
                    and event.payload["merchant_id"] == self.world.player_id
                ),
                None,
            )
            if encounter:
                if self.overlay == "major_event" and self.major_event_notice is not None:
                    self.major_event_queue.insert(0, self.major_event_notice)
                    self.major_event_notice = None
                if self.overlay == "mission_notice" and self.mission_notice is not None:
                    self.mission_notice_queue.insert(0, self.mission_notice)
                    self.mission_notice = None
                self.time.paused = True
                self.time.accumulator = 0
                if encounter.event_type == "PirateEncounterStarted":
                    self._start_explosion(actor_map_position(self.world, self.player))
                self.overlay = "encounter"
                break
            arrival = next(
                (
                    event
                    for event in events
                    if event.event_type == "ShipArrived"
                    and event.payload["merchant_id"] == self.world.player_id
                ),
                None,
            )
            if arrival:
                self.time.paused = True
                self.time.accumulator = 0
                self.selected_port_id = arrival.payload["destination_id"]
                self.status_message = f"Arrived at {self.world.ports[self.selected_port_id].name}."
                break
            if self.overlay in {"major_event", "mission_notice"}:
                break

    def shutdown(self) -> None:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        pygame.quit()

    def run(self, *, maximum_frames: int | None = None) -> int:
        frames = 0
        try:
            while self.running and (maximum_frames is None or frames < maximum_frames):
                elapsed = self.clock.tick(self.settings.render_fps) / 1000
                for event in pygame.event.get():
                    self.handle_event(event)
                self.update(elapsed)
                self.draw()
                frames += 1
        finally:
            self.shutdown()
        return 0


def run_ui(
    settings: SimulationSettings = SETTINGS,
    *,
    maximum_frames: int | None = None,
    enable_audio: bool = True,
) -> int:
    """Initialize and run the pygame interface."""
    app = PirateTradingUI(settings, enable_audio=enable_audio)
    return app.run(maximum_frames=maximum_frames)
