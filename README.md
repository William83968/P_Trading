# Pirate Trading

Pirate Trading is a single-player trading sandbox built around a deterministic, living
world. Ports, merchants, weather, factions, and other systems will continue to evolve
independently of the player.

The project is currently at its technical-foundation stage. See the
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

## Run

Both entry points should print the package name and version:

```shell
python -m pirate_trading
pirate-trading
```

## Quality checks

Run all local quality gates from the repository root:

```shell
ruff check .
ruff format --check .
pytest
```
