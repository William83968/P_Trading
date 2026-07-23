"""Pygame presentation for the Milestone 1 playable trading slice."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import pygame

from pirate_trading.models import (
    BeginVoyage,
    BuyCargo,
    Inventory,
    MarketSample,
    Merchant,
    SellCargo,
    WorldState,
)
from pirate_trading.persistence import SaveError, load_game, save_game
from pirate_trading.settings import SETTINGS, SimulationSettings
from pirate_trading.simulation import SimulationEngine, load_world

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


SidebarTab = Literal["market", "hold", "port"]


@dataclass(frozen=True, slots=True)
class CargoHoldingView:
    """Presentation-only valuation of one commodity in the player's hold."""

    commodity_id: str
    name: str
    quantity: int
    cost_basis: int
    average_unit_cost: float
    bid_price: int
    estimated_value: int
    estimated_profit: int
    sellable_quantity: int


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
            player.cargo.get(commodity_id, 0),
            inventory.capacity - inventory.quantity,
            port_treasury // inventory.bid_price,
        ),
    )


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
        inventory = port.inventories[commodity_id]
        estimated_value = quantity * inventory.bid_price
        holdings.append(
            CargoHoldingView(
                commodity_id=commodity_id,
                name=world.commodities[commodity_id].name,
                quantity=quantity,
                cost_basis=cost_basis,
                average_unit_cost=cost_basis / quantity,
                bid_price=inventory.bid_price,
                estimated_value=estimated_value,
                estimated_profit=estimated_value - cost_basis,
                sellable_quantity=(
                    sell_limit(player, commodity_id, port.treasury, inventory)
                    if player.port_id == selected_port_id and player.voyage is None
                    else 0
                ),
            )
        )
    return tuple(holdings)


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
    progress = 1.0 - actor.voyage.remaining_ticks / route.travel_ticks
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
        if enable_audio:
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(settings.assets_directory / "The Sailor's Tale.mp3")
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
        self.news_read_through_sequence = -1
        self.status_message = ""
        self.buttons: dict[str, Button] = {}
        self.time = TimeController(settings.time_rates, settings.initial_time_rate_index)
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
        self.trade_quantity = 1
        if self.world is None or not mark_existing_news_read:
            self.news_read_through_sequence = -1
        else:
            self.news_read_through_sequence = max(
                (event.sequence for event in self.world.recent_events),
                default=-1,
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
        self.screen.blit(self.assets.image("atlantic.png", (1000, 706)), (140, 7))
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((15, 10, 5, 145))
        self.screen.blit(shade, (0, 0))
        paper = self.assets.image("message_paper.png")
        paper_rect = paper.get_rect(center=self.screen.get_rect().center)
        self.screen.blit(paper, paper_rect)
        self._text("Pirate Trading", 42, INK, (640, 235), center=True, decorative=True)
        self._text("A Living-World Trading Sandbox", 16, INK, (640, 275), center=True)
        self._add_button("new", pygame.Rect(540, 315, 200, 42), "New Game")
        self._add_button(
            "continue",
            pygame.Rect(540, 365, 200, 42),
            "Continue",
            enabled=self.settings.quick_save_path.exists(),
        )
        self._add_button("credits", pygame.Rect(540, 415, 200, 42), "Credits")
        self._add_button("quit", pygame.Rect(540, 465, 200, 42), "Quit")

    def _draw_routes_and_ports(self) -> None:
        if self.world is None:
            return
        offset_y = 7
        for route in self.world.routes.values():
            first = self.world.ports[route.port_a]
            second = self.world.ports[route.port_b]
            pygame.draw.line(
                self.screen,
                ROUTE,
                (first.map_x, first.map_y + offset_y),
                (second.map_x, second.map_y + offset_y),
                3,
            )
        for port in self.world.ports.values():
            point = (port.map_x, port.map_y + offset_y)
            selected = port.id == self.selected_port_id
            pygame.draw.circle(self.screen, ACCENT if selected else PANEL, point, 11)
            pygame.draw.circle(self.screen, TEXT, point, 11, 2)
            self._text(port.name, 14, INK, (point[0] + 14, point[1] - 9))

        for merchant in self.world.merchants.values():
            x, y = actor_map_position(self.world, merchant)
            if merchant.controller == "player":
                marker = self.assets.image("player-marker.png", (48, 48))
                self.screen.blit(marker, marker.get_rect(center=(round(x), round(y + offset_y))))
            else:
                pygame.draw.circle(
                    self.screen,
                    ACCENT,
                    (round(x), round(y + offset_y)),
                    4,
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
            if self.player.cargo.get(self.selected_commodity_id, 0) <= 0:
                return "You do not hold this commodity."
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
        for index, (tab, label) in enumerate(
            (("market", "Market"), ("hold", "Hold"), ("port", "Port"))
        ):
            rect = pygame.Rect(sidebar_x + 5 + index * 91, 164, 88, 32)
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
            y = 246 + index * 31
            rect = pygame.Rect(sidebar_x + 6, y, 268, 29)
            if commodity_id == self.selected_commodity_id:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=4)
            else:
                pygame.draw.rect(self.screen, PANEL_LIGHT, rect, 1, border_radius=4)
            self.buttons[f"commodity:{commodity_id}"] = Button(f"commodity:{commodity_id}", rect)
            status = market_status(inventory, self.settings)
            status_color = BAD if status == "Shortage" else GOOD if status == "Surplus" else TEXT
            trend = price_trend(self.world.price_history[port.id][commodity_id])
            self._text(commodity.name, 13, TEXT, (sidebar_x + 10, y + 6))
            self._text(str(inventory.quantity), 13, status_color, (sidebar_x + 108, y + 6))
            self._text(f"{inventory.bid_price / 100:.2f}", 13, TEXT, (sidebar_x + 163, y + 6))
            self._text(
                f"{inventory.ask_price / 100:.2f}{trend}",
                13,
                TEXT,
                (sidebar_x + 214, y + 6),
            )
        self.screen.set_clip(old_clip)

        commodity = self.world.commodities[self.selected_commodity_id]
        held = self.player.cargo.get(self.selected_commodity_id, 0)
        self._text(f"{commodity.name} · Held {held}", 14, TEXT, (sidebar_x + 10, 409))
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
        if buy_limit == 0 and sell_max == 0:
            reason = self._trade_unavailable_reason(buying=held == 0)
            self._text(reason, 13, MUTED, (sidebar_x + 10, 558))

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
            self._text(f"Qty {holding.quantity}", 14, TEXT, (sidebar_x + 150, y + 8))
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
        self._text(
            f"{self.world.ports[self.selected_port_id].name} bid: "
            f"${holding.bid_price / 100:,.2f} · Live · 0h",
            13,
            TEXT,
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
            f"Sellable here: {holding.sellable_quantity}",
            13,
            MUTED,
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
        if reason:
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
        for inventory in port.inventories.values():
            status_counts[market_status(inventory, self.settings)] += 1
        self._text("Market conditions", 15, TEXT, (sidebar_x + 10, 263))
        self._text(
            f"SHORTAGE {status_counts['Shortage']}  ·  BALANCED "
            f"{status_counts['Balanced']}  ·  SURPLUS {status_counts['Surplus']}",
            12,
            MUTED,
            (sidebar_x + 10, 287),
        )
        self._text("Connected routes", 15, TEXT, (sidebar_x + 10, 323))
        y = 348
        for route in sorted(self.world.routes.values(), key=lambda item: item.id):
            if port.id not in (route.port_a, route.port_b):
                continue
            destination = self.world.ports[route.other_end(port.id)]
            self._text(destination.name, 14, TEXT, (sidebar_x + 10, y))
            self._text(
                f"{route.travel_ticks}h · ${route.operating_cost / 100:.2f}",
                13,
                MUTED,
                (sidebar_x + 145, y + 1),
            )
            y += 27

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
            pygame.Rect(sidebar_x + 10, 520, 260, 38),
            sail_label,
            enabled=sail_enabled,
        )
        if reason:
            self._text(reason, 13, MUTED, (sidebar_x + 10, 566))

    def _draw_sidebar(self) -> None:
        if self.world is None:
            return
        sidebar_x = self.settings.map_width
        pygame.draw.rect(
            self.screen,
            PANEL,
            pygame.Rect(
                sidebar_x,
                0,
                self.settings.window_width - sidebar_x,
                self.settings.window_height,
            ),
        )
        day = self.world.tick // self.settings.ticks_per_day + 1
        hour = self.world.tick % self.settings.ticks_per_day
        self._text(f"Day {day}, {hour:02d}:00", 18, TEXT, (sidebar_x + 10, 12))
        self.screen.blit(self.assets.image("dollar.png", (24, 24)), (sidebar_x + 10, 42))
        self._text(f"${self.player.cash / 100:,.2f}", 18, TEXT, (sidebar_x + 42, 43))
        self._add_button(
            "open_hold",
            pygame.Rect(sidebar_x + 10, 70, 198, 26),
            f"Cargo {self.player.cargo_quantity}/{self.player.capacity}",
        )
        location = (
            self.world.ports[self.player.port_id].name
            if self.player.port_id
            else f"To {self.world.ports[self.player.voyage.destination_id].name} "
            f"({self.player.voyage.remaining_ticks}h)"
        )
        self._text(location, 14, MUTED, (sidebar_x + 10, 96))

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
        if self.active_sidebar_tab == "market":
            self._draw_market(sidebar_x)
        elif self.active_sidebar_tab == "hold":
            self._draw_hold(sidebar_x)
        else:
            self._draw_port(sidebar_x)

        unread = sum(
            item.latest_sequence > self.news_read_through_sequence
            for item in build_news_items(self.world, self.settings)
        )
        news_label = f"News {unread}" if unread else "News"
        self._add_button("news", pygame.Rect(sidebar_x + 10, 590, 82, 32), news_label)
        self._add_button(
            "opportunities",
            pygame.Rect(sidebar_x + 98, 590, 84, 32),
            "Offers",
        )
        self._add_button("journal", pygame.Rect(sidebar_x + 188, 590, 82, 32), "Journal")
        self._add_button("save", pygame.Rect(sidebar_x + 10, 630, 82, 32), "Save")
        self._add_button("load", pygame.Rect(sidebar_x + 98, 630, 84, 32), "Load")
        self._add_button("menu", pygame.Rect(sidebar_x + 188, 630, 82, 32), "Menu")
        for line_index, line in enumerate(
            self._wrap(self.status_message, 260, 14, maximum_lines=2)
        ):
            self._text(line, 14, MUTED, (sidebar_x + 10, 674 + line_index * 18))

    def _route_between(self, first: str, second: str) -> Any | None:
        if self.world is None:
            return None
        for route in self.world.routes.values():
            if {route.port_a, route.port_b} == {first, second}:
                return route
        return None

    def _draw_game(self) -> None:
        self.screen.fill(BACKGROUND)
        self.screen.blit(self.assets.image("atlantic.png", (1000, 706)), (0, 7))
        self._draw_routes_and_ports()
        self._draw_sidebar()

    def _draw_overlay(self) -> None:
        if self.overlay is None:
            return
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((15, 10, 5, 150))
        self.screen.blit(shade, (0, 0))
        paper = self.assets.image("message_paper.png")
        paper_rect = paper.get_rect(center=self.screen.get_rect().center)
        self.screen.blit(paper, paper_rect)
        content = pygame.Rect(paper_rect.x + 70, paper_rect.y + 85, 360, 300)
        self._add_button(
            "close_overlay",
            pygame.Rect(content.right - 48, content.y, 42, 30),
            "Close",
        )
        if self.overlay == "credits":
            self._text("Credits", 30, INK, (content.centerx, content.y + 20), center=True)
            for index, entry in enumerate(self.credits[:13]):
                self._text(entry, 14, INK, (content.x, content.y + 50 + index * 18))
        elif self.overlay == "news":
            self._draw_news(content)
        elif self.overlay == "history":
            self._draw_history(content)
        elif self.overlay == "opportunities":
            self._draw_empty_information(
                content,
                "Opportunities",
                "No opportunities are available yet. Contracts and simulation-driven offers "
                "arrive in later milestones.",
            )
        elif self.overlay == "journal":
            self._draw_empty_information(
                content,
                "Journal",
                "No story leads have been recorded yet. Local characters and story progress "
                "arrive in later milestones.",
            )
        else:
            self._text("Notice", 30, INK, (content.centerx, content.y + 30), center=True)
            for index, line in enumerate(self._wrap(self.status_message, 330, 16, 8)):
                self._text(line, 16, INK, (content.x + 15, content.y + 75 + index * 24))

    def _draw_empty_information(self, content: pygame.Rect, title: str, message: str) -> None:
        self._text(title, 30, INK, (content.centerx, content.y + 24), center=True)
        for index, line in enumerate(self._wrap(message, 320, 16, 8)):
            self._text(line, 16, INK, (content.x + 20, content.y + 78 + index * 24))

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
            rect = pygame.Rect(content.x + 5, y, 350, 34)
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
            line = self._wrap(item.text, 335, 12, 1)
            if line:
                self._text(line[0], 12, INK, (rect.x + 5, rect.y + 17))

    def _draw_history(self, content: pygame.Rect) -> None:
        if self.world is None:
            return
        commodity = self.world.commodities[self.selected_commodity_id]
        samples = self.world.price_history[self.selected_port_id][self.selected_commodity_id]
        self._text(
            f"{commodity.name} Price History",
            25,
            INK,
            (content.centerx, content.y + 24),
            center=True,
        )
        chart = pygame.Rect(content.x + 10, content.y + 70, 335, 180)
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
        status = market_status(
            self.world.ports[self.selected_port_id].inventories[self.selected_commodity_id],
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
        if rejected:
            self.status_message = rejected.payload["reason"]
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

    def _handle_game_action(self, name: str) -> None:
        if self.world is None:
            return
        if name.startswith("tab:"):
            self._activate_tab(name.split(":", 1)[1])
        elif name == "open_hold":
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
        elif name == "buy":
            self._execute_trade(True)
        elif name == "sell":
            self._execute_trade(False)
        elif name == "sail":
            self._execute_sail()
        elif name == "save":
            self.save()
        elif name == "load":
            self.continue_game()
        elif name == "news":
            self.overlay_scroll = 0
            self.overlay = "news"
            self.news_read_through_sequence = max(
                (item.latest_sequence for item in build_news_items(self.world, self.settings)),
                default=self.news_read_through_sequence,
            )
        elif name == "opportunities":
            self.overlay_scroll = 0
            self.overlay = "opportunities"
        elif name == "journal":
            self.overlay_scroll = 0
            self.overlay = "journal"
        elif name == "history":
            self.overlay_scroll = 0
            self.overlay = "history"
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
        self.overlay = None

    def _handle_click(self, position: tuple[int, int]) -> None:
        if self.overlay is not None:
            for button in reversed(tuple(self.buttons.values())):
                if not button.enabled or not button.rect.collidepoint(position):
                    continue
                if button.name == "close_overlay":
                    self.overlay = None
                elif button.name.startswith("news:"):
                    self._handle_news_action(button.name)
                return
            return
        for button in reversed(tuple(self.buttons.values())):
            if button.enabled and button.rect.collidepoint(position):
                if self.mode == "title":
                    self._handle_title_action(button.name)
                else:
                    self._handle_game_action(button.name)
                return
        if self.mode == "game" and self.world is not None and position[0] < self.settings.map_width:
            for port in self.world.ports.values():
                distance = math.dist(position, (port.map_x, port.map_y + 7))
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
        if self.overlay is not None or position[0] < self.settings.map_width:
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
            self._handle_click(event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            self._handle_scroll(event.y, pygame.mouse.get_pos())
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_m and self.audio_available:
                muted = pygame.mixer.music.get_volume() > 0
                pygame.mixer.music.set_volume(0 if muted else self.settings.music_volume)
            elif event.key == pygame.K_ESCAPE:
                if self.overlay:
                    self.overlay = None
                elif self.mode == "game":
                    self.mode = "title"
                    self.time.paused = True
            elif self.mode == "game" and event.key == pygame.K_SPACE:
                self.time.paused = not self.time.paused
            elif self.mode == "game" and event.key == pygame.K_LEFT:
                self.time.slower()
            elif self.mode == "game" and event.key == pygame.K_RIGHT:
                self.time.faster()

    def update(self, elapsed_seconds: float) -> None:
        if self.mode != "game" or self.engine is None or self.world is None:
            return
        for _ in range(self.time.due_ticks(elapsed_seconds, self.settings.max_ticks_per_frame)):
            events = self.engine.step()
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
