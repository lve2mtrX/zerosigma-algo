# Phase 10 — Historical / Snapped-Data Backtest Adapter (implementation plan)

Status: **PLAN** (Phase 9H prep). A minimal read-only scaffold exists
(`src/replay/`, `scripts/discover_replay_data.py`); the full adapter is Phase 10.

## 0. Goal + non-negotiables

Replay **saved exposure snapshots** through the **same** scanner → strategy →
selector → paper-lifecycle path used by live paper testing, so backtest results
are directly comparable to forward results.

- **No strategy/selector/lifecycle fork.** Reuse `run_scanner.main(argv)` (the
  Phase 7 in-process seam), the Phase 5 daily selector, and the Phase 9B paper
  lifecycle verbatim. The ONLY new code is a data *source* (a replay structure
  provider) + an orchestrator that steps through snapshots.
- **Read-only.** Consumes saved snapshots; never writes snapshots, never hits a
  broker, never places/previews an order.
- **Deterministic.** Same snapshots + same preset → same results.

## 1. Data discovery step

`scripts/discover_replay_data.py` (already scaffolded) scans the known capture
roots and reports counts / sample paths / top-level keys, and smoke-maps one
snapshot through the shared loader. Run it first:

```
python -m scripts.discover_replay_data
python -m scripts.discover_replay_data --root outputs/replay --root data/snapshots
```

Today it reports **0 files** — there is no capture step yet (see §6). Discovery
is the gate: Phase 10 implementation starts once real snapshots land.

## 2. Saved exposure file locations

Default read-only roots (`src/replay/snapshot_loader.DEFAULT_SNAPSHOT_ROOTS`):

```
outputs/snapshots/   outputs/replay/   data/snapshots/   data/replay/
snapshots/           replay/
```

Recommended capture layout (one file per symbol per tick):

```
outputs/replay/<SYMBOL>/<YYYY-MM-DDTHH-MM-SS>.json
```

`.gitignore` already ignores `outputs/**` so captures never get committed.

## 3. Snapshot schema review

Two accepted shapes (`snapshot_loader.load_snapshot_record`):

**(a) Raw `/market/snapshot` payload** — exactly what the live ZS provider
consumes (`exposures.{total_gex_1pct, total_da_gex_1pct, gamma.{regime, flip,
cluster_primary, cluster_secondary}, max_call_oi_strike, max_put_oi_strike,
max_call_vol_strike, atm_strike}`, `spot.spot`, `chain.{expiry,dte}`).

**(b) Capture bundle** (preferred for replay — keeps the volume series together):

```json
{
  "symbol": "SPX",
  "captured_at": "2026-06-03T15:15:00-04:00",
  "snapshot": { ...the /market/snapshot payload... },
  "exposure_series": { "strikes": [...], "calls": [...], "puts": [...] }
}
```

The `exposure_series` block is what unlocks the **2K / 5K / 10K** wing tiers +
MaxVol; without it only the single-level public wings are available (10K stays
None, exactly like live public/wings-only data).

## 4. Wingonomics file review

"Wingonomics" is the ZerσSigma exposure/wing methodology that produces the
PUT_CEILING / CALL_FLOOR levels by walking per-strike volume against a threshold
(2K / 5K / 10K). In THIS repo it is encoded by the live mapper
(`zerosigma_api._highest_strike_where` / `_lowest_strike_where` at thresholds
2000 / 5000 / 10000) and mirrored by the stub against the mock chain. There is
**no separate wingonomics data file** in this repo today.

Phase 10 action: if Dan has external wingonomics CSV/JSON exports (per-strike
volume by timestamp), point discovery at them and add a small adapter that emits
the §3(b) `exposure_series` block from those columns — then the existing mapper
derives all wing tiers unchanged.

## 5. Mapping saved exposures → `StructureSnapshot`

DONE (scaffold). `snapshot_loader.map_payload_to_snapshot(payload, vol_series,
symbol=..., source="replay")` delegates to
`ZeroSigmaApiStructureProvider.build_snapshot_from_payload` — the **same**
mapping the live provider uses (extracted in Phase 9H precisely so replay can
reuse it). This guarantees replayed snapshots carry identical fields, including
the Phase 9H 10K wings + primary/secondary gamma.

Phase 10 will wrap this in a tiny `ReplayStructureProvider` that satisfies the
`StructureProvider` protocol (`get_snapshot` returns the next snapshot in the
replay sequence), so the scanner consumes it like any other provider.

## 6. Quote / chain data availability check

The scanner needs a `QuoteProvider` chain in addition to structure. Saved
exposure snapshots **do not** contain per-strike bid/ask/mid. Options:

1. **Captured chain alongside structure** — if the capture also saved a
   `/market-data` chain, replay it via a `ReplayQuoteProvider`. Most faithful.
2. **MockQuoteProvider re-centered on the snapshot spot** — reuse the Phase 2.6
   `QuoteRequest.spot_hint` + `required_strikes` path so the mock chain centers
   on each snapshot's spot. Lets structure/selector logic run, but P&L uses
   synthetic prices (directional/structural study, not fill-accurate).
3. **`null` quote provider** — structure-only replay (selector eligibility +
   wing/gamma study) with no pricing.

## 7. Fallback assumptions when quote data is missing

- No captured chain → default to **MockQuoteProvider re-centered on snapshot
  spot** (option 6.2), and stamp every replayed result `quote_source=mock_replay`
  so it is never mistaken for real fills.
- Missing `exposure_series` → 10K (and possibly 5K) wings are None; the selector
  still runs on the tiers that are present. Log which tiers were unavailable.
- Missing gamma clusters → primary/secondary gamma derive from walls/flip (the
  Phase 9H `cockpit_helpers.primary_secondary_gamma` fallback), labelled derived.
- Missing spot → skip the tick (cannot price or center a chain); count it.

## 8. Same selector path reuse

Feed each replayed `(structure, chain)` through the existing
`strat.generate_candidates → apply_filters → strat.score → strat.select`, then
the existing `select_daily_trade(rows, SelectorConfig.from_profile(...))`. The
preset's `daily_selector` (incl. Phase 9G `balanced_structure_premium_valid`)
drives selection unchanged. No selector code is copied or forked.

## 9. Same paper lifecycle reuse

Route `selected_trade=True` rows into the Phase 9B paper lifecycle
(`src/paper/lifecycle.py`) with `PaperLifecycleConfig` sourced from the preset
(once Phase 9G per-profile TP/SL wiring lands) or PAPER_* env today. Re-price
open spreads from each subsequent replayed tick's candidates (same
`(side,short,long,expiry)` match the live lifecycle uses). Reuse the Phase 9B
ledger writers so backtest output files match forward output files 1:1.

## 10. Output comparison by preset

Run each preset over the SAME snapshot sequence and emit a per-preset summary
(reuse `review_portfolio_forward` shapes): trades, win rate, realized/total P&L,
max drawdown, avg hold, exit-reason histogram (TP/SL/EOD). Then a comparison
table keyed by preset so the questions below are answerable side-by-side.

## 11. Backtest questions this must answer

| Question | How the adapter answers it |
|---|---|
| Dynamic both-side vs fixed call-only controls | Compare `*_dynamic_*` vs `*_call_*_control` presets over the same snapshots |
| 2K vs 5K vs 10K wing thresholds | Vary the wing tier the strategy anchors on; requires `exposure_series` (10K) |
| Morning vs EOD windows | Compare `morning_*` vs `eod_*` presets (entry_window/target_time) |
| TP75 vs TP50 vs no TP | Compare `*_tp75` / `*_tp50` / `*_no_tp` presets (TP wiring required) |
| SL150 vs SL200 | Compare `*_sl150_*` vs `*_sl200_*` presets |
| Dynamic selector performance | Inspect `balanced_structure_premium_valid` selection rate + P&L vs controls |
| Put-credit regime test effectiveness | Run `regime_put_credit_test` over put-favorable snapshot windows |

## 12. Suggested Phase 10 build order

1. Capture step (writes §3(b) bundles) — likely a small read-only poller or an
   adapter over Dan's existing wingonomics exports.
2. `ReplayStructureProvider` + `ReplayQuoteProvider` (protocol-conformant).
3. `scripts/run_backtest.py` — steps snapshots through `run_scanner.main(argv)`
   per preset, writes per-preset ledgers under `outputs/backtest/`.
4. `scripts/review_backtest.py` + a preset-comparison table.
5. Tests: snapshot→snapshot mapping parity with live; selector parity; lifecycle
   parity; deterministic replay.

Nothing in this plan introduces broker execution, order preview, or order
placement. Backtesting is local, read-only, paper-only.

---

## 13. Discovered real data sources (Phase 9I research)

`python -m scripts.discover_backtest_sources` (read-only; HOME/env-derived paths,
**no hardcoded username**) located the following under `~/Dropbox/Trading`
(override with `ZSA_TRADING_ROOT` or `--root`):

| Source | Path (relative to trading root) | Kind | Usable | Notes |
|---|---|---|---|---|
| **SPX per-strike exposures (PRIMARY)** | `TOS Data/Daily Exposures/SPX/SPX_RAW_*.csv` | per-strike per-tick CSV (~145 days, Oct 2025→) | ✅ yes | `Strike`, `CALL Volume`, `PUT Volume`, `SPX_Spot`, Greeks, bid/ask present |
| SPX 1DTE exposures | `TOS Data/Daily Exposures/SPX_1DTE/SPX_RAW_1DTE_*.csv` | same schema (~78 days) | ✅ yes | for 1DTE preset replay |
| Wingonomics outputs | `TOS Data/WINGONOMICS/` | `wingonomics_daily_stats.csv` (62 cols, ~140 days) + `wingonomics_latest.json` | 🟡 validation | aggregated per-day wing levels + entry/exit times + outcomes — VALIDATE replay vs these |
| Wingonomics script (reference) | `TOS Data/0 - Strategies_Backtesting/wingonomics/scripts/wingonomics.py` | Python (read-only, **do not modify**) | 📖 reference | wing-detection logic to mirror |
| Greek_Data_MASTER_CURRENT.xlsm | repo-sibling Excel (~822 KB) | binary workbook | ⬜ no | reference master only |
| DeltaDrift Daily Snapshots | `TOS Data/DeltaDrift Daily Snapshots/` | ~120 PDF reports | ⬜ no | visual only |

### Wingonomics validates our approach (do NOT modify it)

`wingonomics.py` reads the SAME `SPX_RAW_*.csv` files, filters RTH, and computes
10K wing levels by **volume threshold** — `call_floor = min(strike where CALL
Volume ≥ 10000)`, `put_ceiling = max(strike where PUT Volume ≥ 10000)`. This is
*exactly* the ZS mapper's `_lowest/_highest_strike_where(..., 10000.0)` (Phase
9H). So replaying the raw CSVs through our pipeline should reproduce wingonomics'
wing levels — `wingonomics_daily_stats.csv` (with its `entry_active_time`,
`exit_time`, `*_call_floor`, `*_put_ceiling`, `initial_10k_*`, breach columns) is
the ground-truth to validate against. We CONSUME these as reference; we never run
or edit wingonomics.

### ETL: SPX_RAW CSV → capture bundle → StructureSnapshot

Per timestamp row-group in `SPX_RAW_<date>.csv` (one group per `time` within a
`session=RTH` day):
1. `strikes` = sorted unique `Strike`; `calls` = `CALL Volume` per strike;
   `puts` = `PUT Volume` per strike; `spot` = `SPX_Spot`.
2. Emit a §3(b) capture bundle: `{symbol, captured_at: <date>T<time>, snapshot:
   {spot:{spot}, exposures:{...net GEX/DEX/VEX from NET columns, gamma clusters
   if derivable}}, exposure_series:{strikes, calls, puts}}`.
3. `snapshot_loader.load_snapshot_record(bundle)` → `StructureSnapshot` with
   2K/5K/**10K** wings derived by the SHARED live mapper (no fork).

Quote/chain: the CSVs also carry `CALL BID/ASK`, `PUT BID/ASK` per strike, so a
`ReplayQuoteProvider` can build a real `OptionChainSnapshot` from the same rows
(more faithful than mock). When a strike's bid/ask is missing, fall back to the
mock re-centered on snapshot spot (§7) and stamp `quote_source=mock_replay`.

### Path configuration (no hardcoded user)

Discovery + the future capture step resolve roots from, in order: `--root` CLI →
`ZSA_TRADING_ROOT` env → `~/Dropbox/Trading` (`Path.home()`). Extra scan dirs via
`ZSA_BACKTEST_DIRS` (os.pathsep-separated). No Windows username is ever hardcoded
in code; the example paths in this doc are illustrative only.

### Updated Phase 10 build order (concrete)

1. `scripts/capture_exposures.py` — read-only ETL: `SPX_RAW_*.csv` → per-tick
   capture bundles under `outputs/replay/SPX/<date>/<time>.json`.
2. `ReplayStructureProvider` + `ReplayQuoteProvider` (build chain from CSV bid/ask).
3. `scripts/run_backtest.py` — replay bundles through `run_scanner.main(argv)` per
   preset + the Phase 9B paper lifecycle → `outputs/backtest/<preset>/`.
4. `scripts/review_backtest.py` — per-preset P&L / drawdown / win-rate / expectancy
   / no-trade-reason table; cross-check wing levels + entry/exit vs
   `wingonomics_daily_stats.csv`.

## 14. Phase 10A landed (Phase 9J) — SPX_RAW loader + WDS

The first ETL step is built and validated on real data:

- `src/replay/spx_raw_loader.py` — reads `SPX_RAW_<date>.csv` (RTH filter,
  group-by-timestamp, builds `{strikes, calls, puts, spot}`) and maps ONE timestamp
  to a `StructureSnapshot` via the SHARED `map_payload_to_snapshot` (no fork). So
  2K/5K/10K wings AND the Phase 9J W2/WDS inputs derive identically to live.
- `scripts/backtest_spx_raw.py` — read-only CLI (HOME/env paths, no hardcoded
  username) that prints available dates + a sample mapped structure with its true
  **WDS**. Validated: 145 dates (2025-10-31 → 2026-06-03); a midday tick yields
  formed 10K wings and a real dominant-wing WDS read.

This is loader-only — no `run_backtest`, no scanner/lifecycle run yet (that is
Phase 10B). Next: `ReplayStructureProvider` (sequence the snapshots) +
`ReplayQuoteProvider` (build the chain from the CSV `CALL/PUT BID/ASK` columns) →
`run_scanner.main(argv)` per preset → paper lifecycle → per-preset comparison vs
`wingonomics_daily_stats.csv`.

## 15. Phase 10A landed — multi-symbol backtesting module + wing corridor

The Phase 9J single-symbol `spx_raw_loader` is generalised into a **pure,
multi-symbol** backtesting package and a small CLI surface. Everything still maps
into the SAME live shapes (no strategy/selector fork); nothing here runs a scanner,
hits a broker, previews an order, or calls a live API.

### `src/backtesting/` (new pure package)

- **`schemas.py`** — `SymbolConfig` (per-symbol spot column + wing thresholds +
  note), `symbol_config(symbol)` for `SPX/SPY/QQQ`, required structure cols
  (`timestamp`, `Strike`, `CALL Volume`, `PUT Volume`, `<SYM>_Spot`) + pricing cols
  (`CALL/PUT BID/ASK`) + optional metric cols, `ENTRY_WINDOWS` (Morning `11:00`
  ±5 min, EOD `15:00/15:15/15:30` ±15/±30 min), `RTH` bounds, `DTE_0`/`DTE_1`, and
  the `<SYM>` / `<SYM>_1DTE` sub-folder + `<SYM>_RAW[_1DTE]_*.csv` glob rules.
  Thresholds default to 2K/5K/10K for ALL symbols, with SPY/QQQ explicitly flagged
  **provisional** (symbol-specific calibration is a Phase 10B task).
- **`raw_snapshot_loader.py`** — `trading_root(cli)` resolves `--trading-root` →
  `ZSA_TRADING_ROOT` env → `~/Dropbox/Trading` (no hardcoded username);
  `parse_timestamp` handles mixed forms (ISO-offset / `YYYY-MM-DD HH:MM:SS` /
  compact) and normalises tz-aware values to America/New_York wall time; RTH filter
  (by `session=="RTH"` or the 09:30–16:00 window); symbol-aware spot column; 0DTE
  globs exclude the `1DTE` files. Pure: `available_dates`, `file_for_date`,
  `available_timestamps`.
- **`mappers.py`** — `select_snapshot(timestamps, target)` (closest |delta| inside
  the entry window; ties prefer **at-or-after** via `abs(delta)*2 + (delta<0)`),
  `map_structure` → `StructureSnapshot` and `map_option_chain` → `OptionChainSnapshot`
  (both via the SHARED live mapper / bid-ask quotes, `source="backtest_raw"`),
  `vertical_credit` (mid-to-mid), `corridor_wds` (= `ch.wing_dominance`), and
  repo-local `output_base/latest_dir/run_dir` (honor `OUTPUT_DIR`/`DATA_DIR`, else
  `<repo>/outputs`, ALWAYS under `…/backtests/`; never the raw data folders).

### MANDATORY wing-corridor validity (CW1 < Spot < PW1)

A 10K wing structure is **ACTIVE only when the call floor is below spot AND the put
ceiling is above spot** — i.e. `CW1 (call_floor_10k) < spot < PW1 (put_ceiling_10k)`.
A call floor priced ABOVE spot is NOT an active floor; a put ceiling BELOW spot is NOT
an active ceiling. This is encoded once, in pure code, and reused live + in backtest:

- `cockpit_helpers.wing_corridor_status(spot, cw1, pw1)` →
  `{corridor_valid, cw1, pw1, spot, reason, side_read}`. Missing CW1/PW1 → invalid;
  `CW1 >= spot` → `"CW1 is not below spot."`; `PW1 <= spot` → `"PW1 is not above spot."`;
  `CW1 < spot < PW1` → valid.
- `wing_dominance` GATES the dominant wing on the corridor: `wds_active =
  corridor_valid and raw_dominant is not None`. When the corridor is not formed the
  raw WDS is still computed but surfaced as **context-only** (`raw_wds_source="true"`,
  `dominant_wing_side="unavailable"`, `wds_source="unavailable"`) — never as active
  structure.
- Operator read leads with **"Structure status: Active corridor"** /
  **"Inactive — corridor not formed"**; the nearest 2K/5K wing is framed as immediate
  breach risk, NOT the primary structure, when the corridor is invalid.
- The selector receives `corridor_valid` + `wds_active` metadata and grants **no
  positive structure credit** when the corridor is invalid (display-only/deferred this
  pass; WDS→selector weighting stays a Phase 10B item).
- The scan CSV records `corridor_valid / cw1 / pw1 / corridor_reason / raw_wds /
  active_wds` per snapshot, so corridor state is auditable per date.

### CLIs (all read-only, HOME/env paths, no hardcoded username)

```
python -m scripts.discover_backtest_sources --symbols SPX SPY QQQ --include-1dte
python -m scripts.backtest_dry_run   --symbol SPX --profile morning_5k_dynamic_tp75 --latest --entry 11:00
python -m scripts.backtest_scan_dates --symbol SPX --profile eod_5k_dynamic_sl150_no_tp --start 2026-05-01 --end 2026-06-03 --entry 15:15 --limit 10
```

Discovery reports per symbol×DTE: folder, file count, date range, sample file, spot
column presence, structure/pricing/optional column coverage, and usable-for-structure /
usable-for-pricing. **1DTE is DISCOVERY-ONLY in Phase 10A** (SPX 1DTE is found and
reported; full 1DTE strategy logic is future). Dry-run maps ONE entry snapshot and
prints spot, 2K/5K/10K wings, corridor + WDS, gamma, candidate vertical spreads and
chain priceability. Scan-dates writes one row per entry snapshot to
`outputs/backtests/latest/scan_<SYM>_<DTE>_<HHMM>.csv` AND a timestamped run dir.

### Validated on real data (no fixtures)

`SPX` 145×0DTE (2025-10-31 → 2026-06-03) + 78×1DTE; `SPY`/`QQQ` 66 each. SPX
2026-06-03 @ 11:00:15 → corridor **ACTIVE** (`7575 < 7578.55 < 7600`), dominant
**PUT_CEILING 10K WDS 58% Tier 2**, both candidate spreads priceable (162 chain
quotes). The exact bug the corridor rule fixes is real in this data: an earlier SPX
midday tick had `call_floor_10k 7560 > spot 7557.74` → correctly reported **INACTIVE
— corridor not formed** instead of "dominant CALL_FLOOR 10K".

Tests: `tests/test_phase10a_corridor.py` (12 — corridor valid/invalid/missing, WDS
raw-not-active when invalid, operator read inactive vs active) +
`tests/test_phase10a_backtest.py` (15 — discovery, mixed timestamps, symbol spot col,
RTH, snapshot selection/ties, structure+WDS mapping, chain pricing, dry-run + scan CLIs
on a synthetic root, 1DTE discovered-but-future, no-hardcoded-username, no-execution
tokens).

Still loader/mapping-only — **no** `run_backtest`, no scanner/selector/lifecycle run
(that is Phase 10B): `ReplayStructureProvider`/`ReplayQuoteProvider` over these mapped
snapshots → `run_scanner.main(argv)` per preset → paper lifecycle → per-preset P&L /
drawdown / win-rate comparison vs `wingonomics_daily_stats.csv`, plus SPY/QQQ wing
calibration and full 1DTE support.

## 16. Phase 10B landed — replay runner + lifecycle sim + reports

The historical replay RUNNER is built. It drives the SAME live path end to end and
SIMULATES the exit — no strategy/selector fork, no broker, no order preview, no
Tastytrade, no ZerσSigma live API.

### Reused live path (the durable bit — no fork)

```
saved raw file → mappers.map_structure / map_option_chain   (Phase 10A)
              → VerticalWingV1.generate_candidates           (the live strategy)
              → risk.filters.apply_filters                   (the live risk caps)
              → VerticalWingV1.score
              → selector.readiness.compute_readiness         (the live readiness)
              → selector.daily_selector.select_daily_trade   (the live Phase 5 selector)
              → lifecycle_sim.simulate_exit                  (NEW historical exit sim)
```

Candidate construction is NOT re-implemented — `generate_candidates` already builds the
CALL_CREDIT (short at PUT_CEILING, long one strike higher) + PUT_CREDIT (short at
CALL_FLOOR, long one strike lower) verticals at the 2K/5K tier. The backtest only feeds
it the profile-derived `volume_threshold` / `spread_width`. Side filtering, structure-vs-
premium balancing, and quote/risk validity are the live selector's job (`select_daily_trade`).

### New modules (`src/backtesting/`)

- **`profile_runtime.py`** — `derive_run_settings(profile)` reads behavior from PROFILE
  FIELDS (`target_time`, `threshold_label`/`wing_threshold`, `allow_*_credit`,
  `daily_selector`, `take_profit_pct`, `stop_loss_pct`, `target_dte`), never by name.
  TP/SL semantics: `take_profit_pct` = credit-CAPTURE fraction (TP75 → debit ≤ 0.25×credit;
  TP50 → ≤ 0.50×credit); `stop_loss_pct` = LOSS fraction (SL150 → debit ≥ 2.5×credit;
  SL200 → ≥ 3.0×credit) — matching the reference backtest. `selector_config_from_profile`
  mirrors `run_scanner`'s SelectorConfig build. `threshold_scheme(symbol)` returns
  `spx_2k5k10k_standard` for SPX and `provisional_spx_2k5k10k` + a warning for SPY/QQQ.
- **`replay_providers.py`** — `ReplayStructureProvider` / `ReplayQuoteProvider` wrap the
  mapped snapshots and satisfy the provider shape (`get_snapshot` / `get_option_chain` /
  `get_spot` / `status`); `provider_name="backtest_raw"`. No network, no broker.
- **`lifecycle_sim.py`** — `build_day_index(rows, symbol)` indexes the day once
  (ts→strike→(call_mid,put_mid) + spot); `simulate_exit(...)` walks post-entry snapshots
  in `(entry_ts, settlement_ts]`, reprices `debit = short_mid − long_mid` (same side),
  fires TP/SL on the first event (SL wins a same-snapshot tie → `event_conflict`), and
  EOD-settles to cash-settle INTRINSIC at the first snapshot in `[16:00, 16:20]`. Exit
  fields: `exit_reason` (TP/SL/EOD/SKIPPED), `exit_debit_points/_dollars`, `pnl_points/
  _dollars`, `credit_kept_pct`, `hold_minutes`, max/min spot after entry, short/long touch
  flags, `snapshots_checked`, `missing_price_count`, `settlement_method`. Points → dollars
  is `× 100` once (1 contract).
- **`replay_runner.py`** — `run_backtest(...)` iterates dates (loads each day's rows once,
  shared across profiles), selects the entry snapshot, maps, runs the reused pipeline,
  records corridor/WDS/gamma per snapshot, and simulates the selected trade. `BacktestResult`
  carries candidates / trades / no_trade_reasons / counters. `resolve_profiles` expands
  `all-main` (4 primary) / `all` (+6 controls).
- **`reports.py`** — pure aggregation + writers: `trades.csv`, `candidates.csv`,
  `daily_pnl.csv`, `equity_curve.csv`, `summary_by_{profile,symbol,corridor,wds_tier}.csv`,
  `no_trade_reasons.csv`, `run_config.json`. Metrics: win rate, total/avg P&L, expectancy,
  gross wins/losses, profit factor, max drawdown + duration, avg credit/risk/distance,
  TP/SL/EOD counts, CALL vs PUT frequency, active vs inactive corridor counts, WDS-tier
  breakdown.

### CLI

```
python -m scripts.backtest_run --symbol SPX --profile morning_5k_dynamic_tp75 \
    --start 2026-01-01 --end 2026-06-03 --dte 0 --run-label test
python -m scripts.backtest_run --symbol SPX --profile all-main --latest-days 20 \
    --dte 0 --run-label smoke
```

### Validated on real data

SPX morning_5k_dynamic_tp75 5-day → 3 trades, +$45, 67% win, TP/SL/EOD 2/1/0. SPX all-main
20-day → 17 trades, TP/SL/EOD 7/6/4, win 0.59. SPY all-main 8-day → 52 candidates MAPPED
but 0 selected (SPX-calibrated thresholds are wrong for SPY — flagged provisional, not
over-interpreted). All outputs land under `outputs/backtests/`. Tests:
`tests/test_phase10b_backtest.py` (24 — candidate construction both sides, call/put-only
exclusion, observe selects nothing, TP75/TP50/SL150/SL200/EOD/SKIPPED exits, missing-price
counting, daily P&L + equity/drawdown, corridor + WDS-tier summaries, CLI smoke repo-local,
no-execution + no-hardcoded-user guards).

### Still Phase 10C (next)

SPY/QQQ threshold calibration + cross-check vs `wingonomics_daily_stats.csv`; contracts
sizing + comparison dashboards; promote corridor/WDS into selector weighting; full 1DTE
support (SPX 1DTE data exists; QQQ_1DTE empty; SPY_1DTE absent). Still NO broker execution,
NO order preview.

## 17. Phase 10E — comparison dashboard + research promotion labels

Phase 10E wraps the existing multi-profile replay result in a pure comparison layer. It
does not add a replay fork and does not feed any ranking or promotion label back into the
live selector.

### CLI

```
python -m scripts.backtest_compare --symbol SPX --profiles all-main --dte 0 \
    --latest-days 20 --starting-balance 10000 --contracts 1 --run-label compare_smoke
```

Comparison cohorts:

- `dynamic-only`: four primary dynamic profiles.
- `controls-only`: four paired call-only controls.
- `all-main`: primary dynamic profiles plus their paired controls.
- `all`: main profiles plus research/observe profiles.
- `custom`: valid saved profiles with no built-in preset kind.
- Explicit profile IDs may be space- or comma-separated.

### Outputs

Outputs land under `outputs/backtests/comparisons/latest/` and a timestamped
`outputs/backtests/comparisons/runs/` directory:

- `comparison_summary.csv`, `profile_rankings.csv`, `dynamic_vs_control.csv`
- `by_profile.csv`, `by_side.csv`, `by_exit_reason.csv`, `by_corridor.csv`
- `by_wds_tier.csv`, `by_entry_window.csv`
- `trades.csv`, `candidates.csv`, `run_config.json`, `narrative_summary.md`

The research ranking exposes all score components in the ranking CSV and documents the
formula in `run_config.json`. Promotion labels are deterministic:

- `Promote to Live Paper Candidate`: at least 10 trades, positive expectancy, profit
  factor above 1, and max drawdown at or below 10%.
- `Watchlist`: positive profile with sufficient trades but drawdown above 10%.
- `Needs More Data`: fewer than 10 trades.
- `Avoid / Control Only`: controls/observe profiles, negative expectancy, profit factor
  at or below 1, or max drawdown above 15%.

These are research labels only. No strategy, selector, risk, quote-validation, order
preview, broker, or execution behavior changes.

## 18. Phase 10F — dynamic selector attribution + control edge audit

Phase 10F explains Phase 10E's finding that dynamic profiles underperformed their
call-only controls. It remains analysis-only: the selected trade is unchanged, and no
recommendation feeds back into profile configuration or selector math.

For every selected dynamic trade, the replay runner records the selected side and best
available opposite-side candidate. The opposite candidate is mechanically simulated with
the same profile TP/SL settings and historical lifecycle simulator, but remains explicitly
hypothetical. Outputs:

- `dynamic_side_attribution.csv`, `selected_side_summary.csv`
- `dynamic_vs_best_opposite.csv`
- `call_control_edge_summary.csv`, `call_control_winners_losers.csv`
- `dynamic_failure_taxonomy.csv`, `dynamic_failure_summary.csv`
- `research_recommendations.csv`, `attribution_summary.{json,md}`

The call-control edge audit summarizes threshold, entry window, credit and distance
buckets, WDS tier, corridor, gamma context, exit reason, day of week, and entry-time
bucket. Failure taxonomy is deterministic and intended to focus the next experiment, not
declare causality.

Promotion wording is deliberately conservative: positive controls are
`Control Positive / Comparison Only`; underperforming dynamics are
`Watchlist / Needs Tuning`. A positive control is the current benchmark, not production
approval.

Backtests → Compare Strategies adds **Why did dynamic underperform?** with selected-side
split, side P&L/win rate/average P&L, dynamic-vs-control P&L, opposite availability,
failure buckets, call-control edge table, deterministic narrative, and research-only
recommendations.
