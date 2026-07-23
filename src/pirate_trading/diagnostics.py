"""Human-readable and JSON Lines diagnostics for headless runs."""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import TextIO

from pirate_trading.models import DomainEvent, SimulationReport, WorldState


class JsonlEventWriter:
    """Write simulation events as one canonical JSON object per line."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: TextIO | None = None

    def __enter__(self) -> JsonlEventWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write(self, event: DomainEvent) -> None:
        if self._file is None:
            raise RuntimeError("JSONL writer must be opened before events are written")
        self._file.write(
            json.dumps(event.to_primitive(), sort_keys=True, separators=(",", ":")) + "\n"
        )


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}${absolute // 100:,}.{absolute % 100:02d}"


def format_report(report: SimulationReport, world: WorldState) -> str:
    """Format the final world and economic ledger for terminal output."""
    lines = [
        "Pirate Trading — Milestone 0",
        f"Seed: {report.seed}",
        f"Completed ticks: {report.completed_ticks:,}",
        f"State hash: {report.state_hash}",
        "",
        "Ports",
    ]
    for port_id in sorted(world.ports):
        port = world.ports[port_id]
        lines.append(f"  {port.name} — treasury {_money(port.treasury)}")
        for commodity_id in sorted(world.commodities):
            commodity = world.commodities[commodity_id]
            inventory = port.inventories[commodity_id]
            lines.append(
                f"    {commodity.name}: stock {inventory.quantity}/{inventory.capacity}, "
                f"bid {_money(inventory.bid_price)}, ask {_money(inventory.ask_price)}"
            )

    lines.extend(["", "Economic ledger"])
    for commodity_id in sorted(world.commodities):
        commodity = world.commodities[commodity_id]
        lines.append(
            f"  {commodity.name}: produced {world.ledger.produced[commodity_id]:,}, "
            f"consumed {world.ledger.consumed[commodity_id]:,}, "
            f"overflow {world.ledger.overflow[commodity_id]:,}, "
            f"unmet {world.ledger.unmet_demand[commodity_id]:,}"
        )
    lines.append(f"  Operating-cost sink: {_money(world.ledger.operating_costs)}")

    lines.extend(["", "Merchants"])
    for merchant_id in sorted(world.merchants):
        merchant = world.merchants[merchant_id]
        location = merchant.port_id or f"at sea to {merchant.voyage.destination_id}"
        lines.append(
            f"  {merchant.name}: cash {_money(merchant.cash)}, profit "
            f"{_money(merchant.realized_profit)}, trades {merchant.completed_trades}, "
            f"voyages {merchant.completed_voyages}, location {location}"
        )

    lines.extend(
        [
            "",
            "Diagnostics",
            f"  Events: {sum(report.event_counts.values()):,}",
            f"  Rejected commands: {report.rejected_commands}",
            f"  Invariant failures: {report.invariant_failures}",
        ]
    )
    return "\n".join(lines)
