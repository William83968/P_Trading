from dataclasses import replace
from pathlib import Path
from shutil import copy

import pytest

from pirate_trading.content import ContentError, load_content
from pirate_trading.settings import SETTINGS


def test_loads_configured_world_content() -> None:
    content = load_content(SETTINGS)

    assert set(content.commodities) == {"grain", "timber", "sugar", "rum", "cloth"}
    assert set(content.ports) == {"crowns_haven", "san_cordelia", "blackwater_cay"}
    assert len(content.routes) == 3
    assert (content.ports["crowns_haven"].map_x, content.ports["crowns_haven"].map_y) == (
        245,
        375,
    )
    assert all(
        inventory.capacity == 300
        for port in content.ports.values()
        for inventory in port.inventories.values()
    )


def test_malformed_yaml_has_source_aware_error(tmp_path: Path) -> None:
    (tmp_path / "commodities.yaml").write_text("commodities: [", encoding="utf-8")
    settings = replace(SETTINGS, content_directory=tmp_path)

    with pytest.raises(ContentError, match="commodities.yaml"):
        load_content(settings)


def test_unknown_route_port_is_rejected(tmp_path: Path) -> None:
    for filename in ("commodities.yaml", "ports.yaml", "routes.yaml"):
        copy(SETTINGS.content_directory / filename, tmp_path / filename)
    route_path = tmp_path / "routes.yaml"
    route_path.write_text(
        route_path.read_text(encoding="utf-8").replace(
            "port_a: crowns_haven", "port_a: missing_port", 1
        ),
        encoding="utf-8",
    )
    settings = replace(SETTINGS, content_directory=tmp_path)

    with pytest.raises(ContentError, match="unknown port"):
        load_content(settings)
