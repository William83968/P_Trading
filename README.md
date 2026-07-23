# Pirate Trading

Pirate Trading is a single-player trading sandbox built around a deterministic, living world. Ports, merchants, weather, factions, and other systems will continue to evolve independently of the player.

The source repository is hosted on
[GitHub](https://github.com/William83968/P_Trading).

The project currently contains a playable trading and cargo-management vertical slice. See the
[development plan](./Pirate%20Trading%20Game%20Development%20Plan.md) for the design,
architecture, and milestone roadmap.

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
- `M`: mute or restore music
- `Escape`: close an overlay or return to the title

The sidebar has three context tabs:

- **Market** compares stock, bid, ask, trend, and held quantity. Its explicit Max Buy and
  Max Sell controls avoid ambiguity.
- **Hold** reviews every owned commodity, capacity, cost basis, selected-port valuation,
  estimated gain or loss, and the executable sale quantity.
- **Port** summarizes local market conditions, connected routes, voyage duration, cost,
  and sailing actions.

Clicking the persistent cargo-capacity summary opens Hold. Select map ports while viewing
Hold to compare cargo valuations; remote values are live during the current perfect-information
milestone. Selling remains available only at the player's docked port. Buying, selling, and
sailing use the same validated simulation commands as AI actions.

News groups notable simulation events and links relevant reports back to their port and
commodity. Opportunities and Journal establish the navigation for later generated content.
The information button in Market or Hold opens history for the shared selected commodity.
Use the mouse wheel over long market, cargo, or news lists to scroll.

Commodities are always presented as text and data; adding one never requires a new UI icon.

The Save and Load controls use one atomic quick-save at `saves/quicksave.json`.

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
