"""Central runtime, balance, and presentation settings."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Settings shared by the simulation, UI, and tests."""

    world_seed: int = 2167
    simulation_ticks: int = 10_000
    ticks_per_day: int = 24
    merchant_count: int = 5
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
    save_format_version: int = 2
    market_history_limit: int = 30
    recent_event_limit: int = 200
    shortage_threshold_bp: int = 7_500
    surplus_threshold_bp: int = 12_500
    window_width: int = 1_440
    window_height: int = 810
    map_width: int = 1_000
    map_height: int = 706
    render_fps: int = 60
    time_rates: tuple[int, ...] = (4, 8, 16)
    initial_time_rate_index: int = 0
    max_ticks_per_frame: int = 64
    background_music: str | None = None
    music_volume: float = 0.35
    week_ticks: int = 168
    weekly_reveal_seconds: float = 1.2
    initial_provisions: int = 5
    player_initial_provisions: int = 0
    weekly_wage: int = 500
    condition_maximum: int = 100
    repair_cost_per_point: int = 100
    event_check_interval: int = 24
    event_gap_min_days: int = 5
    event_gap_max_days: int = 10
    maximum_major_events: int = 2
    base_false_rumour_bp: int = 2_000
    base_bluff_success_bp: int = 3_500
    upgrade_expanded_hold_cost: int = 25_000
    upgrade_fine_sails_cost: int = 30_000
    upgrade_reinforced_hull_cost: int = 30_000
    upgrade_crows_nest_cost: int = 20_000
    ship_raid_cost: int = 500
    port_raid_cost: int = 2_500
    port_raid_cooldown_ticks: int = 168
    explosion_animation_seconds: float = 0.7
    situation_gap_min_ticks: int = 48
    situation_gap_max_ticks: int = 96
    maximum_active_situations: int = 4
    maximum_active_relief_calls: int = 1
    relief_call_streak_days: int = 3
    relief_call_port_cooldown_ticks: int = 504
    relief_call_global_cooldown_ticks: int = 168
    mission_failure_penalty_bp: int = 2_500
    mission_failure_minimum: int = 1_000
    delivery_situation_cooldown_ticks: int = 240
    heat_decay_delay_ticks: int = 72
    heat_decay_per_day: int = 2
    notoriety_decay_per_week: int = 2
    fence_price_bp: int = 8_000
    lay_low_cost: int = 10_000
    payoff_cost_per_point: int = 500
    piracy_service_cooldown_ticks: int = 168
    navy_encounter_cap_bp: int = 7_500
    ship_raid_haul_limit: int = 8
    port_raid_haul_limit: int = 15
    incoming_raid_loss_limit: int = 5
    camera_minimum_zoom: float = 1.0
    camera_maximum_zoom: float = 3.0
    camera_initial_maximum_zoom: float = 2.0


SETTINGS = SimulationSettings()
