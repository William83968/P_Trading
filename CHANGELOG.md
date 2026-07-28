# Changelog

All notable changes to Pirate Trading are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Entries before this file was
introduced were reconstructed from Git history and the development roadmap.

## [Unreleased]

### Added

- Expanded the world from three to five ports with Whitecap Reach and Isla Esmeralda.
- Added salt, coffee, and medicine, bringing the market to eight data-driven commodities.
- Added the weekly 2d6 outlook system with eleven threat and fortune settings, an animated reveal,
  persistent weekly heading, and deterministic effects on trade, travel, reports, opportunities,
  and encounters.
- Added causal world conditions including storms, hurricanes, harvests, crop blight, festivals,
  warehouse fires, shipyard commissions, and relief calls.
- Added delayed market and event reports, fact/estimate/rumour labels, report delivery times, and
  local player knowledge.
- Added ship provisions, wages, condition, repairs, and the Expanded Hold, Fine Sails, Reinforced
  Hull, and Crow's Nest upgrades.
- Added introductory location stories and persistent Journal outcomes for all five ports.
- Added situation-driven delivery, reconnaissance, evacuation, salvage, route-protection,
  smuggling, festival, and harvest opportunities.
- Added one-time major-event notices with contextual links to ports, markets, routes, News, and
  Opportunities.
- Added pirate and navy encounters with flee, payment, surrender, and bluff decisions.
- Added persistent local heat, global notoriety, inspections, service restrictions, official
  payoffs, Blackwater fencing, and Lay Low.
- Added a camera over the 1000×706 map world with cursor-anchored zoom, drag panning, player
  following, bounds, and complete-network framing.
- Split the pygame presentation into a `pirate_trading.ui` package while retaining the public
  `run_ui()` interface.
- Added deliberate two-stage ship and port raids. Successful raids now open a persistent manifest
  from which the player chooses real cargo.
- Added clean and stolen cargo provenance throughout holdings, transactions, confiscation,
  fencing, invariants, and saves.
- Added an explosion animation for pirate attacks and successful player raids.
- Added objective-specific mission acceptance, completion, and failure notices.
- Added a shared live mission checklist for cargo, route, passenger, salvage, and contraband
  objectives, including voyage percentage, next action, and disabled-action explanations.
- Added scrollable accepted-mission history to the Journal with objective payloads, locations,
  accepted/resolved times, outcomes, rewards, and penalties.
- Added ledgered penalties for abandoning accepted missions or allowing them to expire.

### Changed

- Increased the default game window from 1280×720 to 1440×810.
- Enlarged and widened parchment overlays so notices and decisions have more reading space.
- Made the Opportunities list and selected opportunity description scroll independently.
- Reorganized the game shell around a persistent status bar, navigation rail, camera workspace,
  collapsible inspector, and contextual action tray.
- Expanded the inspector to Market, Hold, Port, Ship, and Heat views; moved News, Opportunities,
  Journal, and Market History to global navigation.
- Changed remote markets from perfect global knowledge to delivered, age-labelled information.
- Changed opportunity progress to use actual cargo, hold reservations, route traversal, arrivals,
  event losses, deadlines, and issuing ports.
- Split festival and harvest private deliveries into Festival Supply and Harvest Export. Festival
  jobs name a deterministic festive good; harvest jobs use the harvested commodity and target the
  directly connected port with the lowest inventory ratio.
- Limited mission popups to acceptance, completion, and failure; intermediate progress now updates
  live in Opportunities.
- Changed relief calls to require three consecutive unmet-demand days, permit only one active call
  globally, and apply a 21-day cooldown to each port/commodity pair.
- Removed relief calls from the random major-event catalogue and prevented the situation scheduler
  from duplicating their delivery contracts.
- Changed player raids to award commodities rather than automatic cash. Ship raids allow up to
  eight selected units, port raids allow up to fifteen, and hostile pirate losses are capped at
  five units.
- Changed raid damage to scale with claimed cargo and respect Reinforced Hull.
- Restricted lawful sales to clean cargo. Blackwater now fences a selected stolen commodity and
  quantity at 80% of its current bid.
- Kept quick-save format 2 compatible by reconstructing new additive fields after validating the
  original saved-state hash.
- Disabled the unsuitable background soundtrack while a new audio direction is considered.

### Fixed

- Fixed accepted opportunities changing origin or becoming impossible to deliver after revisiting
  their issuing port.
- Fixed accepted objectives losing selection when unrelated offers appeared.
- Fixed opportunity descriptions and objectives rendering behind their action buttons.
- Fixed repeated relief calls and duplicate shortage-delivery opportunities overwhelming ordinary
  trading.
- Fixed port raids automatically selecting and awarding five medicine.
- Fixed stolen medicine appearing permanently stuck without explaining why lawful sale failed.
- Fixed lawful sales consuming stolen quantities or applying partial changes after rejection.
- Fixed cargo cost-basis calculations across mixed clean/stolen sales, fencing, confiscation,
  piracy losses, opportunity delivery, and raid transfers.
- Fixed static navy stamps appearing as decoration on every patrolled route; the marker now
  represents an actual unresolved navy encounter.
- Fixed map markers, routes, labels, weather, explosions, and hit testing disagreeing after camera
  movement.
- Fixed the inspector header being obscured by overlapping layout regions.
- Fixed a new player's hold starting with five occupied units; new games now begin at `0/50`.
- Fixed message-paper content retaining old fixed coordinates after the window and overlays were
  enlarged, which displaced titles, text, charts, images, and buttons from the writable parchment.
- Fixed simulation time continuing while News or Opportunities were being read and notices
  automatically restoring running time after dismissal.
- Fixed unnamed festival cargo, route-less reconnaissance, warehouse salvage without recoverable
  losses, and Blackwater Passage offers generated while already at Blackwater.
- Restored the intraday `HH:00` clock to the persistent status bar.

### Known Limitations

- Naval pursuit and combat remain planned for Milestone 3.
- Faction arcs and longer generated story chains remain planned for Milestone 4.
- Manual multi-session balance acceptance for Milestones 2.1 and 2.2 is still pending.
- Format-1 quick-saves remain incompatible with the living-world state and require a New Game.

## [0.1.0] - 2026-07-23

### Added

- Created the Python 3.13 project foundation with a `src/` package layout, editable installation,
  Conda environment definition, Ruff, pytest, and GitHub Actions.
- Added `pirate-trading` and `python -m pirate_trading` package entry points.
- Added the deterministic Milestone 0 economy with three ports, five commodities, seeded named
  random streams, inventory-derived pricing, production, consumption, AI merchants, conservation
  ledgers, canonical state hashes, JSONL diagnostics, and a 10,000-tick stability run.
- Added the first playable pygame trading interface with the Atlantic map, player ship, AI
  traffic, dockside buying and selling, voyages, pause and speed controls, market history, News,
  credits, and asset caching.
- Added the separate `pirate-trading-headless` command.
- Added atomic versioned quick-saves with New Game, Continue, Save, and Load.
- Added the Milestone 1.1 Market, Hold, and Port tabs with shared commodity and port context.
- Added cargo cost basis, current valuation, profit/loss estimates, remote comparison, explicit
  Max Buy and Max Sell controls, and direct selling from Hold.
- Added grouped News, session unread state, Market History navigation, and initial Opportunities
  and Journal surfaces.

### Changed

- Changed `pirate-trading` from the initial headless runner to the pygame application.
- Preserved the deterministic console simulation under `pirate-trading-headless`.
- Standardized runtime and balance configuration in `settings.py` while retaining ports,
  commodities, and routes as validated YAML content.
- Kept commodity presentation text-first and data-driven so adding content requires no commodity
  artwork.
