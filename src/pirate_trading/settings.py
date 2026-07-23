"""Central runtime, balance, and presentation settings."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Settings shared by the simulation, UI, and tests."""

    world_seed: int = 2167
    simulation_ticks: int = 10_000
    ticks_per_day: int = 24
    merchant_count: int = 3
    merchant_capacity: int = 50
    merchant_starting_cash: int = 100_000
    player_id: str = "player"
    player_name: str = "Captain"
    player_starting_port: str = "crowns_haven"
    player_starting_cash: int = 100_000
    player_capacity: int = 50
    port_starting_treasury: int = 1_000_000
    storage_capacity_multiplier: int = 3
    basis_point_scale: int = 10_000
    merchant_preference_min: int = 9_500
    merchant_preference_max: int = 10_500
    check_invariants: bool = True
    write_event_log: bool = True
    event_log_path: Path = Path("output/milestone_0_events.jsonl")
    content_directory: Path = Path("content")
    assets_directory: Path = Path("assets")
    quick_save_path: Path = Path("saves/quicksave.json")
    save_format_version: int = 1
    market_history_limit: int = 30
    recent_event_limit: int = 200
    shortage_threshold_bp: int = 7_500
    surplus_threshold_bp: int = 12_500
    window_width: int = 1_280
    window_height: int = 720
    map_width: int = 1_000
    map_height: int = 706
    render_fps: int = 60
    time_rates: tuple[int, ...] = (4, 8, 16)
    initial_time_rate_index: int = 0
    max_ticks_per_frame: int = 64
    music_volume: float = 0.15


SETTINGS = SimulationSettings()
