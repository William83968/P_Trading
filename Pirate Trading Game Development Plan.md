# Pirate Trading Game

## Development Plan v3.0

## 1. Project Vision

Build a single-player pirate trading sandbox in which the player rises from an unknown sailor to a legendary captain through trade, piracy, diplomacy, and exploration.

The defining feature is a world that continues to operate independently of the player. Ports produce and consume goods, merchants seek profit, pirates hunt valuable traffic, navies protect strategic routes, governments change policy, and weather alters risk and travel time.

The player can influence this world, but cannot completely control it.

## 2. Design Pillars

### 2.1 A living but understandable world

World activity must arise from connected systems rather than arbitrary scripted outcomes. Those systems must also expose enough information for the player to understand, predict, and exploit them.

A change is not meaningful merely because it happened in the simulation. It becomes gameplay when the player can notice it, form a theory about it, and make a consequential decision.

The game should therefore surface:

- Current prices, supply, and demand
- Historical prices and recent trends
- Known shipping routes and observed traffic
- News, rumours, shortages, conflicts, and weather warnings
- Explanations for important changes where appropriate

### 2.2 Gameplay and simulation developed together

The simulation is the foundation of the world, but it must be built through playable vertical slices. Every major milestone should add a complete player decision loop rather than an isolated engine subsystem.

The first question is not whether the entire world can simulate itself. It is whether buying cargo, choosing a destination, accepting risk, and selling at the right time is interesting.

### 2.3 Deterministic, bounded randomness

Randomness should create variation without making outcomes feel arbitrary.

- All simulation randomness originates from a world seed.
- Separate named random streams prevent one subsystem from accidentally changing another subsystem's results.
- Probability models are introduced only when simpler rules are insufficient.
- Results are bounded and tested so that extreme values cannot destabilize the world.
- A saved game contains enough state to continue deterministically.

Weighted sampling, distributions, state transitions, and procedural noise are tools, not requirements. Their use must be justified by observable gameplay.

### 2.4 Emergent stories with authored structure

The simulation creates conditions; the narrative layer identifies meaningful conditions and presents opportunities.

For example:

1. A storm delays merchant traffic.
2. A port's sugar inventory falls.
3. Its price rises and merchants reroute toward it.
4. Pirates gather along the increasingly valuable route.
5. A governor posts an escort contract.
6. The player chooses to trade, escort, raid, or ignore the situation.

Authored templates may frame events and provide character, but they should respond to real world state rather than fabricate unrelated conditions.

### 2.5 Scope is a design constraint

The game should prefer a small world with clear, interacting systems over a large world filled with shallow content. Every feature must improve at least one of these:

- The quality of player decisions
- The legibility of the living world
- The interaction between existing systems
- The variety of viable play styles

## 3. Target Player Experience

### Core loop

1. Gather information at a port.
2. Choose cargo, a route, and an acceptable level of risk.
3. Travel while time and the world advance.
4. Respond to opportunities, hazards, and other ships.
5. Sell, repair, resupply, and improve the ship or crew.
6. Build wealth, knowledge, relationships, and reputation.

### Sources of interesting decisions

- Cargo capacity versus supplies and armament
- Reliable margins versus speculative opportunities
- Fast dangerous routes versus slow safe routes
- Immediate profit versus faction reputation
- Fighting, fleeing, negotiating, or surrendering cargo
- Acting on incomplete or outdated information

### Initial MVP boundaries

The first complete prototype should contain:

- One compact region with 5 ports
- 8 commodities with distinct production and consumption patterns
- 10-20 simulated merchant ships
- One player ship and a small set of upgrades
- Buying, selling, travel, operating costs, and cargo limits
- Price history, port information, rumours, and a world event log
- Basic weather and route risk
- A lightweight pirate threat with flee-or-fight resolution
- Save/load and deterministic replay support

The MVP does not require boarding, tactical real-time combat, diplomacy, procedural maps, multiple regions, legendary ships, or a full story campaign.

## 4. Technical Direction

### Stack

- Python 3.13+
- pygame-ce for the application window, input, audio, and rendering
- PyYAML for authored content and balance data
- pytest for unit, integration, determinism, and save/load tests
- Ruff for formatting and linting
- Mypy or Pyright for static type checking once the core model stabilizes

NumPy, SciPy, and other heavy dependencies should be added only after profiling or a specific model demonstrates a need for them.

### Simulation model

- The simulation advances in fixed logical ticks; the initial target is one in-game hour per tick.
- Rendering runs independently and may interpolate presentation state.
- Pausing and time acceleration change how frequently ticks are processed, not the rules within a tick.
- The engine processes systems in a documented, stable order.
- Player and AI intentions enter the simulation as commands.
- Systems emit domain events for history, UI notifications, narrative observation, and debugging.
- The presentation layer reads simulation state but never directly mutates it.

### Deterministic update order

The initial tick order should be explicit and small:

1. Validate and apply queued commands.
2. Advance travel and ship activity.
3. Resolve arrivals and cargo transfers.
4. Apply port production and consumption when scheduled.
5. Recalculate market quotes from resulting inventory pressure.
6. Let AI choose commands for a future tick.
7. Evaluate world-event and narrative triggers.
8. Record events and advance the clock.

The exact order may change, but changing it is a simulation compatibility change and should be tested accordingly.

## 5. Architecture

### Principles

- Prefer plain data models and composition over a universal entity inheritance tree.
- Give every persistent object a stable identifier.
- Keep simulation code independent of pygame so it can run headlessly.
- Keep the central engine small: it schedules systems but does not contain their rules.
- Systems may read shared world state, but mutations happen through narrow, documented operations.
- Use commands for requested actions and events for facts that already occurred.
- Avoid a generic event bus for core state mutation; direct, typed system calls are easier to trace.
- Balance values and content belong in validated data files, while fundamental rules remain in code.

### State, commands, and events

`WorldState` is the serializable source of truth. It contains the clock and collections of ports, ships, markets, factions, routes, and active world conditions.

Commands express intent, for example:

- `BuyCargo`
- `SellCargo`
- `BeginVoyage`
- `ChangeCourse`
- `AttemptEscape`

Events record outcomes, for example:

- `CargoPurchased`
- `ShipDeparted`
- `ShipArrived`
- `ShortageStarted`
- `RouteBecameDangerous`

Both the player interface and AI issue commands through the same validation and execution path.

### Randomness

The randomness service owns named streams derived from the world seed, such as `weather`, `merchant_ai`, `encounters`, and `narrative`. Simulation modules must not use module-level random functions directly.

Tests should be able to inject predetermined outcomes. Debug output should record the world seed, tick, stream, and event context needed to reproduce important failures.

### Economy baseline

Begin with a deliberately simple stock-and-flow economy:

- Ports produce and consume commodity quantities on a schedule.
- Ships physically remove goods from one inventory and add them to another.
- Market quotes respond to inventory relative to a port's target stock.
- Prices have configurable floors, ceilings, and smoothing.
- Transaction costs and operating costs prevent effortless infinite arbitrage.
- Shortages and surpluses are explicit states derived from inventory pressure.

Do not begin with inflation, banking, credit, currency exchange, or a global macroeconomic model. Add them only if the basic trading game needs them.

The economy should track invariants. Except for declared production, consumption, loss, or scripted setup, goods must not appear or disappear.

### AI baseline

Merchant AI should initially use a transparent utility score rather than a complex planner:

`expected profit - travel cost - risk cost - opportunity cost`

AI knowledge may be delayed or incomplete, but AI actions must obey the same cargo, money, travel, and market rules as the player. Decision traces should be available in debug mode.

### Persistence

A save contains:

- Save-format version
- Game/simulation version
- World seed and random-stream states
- Current simulation tick
- Complete persistent world state
- Pending commands and time-based effects

Save/load round trips must preserve state exactly. Migrations are required when a released save format changes.

## 6. Recommended Starter Scaffold

```text
PirateTrading/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── src/
│   └── pirate_trading/
│       ├── __init__.py
│       ├── __main__.py
│       ├── settings.py
│       ├── domain/
│       │   ├── ids.py
│       │   ├── models.py
│       │   ├── commands.py
│       │   └── events.py
│       ├── simulation/
│       │   ├── clock.py
│       │   ├── engine.py
│       │   ├── random_service.py
│       │   └── world_state.py
│       ├── economy/
│       │   ├── pricing.py
│       │   └── system.py
│       ├── ai/
│       │   └── merchant.py
│       └── diagnostics/
│           ├── event_log.py
│           └── metrics.py
├── content/
│   ├── commodities.yaml
│   ├── ports.yaml
│   └── routes.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   └── GAME_DESIGN.md
├── tests/
│   ├── unit/
│   ├── integration/
│   └── determinism/
└── tools/
    └── run_headless.py
```

This scaffold contains only what the headless economic experiment needs. Even within it, files should be introduced with their first implementation rather than created empty.

Add packages such as `presentation`, `persistence`, `navigation`, `encounters`, `factions`, and `narrative` when their milestones begin. Add `assets` with the first graphical or audio asset. Split `domain/models.py` when it becomes difficult to navigate, not in anticipation of future complexity.

The important boundary is not the number of packages. It is that `simulation`, `economy`, and `ai` remain runnable without importing pygame or presentation code.

## 7. Development Roadmap

### Milestone 0 — Headless economic experiment

Build:

- Packaging, tests, linting, and a minimal continuous-integration workflow
- Serializable world state and stable identifiers
- Fixed-tick engine and seeded random streams
- Ports, routes, commodities, inventories, and production/consumption
- Basic pricing model
- Merchant decisions and abstract travel
- Headless runner with summary metrics and event logs

Exit criteria:

- The same seed and command sequence produce identical state hashes after 10,000 ticks.
- Commodity conservation tests pass.
- No inventory, price, cash, or travel duration becomes invalid during a long simulation.
- Merchants complete profitable routes without player input.
- A developer can inspect why a merchant chose a route and why a price changed.

### Milestone 1 — Playable trading vertical slice

Build:

- pygame application loop and basic screens
- One region with 3 ports and 5 commodities
- Player ship, cargo capacity, money, travel, buying, and selling
- Pause and time controls
- Market table, price history, news/event log, and route information
- Save/load

Exit criteria:

- A new player can complete a buy-sail-sell loop without developer guidance.
- AI and player transactions use the same market rules.
- The player can explain at least one price movement using information shown by the game.
- Saving, loading, and continuing produces the same outcome as uninterrupted play.
- A 30-minute play session contains multiple viable trade choices.

### Milestone 2 — Risk and route decisions

Build:

- Expand to the MVP's 5 ports and 8 commodities
- Supplies, wages, maintenance, and travel costs
- Weather conditions and route modifiers
- Incomplete market knowledge, rumours, and information age
- Basic encounter framework and pirate threat
- Ship upgrades that create meaningful trade-offs

Exit criteria:

- The nominally most profitable route is not always the best choice after cost and risk.
- Weather and information age visibly affect player decisions.
- At least three economically viable ship configurations or strategies exist.
- Automated simulations do not converge to permanent universal shortages or price caps.

### Milestone 3 — Naval interaction

Build:

- Detection, pursuit, escape, and surrender
- First combat implementation
- Damage, repairs, crew impact, and cargo loss
- Pirate and naval behaviours
- Rewards and consequences that feed back into trade routes and markets

Choose the combat format only after prototyping it. Do not commit prematurely to real-time tactical combat if a faster encounter system better preserves the trading game's pace.

Exit criteria:

- Combat and avoidance consume the same world resources used by trading.
- Piracy changes traffic, supply, prices, and patrol behaviour.
- Trading-focused play remains viable without requiring frequent combat.

### Milestone 4 — Factions and generated opportunities

Build:

- Reputation and faction relationships
- Laws, tariffs, embargoes, conflict, and diplomacy
- Contracts derived from actual simulation conditions
- Narrative observer and authored event templates
- Multiple long-term progression paths

Exit criteria:

- Contracts reflect valid world conditions and update when those conditions change.
- Faction changes affect routes, prices, access, or safety in visible ways.
- The player can pursue at least trading, privateering, and piracy-oriented careers.

### Milestone 5 — Content, balance, and release preparation

Build:

- Additional content only where existing systems support it
- Tutorials and accessibility options
- Audio and visual polish
- Economy and AI balancing tools
- Save migrations, profiling, crash diagnostics, and packaging
- Endgame goals and a clear definition of becoming a legendary captain

Exit criteria:

- New-player and long-session playtests meet defined experience targets.
- Performance remains within budget at the supported world size.
- Saves survive supported upgrades.
- The game has a satisfying early game, midgame transition, and endgame objective.

## 8. Testing and Observability

### Required test categories

- Unit tests for pricing, inventory transfer, command validation, and AI scoring
- Integration tests for complete trade and travel flows
- Determinism tests based on state hashes and recorded commands
- Property/invariant tests for quantities, prices, money, and travel state
- Save/load round-trip and migration tests
- Long-running headless stability tests
- Performance benchmarks at target and stress-test world sizes

### Debug tools

- Seed and tick display
- Pause, single-tick, and accelerated headless modes
- Structured event history with filters by port, ship, commodity, and faction
- Market and inventory charts
- AI decision traces
- State snapshots and comparison tools

These are part of the product's development foundation, not optional polish. Emergent systems are otherwise extremely difficult to balance and debug.

## 9. Working Practices

- Keep the default branch runnable and tested.
- Implement features in small vertical slices.
- Write a short design note for changes that affect simulation order, save compatibility, or major player rules.
- Document public interfaces and non-obvious invariants.
- Store tunable values in validated content files, but avoid turning every constant into configuration.
- Profile before optimizing or introducing specialized numerical libraries.
- Record notable player-facing changes in the changelog.
- Review scope at the end of every milestone.

Documentation should describe decisions that developers or players need to understand. It should not be created merely to satisfy a prescribed document list.

## 10. Immediate Starting Sequence

The first implementation cycle should be deliberately small:

1. Create the package, test runner, lint configuration, and minimal CI.
2. Define identifiers, commodity/port/ship models, `WorldState`, commands, and events.
3. Implement the fixed simulation clock and named seeded random streams.
4. Load three ports, five commodities, and routes from validated content files.
5. Implement production, consumption, inventory transfers, and bounded pricing.
6. Add one merchant that chooses, travels, buys, and sells through commands.
7. Build the headless runner, invariants, event log, and determinism test.
8. Add the first pygame screen only after the complete loop works headlessly.
9. Expose the market, travel, and transaction loop to the player.
10. Playtest before adding weather, combat, politics, or narrative systems.

## 11. Definition of Success

The project succeeds when players believe the world is alive because they can observe its causes, anticipate its changes, and make plans that matter.

For every proposed feature, ask:

1. What new decision does this give the player?
2. How will the player perceive its cause and effect?
3. Which existing system does it meaningfully interact with?
4. How will we test that it remains stable and understandable?

If those questions do not have convincing answers, simplify, redesign, or remove the feature.
