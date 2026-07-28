"""Pure calculations shared by focused decision overlays."""

from typing import Any

from pirate_trading.models import Merchant, PendingRaidLoot


def projected_raid_damage(
    pending: PendingRaidLoot,
    merchant: Merchant,
    selected_quantity: int,
) -> int:
    divisor = 4 if pending.raid_type == "ship" else 3
    raw = pending.base_condition_damage
    if selected_quantity:
        raw += (selected_quantity + divisor - 1) // divisor
    if "reinforced_hull" in merchant.ship.upgrades:
        return max(1, (raw * 7 + 9) // 10)
    return raw


def draw_focused_overlay(app: Any, content: Any) -> bool:
    """Draw a registered information or decision overlay."""
    if app.overlay == "news":
        app._draw_news(content)
    elif app.overlay == "history":
        app._draw_history(content)
    elif app.overlay == "opportunities":
        app._draw_opportunities(content)
    elif app.overlay == "journal":
        app._draw_journal(content)
    elif app.overlay in {"weekly_roll", "weekly_info"}:
        app._draw_weekly_outlook(content, reveal=app.overlay == "weekly_roll")
    elif app.overlay == "encounter":
        app._draw_encounter(content)
    elif app.overlay == "major_event":
        app._draw_major_event(content)
    elif app.overlay == "mission_notice":
        app._draw_mission_notice(content)
    elif app.overlay == "upgrades":
        app._draw_upgrades(content)
    elif app.overlay == "raid_loot":
        app._draw_raid_loot(content)
    else:
        return False
    return True
