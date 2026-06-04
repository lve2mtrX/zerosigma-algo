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
