"""Pure panel-facing cargo helpers."""

from dataclasses import dataclass
from typing import Any

from pirate_trading.models import Merchant


@dataclass(frozen=True, slots=True)
class CargoProvenance:
    total: int
    clean: int
    stolen: int


def cargo_provenance(merchant: Merchant, commodity_id: str) -> CargoProvenance:
    total = merchant.cargo.get(commodity_id, 0)
    stolen = min(total, merchant.stolen_cargo.get(commodity_id, 0))
    return CargoProvenance(total=total, clean=total - stolen, stolen=stolen)


def draw_context_panel(app: Any, sidebar_x: int) -> None:
    """Route the inspector to one focused context without coupling the app shell."""
    drawers = {
        "market": app._draw_market,
        "hold": app._draw_hold,
        "port": app._draw_port,
        "ship": app._draw_ship,
        "reputation": app._draw_reputation,
    }
    drawers[app.active_sidebar_tab](sidebar_x)
