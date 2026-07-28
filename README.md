# Pirate Trading

Pirate Trading is a single-player trading sandbox built around a deterministic, living world. Ports, merchants, weather, factions, and other systems will continue to evolve independently of the player.

The source repository is hosted on
[GitHub](https://github.com/William83968/P_Trading).

The project currently contains a playable five-port living-world vertical slice. See the
[development plan](./Pirate%20Trading%20Game%20Development%20Plan.md) for the design,
architecture, and milestone roadmap, or the [changelog](./CHANGELOG.md) for implemented changes.

## Development environment

The project uses the `game-gen` Conda environment with Python 3.13. To rebuild it from
scratch:

```shell
conda env remove --name game-gen --yes
conda env create --file environment.yml
conda activate game-gen
```

The environment definition installs the project and its development tools in editable
mode. To refresh the editable installation without rebuilding the environment:

```shell
python -m pip install --editable ".[dev]"
```

## Run the game

Open the pygame trading interface:

```shell
python -m pirate_trading
pirate-trading
```

The game starts paused. Use the supplied controls or keyboard shortcuts to advance time,
inspect markets, trade, and sail between ports:

- `Space`: play or pause
- `Left` / `Right`: change between 4, 8, and 16 simulation hours per second
- `Escape`: close an overlay or return to the title
- `Enter` / `Space`: dismiss a settled weekly dice reveal
- Mouse wheel: zoom the map between 1× and 3×
- Drag empty map space: pan and leave player-follow mode
- `F`: recenter and smoothly follow the player
- `Home`: frame the complete known port network

Background music is currently disabled while the project establishes a more suitable audio
direction.

At the start of every seven-day period, two dice select the global weekly threat or fortune
setting. The animated reveal pauses the game, and a bold heading remains visible over the map.
Click the heading for trade-access, opportunity, raid, travel, report, and market effects.

The scalable inspector has five context tabs:

- **Market** compares stock, bid, ask, trend, and held quantity. Its explicit Max Buy and
  Max Sell controls avoid ambiguity.
- **Hold** reviews every owned commodity, capacity, cost basis, selected-port valuation,
  estimated gain or loss, and the executable sale quantity.
- **Port** summarizes routes, duration, operating cost, threat, provisions, condition, wage debt,
  repairs, upgrades, and sailing.
- **Ship** collects condition, provisions, wages, upgrades, and maintenance.
- **Heat** presents notoriety, local enforcement pressure, and contraband.

News, Opportunities, Journal, and Market History use the left navigation rail. The inspector can
be collapsed from the top bar when more map space is useful. The bottom action tray retains
camera mode, current status, and space for expanding contextual actions.

The default game window is 1440×810. Information overlays scale `message_paper.png` into a wider
reading surface. In Opportunities, the offer list and the selected offer's description scroll
independently: place the pointer over the area you want to scroll.

The persistent status bar shows both the current day and intraday hour. Each simulation tick is
one in-game hour.

Clicking the persistent cargo-capacity summary opens Hold. Select map ports while viewing
Hold to compare cargo valuations. Local values are live facts; remote values are delayed reports
labelled with their age, and an unknown value is never filled from hidden simulation state.
Selling remains available only at the player's docked port. Buying, selling, provisioning,
repairing, upgrading, and sailing use validated simulation commands shared with AI actions.

News groups weather, shortages, player activity, reports, and rumours and links relevant items
back to their context. Opportunities now arise from actual weather, market, route, and piracy
conditions every two to four days. They can reserve hold space and track delivery, travel,
reconnaissance, evacuation, salvage, protection, and smuggling objectives. Festival Supply names
Rum, Sugar, or Coffee; Harvest Export names the harvested commodity and a directly connected
low-stock destination. Accepted objectives remain pinned and display a live objective-specific
checklist with clean cargo, reserved mission loads, route status, voyage percentage, next action,
deadline, reward, penalty, and any reason completion is unavailable. Journal preserves local
story choices and accepted mission outcomes.

News pauses the simulation while open and restores the previous running state when closed.
Opportunities and interrupting notices also pause time, but leave it paused after dismissal.
Accepted missions receive objective-specific acceptance, completion, and failure notices.
Intermediate cargo and travel changes stay non-modal and update the checklist instead. Abandoning
a mission or missing its deadline incurs a ledgered penalty equal to 25% of its reward, with a
$10 minimum when funds are available.

Relief calls are deliberately rarer than ordinary situations. They require three consecutive
days of unmet grain or medicine demand, only one may be active globally, and the same
port/commodity pair has a 21-day cooldown. A relief condition creates its own contract and is not
duplicated by the ordinary situation scheduler.

Major or urgent information pauses once when learned and provides direct links to the affected
port, market, route, News entry, or opportunity. Routine lifecycle updates remain non-modal.

Contracts use a persistent three-stage loop: visit the issuing port to accept, obtain the required
cargo, then return before the deadline to Deliver or Abandon. Accepted contracts stay at the top
of Opportunities and remain selected when unrelated offers arrive.

The player's pirate ship can also raid:

- While sailing, raid an AI ship travelling on the same route for a $5 preparation cost. A
  successful raid opens its real cargo manifest and permits up to 8 selected units.
- While docked, Raid Port costs $25 and offers up to 15 selected units from the port's real stock.
  It closes that public market for the day, raises local heat, and starts a seven-day alert.
- Taking more cargo causes more condition damage. Failed ship and port raids retain their larger
  fixed damage and provide no loot. Pirate attacks against the player remain capped at 5 units.

Raids create persistent local heat and global notoriety. Lawful ports progress through Clear,
Watched, Wanted, and Hunted enforcement levels, reducing trade access and eventually refusing
public markets, upgrades, or ordinary repair prices. Navy patrols may inspect voyages and offer
Pay Fine, Surrender Contraband, Flee, or Bluff. Pressure decays after raid-free time and can be
reduced through weekly official payoffs.

Blackwater Cay ignores lawful restrictions. It fences a selected stolen commodity and quantity at
80% of its current bid and offers a weekly $100 Lay Low service. Lawful buyers only accept the
clean portion of a cargo stack. Hold labels clean and stolen quantities separately and directs
contraband to Blackwater. Stolen quantities retain provenance through fencing, confiscation, and
saves. Pirate attacks and player raids use the supplied `explode.png`
burst animation; all expenses, fines, confiscations, transfers, and salvage remain covered by
conservation ledgers.
The information button in Market or Hold opens history for the shared selected commodity.
Use the mouse wheel over long market, cargo, or news lists to scroll.

Commodities are always presented as text and data; adding one never requires a new UI icon.

The Save and Load controls use one atomic format-2 quick-save at `saves/quicksave.json`.
Earlier format-2 documents are hash-checked in their original form before new additive fields
receive deterministic defaults.
Older format-1 saves require New Game because Milestone 2 adds persistent information, ship,
event, story, encounter, and weekly-outlook state.

## Run the headless economy

Run the configured deterministic 10,000-hour experiment without opening pygame:

```shell
pirate-trading-headless
```

The headless runner prints its final state hash, port markets, economic ledgers, and
merchant results. It also writes structured events to
`output/milestone_0_events.jsonl` by default.

Runtime and global balance values live in
[`settings.py`](./src/pirate_trading/settings.py). Ports, commodities, and routes are
defined in the [`content`](./content) YAML files. This keeps the command simple while
making the experiment easy to tune.

Display the installed package version without starting the game:

```shell
pirate-trading --version
```

## Assets and acknowledgements

The interface uses the files under [`assets`](./assets). Third-party icon attributions
are retained in [`acknowledgements.html`](./assets/acknowledgements.html) and are
available from the in-game Credits overlay.

## Quality checks

Run all local quality gates from the repository root:

```shell
ruff check .
ruff format --check .
pytest
```
