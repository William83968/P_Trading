# Pirate Trading Game

## Development Plan v3.1

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

Python's seeded random-number generator is sufficient for the intended simulation scale. The
challenge is not generating numbers more quickly or replacing the generator with a more elaborate
formula; it is translating random inputs into bounded, state-dependent, and understandable
consequences. Additional systems should use independent streams such as `weather`, `encounters`,
`treasure`, `conflict`, `opportunities`, `narrative`, and `rumours` so that adding a roll to one
system does not alter the outcomes of another.

### Causal world events and narrative

Milestones 2 through 4 should follow one shared design direction: causal world events first,
imperfect information second, and location opportunities and story arcs on top. The deterministic
engine is the foundation and needs additional stateful systems, not a replacement random-number
algorithm.

Weather, robberies, treasure discoveries, battles, shortages, festivals, and political changes are
simulation facts. News and rumours are reports about those facts. News must not directly change a
price merely because a headline says that a price should rise.

A typical causal chain is:

1. A storm damages sugar production and delays traffic on a route.
2. Sugar inventory falls as local consumption continues.
3. Market quotes respond to the lower inventory.
4. Merchants redirect cargo toward the higher margin.
5. Pirate activity responds to the more valuable traffic.
6. Reports reach nearby ports with varying delay, detail, and reliability.
7. Production, traffic, and prices recover as the condition expires.

Events affect production, consumption, cargo, travel time, route availability, demand, operating
costs, or risk. Prices continue to emerge from resulting market state. Material creation,
destruction, and transfer of goods or cash must be recorded in explicit ledgers.

Persistent world conditions should have stable IDs and record:

- Event type, start tick, lifecycle stage, and expected or actual expiry
- Geographic scope and affected ports, routes, actors, and commodities
- Severity and mechanical modifiers
- Publicly known information and hidden simulation state
- Causes, preceding events, and resulting events
- News reports, rumours, opportunities, and story consequences derived from the condition

Most consequential events should progress through precursor, active, consequence, and recovery
stages. Eligibility depends on world state, location, season, cooldowns, and prior events. Weighted
selection, hazard rates, bounded severity distributions, and state machines may all be used, but a
single roll should not fully determine an event's economic result.

Visible events should be correlated with relevant future price movements without becoming direct
trading instructions. Exact severity, duration, inventories in transit, competing merchant
intentions, and some secondary consequences may remain unknown. Reports may be accurate, delayed,
incomplete, exaggerated, or genuine rumours that later prove false, but uncertainty must come from
the information model rather than arbitrary reversals of the underlying simulation.

Location stories use an authored narrative spine with simulation-generated connective events.
Every port should have recurring characters or factions, an ongoing local conflict, distinct
economic strengths and vulnerabilities, unique services, and opportunity templates that respond
to current world state. The player's decisions can advance, redirect, or close branches of a local
story; generated text must not invent conditions that are absent from the simulation.

Initially keep this work in two flat modules:

- `world_events.py` for eligibility, active conditions, mechanical effects, and resolution
- `narrative.py` for story state, opportunities, rumours, and player-facing reports

Create a separate encounter or market-venue module only when those rules become large enough to
need one. The central engine remains a small scheduler and does not absorb individual event rules.

### Event simulation test strategy

Event tests must measure causal behaviour and long-run balance, not only reproduce one expected
seed:

- Counterfactual tests run equivalent worlds with an event enabled and suppressed, then verify the
  expected state difference.
- Directional tests verify that relevant effects usually move inventory and prices in the intended
  direction over the intended time window.
- Specificity tests detect unjustified movement in unrelated commodities and distant markets.
- Bounded-predictability tests verify that visible information improves forecasts while a simple
  “buy every reported shortage” strategy does not dominate multi-signal decision making.
- Seed-matrix tests cover many long simulations and detect price-cap lock, universal shortages,
  runaway wealth, dead markets, event floods, and event starvation.
- Conservation tests reconcile robbery, combat, spoilage, treasure, production, and every other
  declared source, sink, or transfer.
- Narrative tests enforce prerequisites, exclusivity, deadlines, valid references, and reachable
  outcomes.
- Replay tests verify that saves restore active conditions, reports, story state, pending effects,
  random streams, and subsequent outcomes exactly.
- Performance tests keep event evaluation comfortably faster than real time in headless runs.

Initial calibration should aim for moderate rather than dominant predictive power. As a starting
measurement, visible relevant events may target roughly `0.25–0.55` correlation with subsequent
price movement over their designed horizon, while irrelevant markets should remain near zero.
These are tuning targets to validate through telemetry and playtests, not universal gameplay rules.

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

## 6. UI Design Direction

### UI goals

The interface should make the simulation understandable rather than merely decorative. The player must be able to see current conditions, compare opportunities, understand recent changes, and issue commands without interpreting raw simulation state.

The first UI targets a 1280×720 window:

- A 1000×706 map area using the Atlantic map
- A right sidebar for time, cash, cargo, market information, and actions
- Code-drawn port markers and route lines
- Fixed-size overlays for news, explanations, contracts, and confirmations

The layout may become responsive later, but Milestone 1 should first establish a clear, readable fixed-resolution interface.

### Pygame template

`src/pygame_template.py` is the reference for starting the presentation layer. Preserve its useful loop structure:

1. Initialize pygame, audio, the window, and the clock.
2. Poll and handle input events.
3. Advance the simulation according to the selected time state.
4. Update presentation state.
5. Draw the map, world markers, sidebar, overlays, and controls.
6. Flip the display and limit the rendering frame rate.
7. Shut pygame down cleanly.

Do not import the template as a production module because it initializes pygame and enters its loop at module import time. Adapt it into a small `src/pirate_trading/ui.py` with an explicit `run_ui()` entry point. Keep the UI flat until its actual size justifies separate screen or widget modules.

Rendering frame rate and simulation time are independent. The UI may render at 60 FPS while the fixed hourly simulation advances only when the time controller schedules a tick. Pausing must stop simulation ticks without stopping input, animation, or rendering.

### Simulation boundary

- The UI reads `WorldState` but does not mutate it directly.
- Buy, sell, and voyage actions enter the simulation through the same command path used by AI.
- UI-only state includes selection, hover, open overlay, table scroll, and current time-control state.
- Simulation events supply notifications, market explanations, and history entries.
- Assets are loaded and converted once, cached by name, and scaled once for their intended role.

### Approved asset roles

- `atlantic.png`: world-map background
- `message_paper.png`: fixed-size news, contract, explanation, and confirmation overlays
- `Jaapokki-Regular.otf` or the bundled regular TTF: primary UI font
- `dynalight.otf`: decorative headings only
- `player-marker.png`: player position on the map
- `pirate-ship.png`: ship portrait or encounter illustration
- `stamp.png`: navy marker
- `pirate-hat.png`: pirate threat or pirate faction marker
- `diploma.png`: contracts and official notices
- `anchor.png`: current port and docking
- `dollar.png`: player cash
- `health.png`: ship condition
- `bomb.png`: combat and danger
- `gunpowder.png`: ammunition
- `tech.png`: upgrades
- `information.png`: help, explanations, and tooltips
- `play.png` and `pause.png`: time controls
- `left-arrow.png` and `right-arrow.png`: navigation and paging
- `The Sailor's Tale.mp3`: background music

`stamp.png` is intentionally a 32×32 map marker and should remain near its native size. `navy.png` currently has no assigned UI role and should not be used in place of the stamp. The paper texture should remain a fixed overlay rather than being stretched as a general-purpose panel.

### Commodity presentation

Commodities must not require individual artwork. The number of commodities is expected to grow, so markets and cargo use scalable, data-driven tables containing:

- Commodity name
- Quantity and cargo capacity impact
- Buy and sell prices
- Recent price direction
- Shortage, balanced, or surplus status
- Optional category or status color accents

Icons are reserved for stable interface concepts such as money, health, navigation, contracts, factions, and time controls. Adding a commodity must never require a UI asset or commodity-specific rendering code.

### Cargo-hold panel

Reviewing owned commodities is a primary trading action, not a secondary report. The main interface
must always show cargo used versus total capacity and provide a dedicated **Hold** panel no more
than one action away.

The Hold panel is generated entirely from the player's current cargo and commodity data. Each
occupied row shows:

- Commodity name and quantity held
- Capacity used
- Total and per-unit purchase cost where known
- Current local bid and estimated cargo value when docked
- Realized or unrealized result where it can be calculated from known information
- Information age when displaying a remote estimate
- Sell quantity controls and a clear reason when selling is unavailable

Selecting a holding also selects the same commodity in the Market panel and its history overlay.
When the player is docked, selling can be initiated directly from the Hold panel through the normal
simulation command path. While travelling, the panel remains available for review but does not
permit trade. An empty hold has an explicit empty state and still shows total available capacity.

Cost basis is simulation state and must survive save/load. Values and profit estimates must be
labelled as current, stale, or unavailable; the interface must not reveal information the player
has not received.

### Information architecture

Additional information should be presented through stable layers rather than accumulating in one
sidebar or event feed:

1. **Persistent status:** time controls, date, player cash, location or voyage, ship condition, and
   cargo used versus capacity.
2. **Context sidebar:** tabs for **Market**, **Hold**, and **Port**. These contain information and
   actions used repeatedly during ordinary play.
3. **Reference overlays:** Market History, News, Opportunities, Journal, and Credits use the paper
   overlay and can be closed without changing the selected port or commodity.
4. **Urgent decisions:** encounters, expiring choices, destructive confirmations, and serious
   errors use focused modal overlays and pause simulation time.
5. **Map signals:** markers and route treatments summarize geography, traffic, weather, danger, and
   available opportunities; selecting one opens its detailed context rather than placing prose on
   the map.

The information surfaces have distinct responsibilities:

- **Market** answers what can be traded here, at what price, and under what stock condition.
- **Hold** answers what the player owns, what it cost, what it may be worth, and what can be sold.
- **Port** explains local identity, services, access rules, factions, and connected routes.
- **News** records reports about world events and clearly labels their location, age, source, and
  confidence.
- **Opportunities** lists actionable contracts and situations with requirements, deadlines,
  rewards, risks, and affected locations.
- **Journal** records persistent characters, local story progress, player choices, and unresolved
  story leads.
- **Market History** shows price and stock history plus known causes for significant changes.

Urgent events may interrupt the player, but ordinary news, AI activity, and price updates must not.
Repeated low-value events should be grouped, and filters, unread counts, priority, and progressive
disclosure should prevent important information from being buried. A headline opens a summary,
which can then link to its affected port, route, commodity, opportunity, or journal entry.

Facts, estimates, rumours, and stale information require visible text labels or symbols and must
never be distinguished by colour alone. The same selected port and commodity should carry across
the Market, Hold, history, news, and opportunity views to reduce navigation work.

### Market venues and local identity

Ports may contain several economically distinct venues without duplicating the entire interface:

- A public market for immediate trades at ordinary quotes
- A contract board for fixed quantities, destinations, prices, and deadlines
- Reputation-gated private buyers or suppliers
- A black market with limited stock, legal risk, and uncertain counterparties
- A ship chandlery for supplies, repairs, and equipment

Venues should differ through availability, access, limited quantities, fees, deadlines, legality,
and risk rather than arbitrary price multipliers. Rules must prevent effortless same-port
arbitrage. Initially, venues may share the port's underlying inventory while offering constrained
orders or services against it.

The initial ports establish the following narrative and economic identities:

- **Crown's Haven:** navy, customs, grain warehouses, cloth merchants, inspections, official
  contracts, and wartime provisioning.
- **San Cordelia:** sugar estates, timber yards, shipbuilding, harvest politics, merchant families,
  and export disputes.
- **Blackwater Cay:** rum, smugglers, treasure hunters, pirate brokers, stolen cargo, and unreliable
  information.

### Asset licensing and acknowledgements

`assets/acknowledgements.html` records the supplied Flaticon attributions and should be included in release packages and exposed from the credits screen. Before release, each acknowledgement should identify the corresponding asset filename, and the provenance or license for the map, paper, soundtrack, ship artwork, and fonts should also be recorded.

The soundtrack uses the MP3 version only. The removed WAV contained the same soundtrack and is not needed.

## 7. Recommended Starter Scaffold

```text
PirateTrading/
├── pyproject.toml
├── README.md
├── src/
│   ├── pygame_template.py
│   └── pirate_trading/
│       ├── __init__.py
│       ├── __main__.py
│       ├── settings.py
│       ├── models.py
│       ├── content.py
│       ├── economy.py
│       ├── simulation.py
│       ├── diagnostics.py
│       ├── persistence.py
│       ├── headless.py
│       └── ui.py
├── assets/
├── content/
│   ├── commodities.yaml
│   ├── ports.yaml
│   └── routes.yaml
├── tests/
│   ├── test_content.py
│   ├── test_economy.py
│   ├── test_simulation.py
│   ├── test_determinism.py
│   ├── test_persistence.py
│   └── test_ui.py
├── saves/
│   └── quicksave.json
└── output/
    └── milestone_0_events.jsonl
```

This scaffold contains only what the headless economic experiment needs. Generated output is ignored by Git.

Keep Milestone 0 in these flat modules. Introduce packages such as `presentation`, `persistence`, `navigation`, `encounters`, `factions`, and `narrative` only when their milestones begin. Split existing modules only when their actual size or responsibilities justify it.

The important boundary is not the number of packages. It is that `simulation`, `economy`, and `ai` remain runnable without importing pygame or presentation code.

## 8. Development Roadmap

### Milestone 0 — Headless economic experiment

Status: Implemented

Build:

- Central settings and validated YAML content for three ports and five commodities
- Serializable world state, stable identifiers, canonical hashes, and economic ledgers
- Fixed hourly ticks and independent seeded random streams
- Inventory-driven pricing, port production and consumption, and bounded storage
- Three merchants using the same buy, sell, and voyage commands
- A single `pirate-trading` headless command with summary and JSONL diagnostics
- Unit, integration, determinism, and 10,000-tick stability tests

Exit criteria:

- The same seed and command sequence produce identical state hashes after 10,000 ticks.
- Commodity conservation tests pass.
- No inventory, price, cash, or travel duration becomes invalid during a long simulation.
- Merchants complete profitable routes without player input.
- A developer can inspect why a merchant chose a route and why a price changed.

### Milestone 1 — Playable trading vertical slice

Status: Implemented

Build:

- Import-safe pygame loop adapted from the project template
- Atlantic map, three ports, visible routes, player marker, and AI traffic
- Player cash, cargo, immediate dockside trading, and empty repositioning voyages
- Text-first commodity table with stock status, bid, ask, and daily price trend
- Pause and 4/8/16-hour time controls with automatic pause on player arrival
- Market-history charts, recent news, credits, music, and asset caching
- Versioned atomic quick-save with New Game, Continue, Save, and Load
- Separate `pirate-trading` UI and `pirate-trading-headless` console commands

Exit criteria:

- A new player can complete a buy-sail-sell loop without developer guidance.
- AI and player transactions use the same market rules.
- The player can explain at least one price movement using information shown by the game.
- Saving, loading, and continuing produces the same outcome as uninterrupted play.
- A 30-minute play session contains multiple viable trade choices.

### Milestone 1.1 — Cargo and information foundation

Status: Implemented

Build:

- Persistent cargo-capacity summary and dedicated data-driven Hold panel
- Market, Hold, and Port sidebar tabs with shared port and commodity selection
- Direct dockside selling from holdings through the existing command path
- Cost basis, current value, information age, and capacity presentation
- Stable overlays and navigation entry points for News, Opportunities, Journal, and Market History
- Notification priority, grouping, unread state, and visible fact/estimate/rumour labels

Exit criteria:

- The player can identify every commodity held, its quantity, cost basis, capacity use, and known
  value without visiting another port or reading an event log.
- Selling from Market and Hold views produces identical validated simulation commands.
- Adding a commodity creates appropriate Market and Hold rows without artwork or
  commodity-specific UI code.
- The player can move from a headline to the affected port, route, commodity, or opportunity
  without losing their previous context.
- Important encounters and expiring decisions are visible without routine simulation activity
  repeatedly interrupting play.

### Milestone 2 — Risk and route decisions

Build:

- Expand to the MVP's 5 ports and 8 commodities
- Supplies, wages, maintenance, and travel costs
- Persistent causal world-event framework and lifecycle
- Weather conditions and route modifiers that affect actual traffic and production
- Incomplete market knowledge, delayed reports, rumours, and information age
- Basic encounter framework and pirate threat
- Ship upgrades that create meaningful trade-offs

Exit criteria:

- The nominally most profitable route is not always the best choice after cost and risk.
- Weather and information age visibly affect player decisions.
- Relevant visible events have useful but bounded predictive value for subsequent market movement.
- Counterfactual, conservation, replay, and multi-seed stability tests pass.
- At least three economically viable ship configurations or strategies exist.
- Automated simulations do not converge to permanent universal shortages or price caps.

### Milestone 3 — Naval interaction

Build:

- Detection, pursuit, escape, and surrender
- First combat implementation
- Damage, repairs, crew impact, and cargo loss
- Pirate and naval behaviours
- Rewards and consequences that feed back into trade routes and markets
- Robberies, battles, and treasure discoveries operate on persistent actors, cargo, cash, and
  explicit economic ledgers

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
- Narrative observer, authored local story spines, and simulation-driven connective events
- Distinct public, contractual, private, and illicit opportunities at each port
- Multiple long-term progression paths

Exit criteria:

- Contracts reflect valid world conditions and update when those conditions change.
- Faction changes affect routes, prices, access, or safety in visible ways.
- Every port has a recognizable economic identity, ongoing local storyline, recurring interests,
  and opportunities unavailable elsewhere.
- News reports simulation facts or explicit rumours rather than inventing unsupported world state.
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

## 9. Testing and Observability

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

## 10. Working Practices

- Keep the default branch runnable and tested.
- Implement features in small vertical slices.
- Write a short design note for changes that affect simulation order, save compatibility, or major player rules.
- Document public interfaces and non-obvious invariants.
- Store tunable values in validated content files, but avoid turning every constant into configuration.
- Profile before optimizing or introducing specialized numerical libraries.
- Record notable player-facing changes in the changelog.
- Review scope at the end of every milestone.

Documentation should describe decisions that developers or players need to understand. It should not be created merely to satisfy a prescribed document list.

## 11. Immediate Starting Sequence

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

## 12. Definition of Success

The project succeeds when players believe the world is alive because they can observe its causes, anticipate its changes, and make plans that matter.

For every proposed feature, ask:

1. What new decision does this give the player?
2. How will the player perceive its cause and effect?
3. Which existing system does it meaningfully interact with?
4. How will we test that it remains stable and understandable?

If those questions do not have convincing answers, simplify, redesign, or remove the feature.
