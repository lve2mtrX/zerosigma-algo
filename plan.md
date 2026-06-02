# ZerσSigma Algo Cockpit — Plan & Architecture

> Phase 1 planning and scaffold. No live execution. No broker connected.
> This document is the source of truth for design decisions; update it as the
> project evolves.

---

## 1. Objective

Build a portable, local, **multi-strategy** algo cockpit that:

1. Pulls pre-computed options structure from the ZerσSigma API (read-only).
2. Generates ranked trade candidates from any registered strategy.
3. Logs every decision (`TRADE_CALL_CREDIT`, `TRADE_PUT_CREDIT`, `NO_TRADE`)
   with rationale.
4. Tracks manually-entered trades + simulated local/paper trades.
5. Produces a clean EOD summary.
6. Exposes all of the above through a local Streamlit cockpit.

It should remain **strategy-agnostic** at the framework level. Vertical Wingy
is just the first strategy registered. The framework must support adding
strategies (iron condors, butterflies, ratio spreads, calendars, single-leg
directional, etc.) without architectural changes.

It must be **portable**. Drop the folder on any machine, create a venv, copy
`.env.example` → `.env`, and run. No hardcoded paths, usernames, or drive
letters.

---

## 2. Phased roadmap

| Phase | Scope | Status |
|---|---|---|
| **0 — Scaffold** | Folder layout, configs, base interfaces, README/plan/notes. | ✅ |
| **1 — Framework + manual flow** | Strategy registry, decision log, manual trade tracker, EOD summary, Streamlit shell. ZS API stubbed. | ✅ |
| **1.5 — Provider split** | Clean separation of StructureProvider (context only) and QuoteProvider (chain pricing). Strategy takes both; scanner + cockpit reflect both timestamps. | ✅ |
| **2 — ZS API wired (read-only)** | StructureProvider implementation against `/api/v1/market/snapshot` + `/api/v1/exposure/series`. Three auth modes (bearer / login / service_token). Subscription-gated endpoints degrade gracefully. | ✅ |
| **2.5 — `public_only` mode + smoke** | Fifth auth mode `public_only` for credentials-free live reads against `/market/snapshot`. Dedicated `scripts.smoke_zs_api` sanitizing output. Cockpit surfaces auth_mode + public_only warnings. | ✅ |
| **2.6 — Structure↔quote alignment + payload-shape fix** | ZS mapper rewritten to read the real worker-written field names (`spot.spot`, `total_gex_1pct`, `total_da_gex_1pct`, `total_vex_1vol`, `total_cex`, `wings.*`, `gamma.regime`/`.flip`, `max_*_oi_strike`, `max_*_vol_strike`). New `QuoteRequest` shape lets the scanner pass `spot_hint` + `required_strikes` to the mock quote provider so chains re-center on live ZS structure (e.g. 7580) instead of the hardcoded 5800. New `Strategy.required_quote_strikes()` keeps the alignment generic. Scanner decision log gains `required_strikes`, `quote_chain_{min,max}_strike`, `missing_required_quote_strikes`, `quote_spot_source`, `quote_spot_hint`. Zero-candidate explanation distinguishes "no structure anchors" / "quote chain missing required legs" / "all candidates rejected by filters". Smoke script gains `--debug-shape` and `--endpoint`. | ✅ |
| **2.7 — Score-breakdown observability** | Every candidate carries `score_breakdown` (every actual component + `final_score` + `no_trade_threshold` + `score_gap_to_threshold`), `weak_components` (two lowest non-meta), `rejection_type` ∈ `selected|score_below_threshold|filter_rejected`. `StrategyDecision` gains `threshold_used`, `rejection_type`, `best_score`, `weak_components`. CSV has one column per component + `score_breakdown_json`. JSONL carries the same per-candidate + per-decision. Below-threshold NO_TRADE explanation names side/strikes/credit/score/threshold/gap + two weakest. Streamlit per-candidate expanders. **No scoring weights or thresholds changed** — pure observability. | ✅ |
| **2.8 — Anchor-volume correctness** | `ExposureContext` extended with `put_ceiling_{2k,5k}_volume`, `call_floor_{2k,5k}_volume`, `maxvol_volume`. ZS provider's `/exposure/series` mapper now captures the actual volume at each derived strike. VW candidate construction reads `anchor_volume` from STRUCTURE first (tagged `zs_exposure_series`) and falls back to chain volume (`quote_provider_fallback`) only when structure didn't carry it. Scoring uses a neutral 0.5 fallback (`missing_anchor_volume_neutral`) when the anchor level exists but volume is missing — no longer silently 0. Candidate meta gains `anchor_source` (`put_ceiling_2k` / `_5k` / `call_floor_2k` / `_5k`), `anchor_volume`, `anchor_volume_source`, `structure_strength_source`. CSV / JSONL / Streamlit surface all four. **Data correctness, not strategy optimization** — no weights touched. | ✅ |
| **3 — Tastytrade capability probe** | Read-only probe scaffold at `src/providers/quotes/tasty_probe.py` + `scripts/probe_tastytrade.py`. Documents the Tasty API contract (base URLs, legacy `/sessions` BARE-token auth, OAuth2 path forward, nested option chain, `/market-data/by-type` bulk REST quotes, `/api-quote-tokens` for DXLink, dry-run endpoints, cert/sandbox behavior) in `docs/reference_notes.md §8b`. CLI subcommands: `--auth-only`, `--accounts`, `--chain`, `--quotes`, `--capabilities`. **Never** POSTs to `/orders`, `/complex-orders`, or any submit path. Never opens the DXLink WebSocket — only confirms `/api-quote-tokens` returns a token. Output redacts account numbers to last-4 and never prints session-token / remember-token / passwords. **Final `TastytradeQuoteProvider` is deferred** until Dan runs the probe against a real account and reviews the capability matrix. | ✅ |
| **3 ext — OAuth + safety gate** | Probe extended with full OAuth2 refresh flow (`POST /oauth/token` with `grant_type=refresh_token`, `Bearer` header on subsequent requests; precedence: OAuth wins when configured, falls back to `/sessions`). Scope parser handles `read trade openid` AND `read,trade,openid` AND mixed. `TastyProbeConfig` gains `client_id` / `client_secret` / `redirect_uri` / `refresh_token` / `scopes` / `allow_trade_scope` / `enable_order_submission`. New `SafetyGateError` raised by `submit_*` stubs (was generic `NotImplementedError`). New `--config` CLI subcommand prints sanitized config dump (auth_mode, scopes, trade_scope_present, safety-gate state, missing fields) — NO HTTP call, runs without credentials. `capabilities_summary` surfaces `trade_scope_present`, `order_submission_enabled`, `execution_blocked_by_safety_gate`, `has_dxlink`, `has_certification_or_sandbox`, `probe_exposes_submit_path: false`. **Trade scope alone NEVER enables execution** — that's the safety invariant locked by tests. ZS API chain quotes remain explicitly out of scope; Tasty is the intended quote provider. | ✅ |
| **3.1 — root auto-resolution + capability quote probe** | Probe gains `resolve_root_for(underlying, expiry)` that walks the chain and picks SPX vs SPXW correctly — SPXW preferred for any expiry present under both (daily/PM-settled is what VW 0DTE targets). New `get_option_quotes_for_strikes(...)` high-level method takes optional `root_symbol` override; auto-resolves when omitted. Output gains `requested_underlying_symbol`, `resolved_root_symbol`, `root_resolution_source` (`explicit \| auto_chain \| direct_match \| unresolved`). Unresolved expiry returns sanitized error with `available_roots` + `sample_expirations_by_root` — never a silent guess, never a traceback. CLI gains `--root-symbol SPX\|SPXW\|RUT\|NDX\|XSP` + `--capability-{expiry,strikes,right}`. `capabilities_summary` runs a REAL quote probe when those args are supplied — `has_quotes` becomes True/False with `quote_probe_count`, `quote_probe_resolved_root_symbol`, `quote_probe_http_status`. **Cosmetic fix**: `--config` no longer lists `TASTY_USERNAME` / `TASTY_PASSWORD` as missing when OAuth is fully configured; `missing_fields` shape now carries `oauth_missing_fields`, `legacy_missing_fields`, `usable_auth_modes`, `fully_configured`. **OCC builder** extracted from CLI into the probe module so both share one implementation. Safety boundary unchanged — `submit_*` still raise `SafetyGateError`, `probe_exposes_submit_path` still False. | ✅ |
| **3 — Vertical Wing v1 end-to-end** | Full candidate generation, scoring, hard filters, decision engine, paper P&L. | 🚧 next (with optional Phase 2.1 broker capability probe in parallel) |
| **4 — Tastytrade quote provider** | `TastytradeQuoteProvider` (REST chain quotes via probe composition + per-quote `QuoteValidation`); `QUOTE_PROVIDER=mock\|null\|tastytrade` factory with CLI override and graceful Streamlit fallback; broker validation results land on every `OptionQuote` + flow into ranked CSV (`short/long/quote_validation_passed`, `*_rejection_reason`, `quote_chain_root`, `quote_root_resolution_source`, `quote_age_seconds`). | ✅ |
| **4.1 — audit metadata + target-DTE plumbing** | Observability + plumbing pass; NO scoring weight changes, NO execution. New `Candidate` fields (`score_edge`, `score_edge_passed`, `marginal_score`); new `Candidate.meta` keys (`spread_bid/ask/mid`, `spread_width_pct_of_mid`, `worst_leg_bid_ask_abs/pct_of_mid`, `risk_rejections{}`, `risk_rejection_type`, `planned_stop_risk_*`, `theoretical_loss_*`). New `src/selector/readiness.py` with `compute_readiness(...)` — pure function returning `selector_eligible_base`, `selector_blockers`, `quote_quality_bucket` ∈ `good/acceptable/poor/wide/invalid/unknown`, `risk_rejection_*`, `candidate_passes_*` flags. New `src/utils/expiry.py` (pure) with `pick_target_expiry(now_et, target_dte, mode, allow_after_hours_roll, available_expiries)` + hardcoded 2025-2027 NYSE holiday list. Scanner CLI gains `--target-dte / --dte-mode / --allow-after-hours-roll / --print-candidates`; env: `TARGET_DTE / DTE_MODE / ALLOW_AFTER_HOURS_EXPIRY_ROLL / MIN_SCORE_EDGE / STRICT_ROOT_HINT`; YAML: `scanner.expiry`. Default `target_dte=0` keeps behavior byte-identical. `tasty_probe.validate_root_hint(...)` validates explicit OPRA root hints against the chain (lax fallback by default, hard-fail under `STRICT_ROOT_HINT=true`). ~22 new APPENDED CSV columns; existing column indices preserved. **Flag for Phase 4.2**: switch `_bid_ask_quality_score` from abs-dollar cap to relative cap. **Flag for Phase 5**: widen `RejectionType` literal to include `marginal_edge`. **Flag for future review**: annual holiday-list refresh. | ✅ |
| **4.2 — quote-scoring recalibration + strict DTE + clock skew** | THREE surgical changes; NO other scoring/weights/threshold/risk-cap touched, NO execution. (1) **Relative-aware `bid_ask_quality`**: the blunt absolute $0.20 cap is replaced by a pct-of-mid scorer in a NEW pure module `src/utils/quote_quality.py` (stdlib-only, neutral path so BOTH `vertical_wing/candidates.py` AND `src/selector/readiness.py` import it without tripping `test_no_vw_leak`). The SAME cutoffs (good ≤3% → 1.0, ≤7% → 0.8–0.6, ≤15% → 0.5–0.2, >15% → 0.0; None/neg → 0.0; crossed/missing leg → 0.0/`invalid`) drive BOTH the score AND `quote_quality_bucket`, so they can no longer contradict (the live 4.1 bug: a Tasty quote PASSED validation yet scored `bid_ask_quality=0.00` with bucket=`poor`). `quote_quality_bucket` MIGRATED from absolute-$ bins to pct-of-mid bins (deliberate semantic change). `candidates.py` STAMPS `bid_ask_quality` + `bid_ask_quality_mode` + `bid_ask_quality_reason` + `quote_quality_bucket` + `quote_quality_reason` into `Candidate.meta`; `readiness.py` PREFERS the stamped bucket and falls back to the shared helper for fixtures. Legacy `absolute` mode is retained as an opt-in knob (`BID_ASK_QUALITY_MODE`, `BID_ASK_MAX_ABS_CAP`; set cap 0.20 for 4.1 parity) and is auto-used when a leg has no usable mid. (2) **Strict target-DTE**: `--strict-target-dte` / `STRICT_TARGET_DTE` / `scanner.expiry.strict_target_dte` (default false). When the requested `target_dte` can only be served by an expiry FALLBACK, strict mode forces `NO_TRADE` (blocker + esr `strict_target_dte_unavailable`) instead of silently trading the fallback. Enforced ONLY in `run_scanner.py` + `readiness.py`; `pick_target_expiry` is byte-identical (its 18 tests stay green). (3) **Clock-skew clamp**: a NEGATIVE oldest-leg `quote_age_seconds` (quote timestamp ahead of the scanner clock) is clamped to 0.0 with `quote_clock_skew_detected` / `quote_clock_skew_seconds` metadata; `QUOTE_AGE_CLOCK_SKEW_TOLERANCE_SECONDS` (default 2.0) labels magnitude only. The broker validator's positive-age staleness rejection is untouched. Six CSV columns APPENDED at the tail (`bid_ask_quality_mode`, `bid_ask_quality_reason`, `quote_clock_skew_detected`, `quote_clock_skew_seconds`, `strict_target_dte`, `strict_target_dte_passed`); JSONL auto-rides via meta; audit print + Streamlit surface them. **Documented mock tweak**: the two default-selected mock spreads' four legs (5780/5785/5815/5820) were tightened `bid_ask_width` 0.10→0.02 — a flat $0.10 on a sub-$1 OTM long leg (e.g. 5820 c_mid=0.50 → 20% of mid) is correctly `wide`/0.0 under the new relative scorer, which would otherwise break the mock smoke invariant. All mids/volumes/OI and every other strike's width are UNCHANGED. **All other scoring is untouched**: `bid_ask_quality` weight stays 0.05, `no_trade_score_threshold` 0.60, `hard_filters.max_bid_ask_width` 0.20, every risk cap. | ✅ |
| **5 — Daily trade selector framework** | SELECTION ONLY — NO execution, NO order submission/preview, NO change to candidate generation / quote fetching / risk filters / scoring. New pure module `src/selector/daily_selector.py` (operates on candidate ROW dicts + a `SelectorConfig`; imports no strategy package → `test_no_vw_leak` stays green). Nine modes: `score_best_valid` (default), `best_credit_valid`, `closest_wing_valid`, `farthest_wing_valid`, `call_credit_only`, `put_credit_only`, `lowest_breach_risk_valid` (transparent distance/risk/credit composite via `LOWEST_BREACH_RISK_*_WEIGHT`; partial when `planned_stop_risk_pct` missing), `regime_aligned_valid` (positive/neutral → best eligible; negative → blocked; missing `gamma_regime` → `insufficient_regime_data`), `no_trade`. Marks ≤ `MAX_TRADES_PER_DAY` (default 1) rows `selected_trade=true`; never selects rejected / `selector_eligible_base=false` / filter-failing candidates; honors `ALLOW_CALL/PUT_CREDIT` (both false → `no_sides_allowed`), `REQUIRE_QUOTE_VALIDATION`, `REQUIRE_SCORE_EDGE`, and `MIN/MAX_SELECTOR_{SCORE,CREDIT,DISTANCE_FROM_SPOT}` (blockers: `side_disabled_by_config`, `selector_score_below_min`, `selector_credit_below_min`, `selector_distance_below_min`, `selector_distance_above_max`). Preserves the strategy's own decision as `pre_selector_decision`; adds `post_selector_decision` + `selected_trade`. 13 CSV columns APPENDED at the tail; decision log gains `selector_result` (+ `candidates_with_selector_metadata`, after-selector pick); `--print-candidates` adds a `--- daily selector ---` block + `=== DAILY SELECTOR ===` summary; Streamlit gains a selector dropdown + `selected` column. Config via `scanner.selector` YAML / `DAILY_TRADE_SELECTOR` etc. env / CLI (`--daily-selector`, `--max-trades-per-day`, `--allow/--no-allow-{call,put}-credit`, `--require-score-edge`, `--min-selector-{score,credit}`). Default `score_best_valid` matches prior behavior. | ✅ |
| **6 — Strategy run-profiles (config persistence)** | CONFIG / PERSISTENCE ONLY — NO execution, NO orders, NO forward loop, NO change to candidate generation / quote behavior / risk caps / Phase 4.2 scoring / Phase 5 selector logic (only loads its settings). New versioned, validated profile schema `src/config/strategy_profiles.py` (`StrategyProfile` + `validate_profile_dict` returning clean error strings + deterministic `profile_hash` excluding `created_at`/`updated_at`/`profile_path`). File-backed storage under `profiles/` with 4 committed example profiles (`enabled: false`, `stub` + `mock`, no secrets). New `scripts/manage_profiles.py` (`--list/--show/--validate/--validate-all/--copy/--create-template`, `--force` to overwrite). Scanner `--profile <id|path>` applies profile values as defaults with precedence **CLI > profile > env > YAML/default**; `--profile` REPURPOSED to the strategy run-profile (former risk flag → `--risk-profile`; back-compat: a `--profile` value matching a known risk-profile name still works). Profile provenance (`profile_id/name/version/path/loaded/hash`, `config_source_summary`) stamped into CSV + decision-log + logs. Streamlit: read-only run-profile selector that prefills the daily-selector default. Secrets/`execution_mode` keys are rejected by validation. | ✅ |
| **7 — Forward runner / local paper monitoring** | MONITORING + LOCAL LEDGER ONLY — NO execution, NO broker/paper orders, NO order preview, NO position reconciliation, NO backtest adapter. New `scripts/run_forward.py` repeatedly runs the EXISTING scanner via `run_scanner.main(argv)` IN-PROCESS (one-line `main(argv=None)` refactor; existing CLI byte-identical) from a saved Phase 6 run-profile and records a per-run ledger under `outputs/forward/runs/{run_id}/` (`run_manifest.json`, `tick_log.jsonl`, `signal_log.jsonl`, `selected_trades.csv`, `no_trade_log.jsonl`, `heartbeat.json`, + the scanner's own `scanner/` outputs) mirrored to `outputs/forward/latest/`. Manifest carries `no_execution=true`, `execution_mode=disabled_local_monitoring`, git_commit/python/platform. CLI: `--profile`, `--interval-seconds` (default 60; 0=no sleep), `--max-ticks`, `--once`, `--dry-run` (validate + plan + dry_run manifest, no scan), `--market-hours-only` (RTH 09:30–16:00 ET weekdays, simple rule), `--output-dir`, safe `--quote-provider`/`--structure-provider` passthrough. Ledger-level duplicate-signal protection (identity = profile_hash+symbol+expiry+side+strikes+target_dte+trade_date → emitted once, tick flagged `duplicate_selected_signal`). Ctrl+C → manifest `stopped` (exit 0); scanner failure → `error` (exit nonzero); unknown profile → clean exit 2. Streamlit gains a READ-ONLY "Forward runs" section (manifest + heartbeat + counts). `.gitignore` ignores `outputs/forward/*`. | ✅ |
| **8 — Forward run review + control UX** | REVIEW/CONTROL UX ONLY — NO execution, NO broker/paper orders, NO order preview, NO process management. New pure module `src/forward/review.py` (discover runs newest-first; load latest pointer/manifest/heartbeat; load tick/signal/no-trade/selected-trades; `summarize_run` with all spec counts; tolerant of missing/empty files — no tracebacks). New `scripts/review_forward.py` CLI (`--list/--latest/--run/--signals/--no-trades/--ticks/--export-summary`, `--limit`, `--forward-root`; `RUN_ID` accepts the `latest` alias; missing run → clean exit 1). Phase 7 runner gains `outputs/forward/latest/latest_run_pointer.json` (non-breaking) so `latest` resolves robustly. Streamlit "Forward runs" section enhanced: run-selector dropdown, latest-heartbeat caption, 5 count metrics (tick/signal/duplicate/no-trade/error), tables of signals / no-trades / latest ticks, run-folder path, and a COPY-ONLY command block (the UI never launches/stops a process). | ✅ |
| **9A — local forward-runner process control** | LOCAL PROCESS CONTROL ONLY — NO execution, NO broker/paper orders, NO order preview, NO broker account selection, NO position reconciliation, NO auto-execution, NO snapshot workers, NO backtest storage. New pure module `src/forward/control.py` manages a process-state dir under `outputs/forward/control/` (`forward_runner.pid`, `control_state.json`, `stop_requested.json`, `logs/`): non-destructive cross-platform PID-liveness probe (Windows `OpenProcess`+`GetExitCodeProcess`, POSIX `os.kill(pid,0)` — no `psutil`), stale detection, status reconciliation (dead PID + stored "running" → `stale`; no state → `stopped`), graceful stop sentinel, and force-stop that targets ONLY the stored PID. Every control-state file carries `no_execution=true` + `execution_mode=disabled_local_monitoring`. New `scripts/control_forward.py` CLI (`status` / `command` [print-only, never launches] / `start --profile … [--interval-seconds/--once/--max-ticks/--market-hours-only/--quote-provider/--structure-provider]` / `stop [--force]` / `cleanup-stale`); `start` launches a DETACHED background `run_forward` using the same `sys.executable`/venv, refuses if a live runner is active, and writes pid/state + captured `.out.log`/`.err.log`. `run_forward.py` gains additive `--control-state-path` / `--stop-file` (writes live progress into the shared control state; polls the stop sentinel each tick and exits with manifest `status=stopped`); standalone behavior byte-identical when both flags are absent. Streamlit "Forward runs" section gains a READ-ONLY control block (Runner/Active/PID metrics + `stale` warning + copy-only command block) — **no start/stop buttons, no subprocess launch from the UI**. No secrets ever read or printed. | ✅ |
| **9B — multi-strategy local paper trade lifecycle + P&L** | LOCAL PAPER ACCOUNTING ONLY — NO broker orders, NO paper-broker orders, NO order preview, NO live execution, NO historical backtest adapter yet. New `src/paper/models.py` (`PaperTrade` record + `PaperLifecycleConfig`, env/CLI-sourced, `execution_mode=local_paper_lifecycle_only`), `src/paper/lifecycle.py` (open-from-signal, re-price a spread from a later tick's `ranked_candidates.csv` by `(side,short,long,expiry)`, MAE/MFE marks, TP `debit≤credit×0.50` / SL `debit≥credit×1.50` / EOD `≥15:55 ET` exits, dup/limit gating — REUSES `manual_tracker` P&L math), and `src/paper/ledger.py` (portfolio paths/writers + tolerant readers + P&L summary + LOCAL-ONLY reconciliation; `broker_position_reconciliation: deferred`). New `scripts/run_portfolio_forward.py` runs the EXISTING scanner once per profile per tick (in-process, `OUTPUT_DIR` per profile), feeds `selected_trade=true` rows to the engine, and writes `outputs/portfolio_forward/runs/{id}/` (`portfolio_manifest.json`, `portfolio_tick_log.jsonl`, `profile_tick_log.jsonl`, `paper_trades_open.csv`, `paper_trades_closed.csv`, `paper_trade_events.jsonl`, `portfolio_summary.json`, `heartbeat.json`, `reconciliation_report.json`, `scanner/{profile_id}/`) + `latest/` mirror. New `scripts/review_portfolio_forward.py` (`--latest/--list/--run/--open/--closed/--events/--reconcile`). Dedup identity = `profile_hash|symbol|expiry|side|short|long|target_dte|trade_date`; portfolio limits (`PAPER_MAX_OPEN_TRADES_{TOTAL,PER_PROFILE}`, `PAPER_ALLOW_{MULTIPLE_OPEN_PER_PROFILE,DUPLICATE_STRIKES}`) emit `duplicate_skipped` / `blocked_by_limits` events. `PAPER_*` config via env / CLI / `config/portfolio_profiles.yaml` (Phase 6 profile schema UNCHANGED). Streamlit gains a READ-ONLY portfolio panel (no buttons). | ✅ |
| **9C — ZerσSigma Algo Cockpit UI refresh + Strategy Builder + safe controls** | UI / PROFILE-MANAGEMENT ONLY — NO trading-logic change, NO broker execution, NO orders, NO order preview. Streamlit re-skinned into a dark, branded, TABBED command-center (sidebar selectors → a top **⚙ Controls** expander; six tabs: Live Cockpit / Strategy Builder / Forward Runner / Portfolio Paper / Logs-Review / Settings). New **pure** `src/app/ui_helpers.py` (`brand_css()` + card/pill/format helpers; palette adapted from the Dashboard theme — `#0b0f14` bg, `#00E5A8` green, `#2d6cff` blue; no new deps). New **pure** `src/app/profile_builder.py` (Phase 6 profile CRUD: template/clone/edit → `build_profile_dict` → `validate` → `save_profile` with an **overwrite guard** + deterministic hash; secrets/execution keys rejected by existing validation). New `src/app/control_ui.py` (testable guards over the Phase 9A `control` module: `can_start` refuse-second-runner, `start_runner` / `stop_runner` graceful-first / `cleanup`). Forward Runner tab gains real **Start / Stop / Cleanup / Refresh** buttons (LOCAL MONITORING ONLY — force-stop behind an explicit checkbox). Each section re-homed into a `render_*()` function — candidate/scoring/decision render logic preserved verbatim. `streamlit_main.py` imports cleanly headless; `tests/test_phase9c_cockpit.py` (20) covers helpers + mocked control + no-execution grep. | ✅ |
| **10 — historical / snapped-data backtesting adapter** | **NEXT:** replay archived/snapped ZS + chain data through the EXISTING scanner path (read-only consumer of snapped data; reuses the Phase 7 in-process `run_scanner.main(argv)` seam + the Phase 7/8 forward ledger/review + the Phase 9B paper lifecycle for simulated fills). Still **NOT** live broker execution, NO order placement/preview, NO snapshot *writers* (consumes existing snapshots only). | 🚧 next |
| **11 — Tastytrade execution readiness / live execution** *(deferred)* | The execution ladder — `manual_confirm` (cockpit shows the order ticket; human clicks Send in the broker UI) → `broker_paper` → `live_tiny` → `live`, gated by explicit mode escalation behind the Phase 3 safety gate. **Deferred** until the monitoring + paper-lifecycle + backtest tracks (9A/9B/10) are exercised. | ⏳ deferred |

Each phase is **shippable on its own**. We never depend on a later phase to
use an earlier one.

---

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Streamlit Cockpit (src/app)                      │
│  strategy selector · risk profile · provider status · candidates    │
│  decision · manual trades · paper P&L · equity curve · EOD          │
└──────┬──────────────────────────────────────────────┬───────────────┘
       │                                              │
┌──────▼─────────────┐   ┌──────────────────┐   ┌─────▼─────────────┐
│ Strategy Registry  │   │ Risk Engine      │   │ Paper / Manual    │
│ src/strategies/    │   │ src/risk/        │   │ src/paper/        │
│ - base.Strategy    │   │ - hard filters   │   │ - account state   │
│ - registry.load()  │   │ - position size  │   │ - positions       │
│ - vertical_wing/   │   │ - stop variants  │   │ - manual tracker  │
└────────┬───────────┘   └────────┬─────────┘   └─────────┬─────────┘
         │                        │                       │
┌────────▼────────────────────────▼───────────────────────▼─────────┐
│                       Provider Layer (src/providers)              │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐  │
│  │ StructureProv.  │   │ QuoteProvider   │   │ ExecutionProv.  │  │
│  │ (ZS API)        │   │ (broker — TBD)  │   │ (paper / future)│  │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘  │
└───────────┼─────────────────────┼─────────────────────┼───────────┘
            │                     │                     │
       ┌────▼────┐           ┌────▼────┐           ┌────▼────┐
       │ ZS API  │           │ Broker  │           │ Local   │
       │(public) │           │  API    │           │paper sim│
       └─────────┘           └─────────┘           └─────────┘
              ─── Phase 1: stubbed ───  ─── Phase 1: disabled ───

                                Reporting (src/reporting)
                                - decision_log.jsonl (append)
                                - eod_summary.{md,json}
                                Storage (src/storage)
                                - paths, CSV/JSONL writers
```

### Key principles

1. **Providers are pluggable.** Every external system is behind an interface.
2. **Strategies are pure(ish).** Given a `StructureSnapshot`, a strategy
   returns ranked `Candidate`s. No I/O inside `generate_candidates`.
3. **Risk is centralized.** Strategies propose; `src/risk` disposes.
4. **Decisions are immutable records.** Every decision (even `NO_TRADE`) is
   appended to `decision_log.jsonl` with the inputs that produced it. Audit
   the whole day from the log.
5. **Outputs are flat files.** CSV/JSONL/Markdown only in Phase 1. No DB yet.

---

> **Strategy-folder boundary (load-bearing rule).** The cockpit is generic.
> Strategy-specific code lives **only** under
> `src/strategies/<strategy_name>/`. No file in `src/app/`, `src/providers/`,
> `src/risk/`, `src/reporting/`, `src/storage/`, `src/paper/`, or `src/utils/`
> may import a specific strategy. The Streamlit cockpit picks strategies via
> `load_strategies(cfg)` — never via a direct `from src.strategies.vertical_wing import ...`.
> Adding a new strategy is a self-contained operation; see README §"Adding a new strategy".

---

## 4. Strategy registry

Every strategy declares itself in `config/strategies.yaml` with this shape:

```yaml
strategies:
  vertical_wing_v1:
    display_name: "Vertical Wing v1 (SPX 0DTE)"
    enabled: true
    module: "src.strategies.vertical_wing.strategy"
    class: "VerticalWingV1"
    default_parameters: { ... }
    editable_parameters: [ ... ]
    required_data_fields: [ ... ]
```

At startup, `src/strategies/registry.py` walks this list, imports each
`module:class`, and returns a `dict[str, Strategy]`. The Streamlit selector
shows only `enabled: true` strategies.

A `Strategy` must implement (see `src/strategies/base.py`):

| Method | Purpose |
|---|---|
| `id` (property) | Stable string id (matches yaml key). |
| `display_name` (property) | Human label. |
| `required_data_fields()` | List of structure fields the strategy needs (e.g. `["chain.put_volume", "exposures.maxvol"]`). Used to validate the snapshot. |
| `generate_candidates(snapshot, params)` | Pure function: snapshot in, list of `Candidate` out. No I/O. |
| `score(candidate, snapshot, params)` | Returns a float score and a structured breakdown. |
| `select(candidates, params)` | Picks the best candidate or returns `NO_TRADE` with reason. |
| `explain(decision)` | Human-readable rationale string for the decision log. |

The framework calls these in order: generate → filter (risk) → score → select → log.

---

## 5. Vertical Wing v1

> First registered strategy. Targets SPX 0DTE single-day verticals based on
> intraday options structure (PUT_CEILING / CALL_FLOOR by volume).

### 5.1 Candidate construction

**PUT_CEILING_CALL_CREDIT** (bearish-of-level / sell rally):
1. Find the highest strike `K` where `put_volume(K) >= volume_threshold`.
2. Candidate: `SELL Call@K / BUY Call@(K + width)`.
3. Example — if 7500 is the 2K put-volume ceiling: `SELL 7500C / BUY 7505C`.

**CALL_FLOOR_PUT_CREDIT** (bullish-of-level / sell dip):
1. Find the lowest strike `K` where `call_volume(K) >= volume_threshold`.
2. Candidate: `SELL Put@K / BUY Put@(K - width)`.
3. Example — if 7500 is the 2K call-volume floor: `SELL 7500P / BUY 7495P`.

Both constructions run on every scan. The decision engine picks one of:

- `TRADE_CALL_CREDIT` — vertical call credit chosen
- `TRADE_PUT_CREDIT`  — vertical put credit chosen
- `NO_TRADE`          — nothing scored above threshold or filters blocked

Calls-only is **not** hardcoded.

### 5.2 Known promising cohorts (research priors)

These bias the scanner's preferred entry windows + thresholds:

| Time (ET) | Volume threshold | Side         | Stop variant       | Notes                |
|-----------|------------------|--------------|--------------------|----------------------|
| 11:00     | 5K               | CALL_CREDIT  | SL_150 / SL_200    | strongest cohort     |
| 11:00     | 2K               | CALL_CREDIT  | SL_150             | strong               |
| 15:15     | 5K               | CALL_CREDIT  | SL_200             | strong               |
| 15:15     | 2K               | CALL_CREDIT  | SL_200             | solid                |
| 15:00     | 2K               | PUT_CREDIT   | SL_200             | positive but weaker  |

### 5.3 Stop logic

For a credit `c`:

| Variant                | Stop trigger        | Realized P&L at stop |
|------------------------|---------------------|----------------------|
| `BASELINE_CASH_SETTLE` | none (hold to cash) | settle-dependent     |
| `SL_100_PERCENT_LOSS`  | debit ≥ `2.0 × c`   | `-c`  (100% of c)    |
| `SL_150_PERCENT_LOSS`  | debit ≥ `2.5 × c`   | `-1.5 × c`           |
| `SL_200_PERCENT_LOSS`  | debit ≥ `3.0 × c`   | `-2.0 × c`           |

Example with $1.00 credit:
- 100% stop → exit at $2.00 debit → P&L = −$1.00
- 150% stop → exit at $2.50 debit → P&L = −$1.50
- 200% stop → exit at $3.00 debit → P&L = −$2.00

### 5.4 Scoring inputs

The scorer combines (weights live in `config/strategies.yaml`):

- entry credit (absolute)
- credit / max-risk ratio
- distance from spot to short strike (points + % of expected-move-remaining)
- strategy-specific structure strength (volume at the ceiling/floor strike, depth of confluence)
- MaxVol relationship (is the short strike above MaxVol for call credits? below for puts?)
- gamma / DA-GEX regime (positive vs negative gamma context)
- DDOI confluence if available
- intraday trend / VWAP / opening range
- bid/ask spread quality on both legs
- time-to-close (decay headroom)
- velocity / breach risk (recent move toward the strike)

### 5.5 Hard filters (pre-score gate)

Skip and log rejection reason for any candidate with:
- non-positive credit
- missing bid / ask / mid on either leg
- bid/ask width above `max_bid_ask_width`
- credit below `minimum_credit_morning` / `minimum_credit_afternoon`
- short strike closer to spot than `min_distance_from_spot`
- missing strategy-required structure confirmation
- score < `no_trade_score_threshold`
- known event / headline day (toggle)

All rejected candidates land in `outputs/runs/ranked_candidates.csv` with the
rejection reason — so we can audit what we missed and why.

---

## 6. Providers

### 6.0 Provider separation (Phase 1.5)

The cockpit treats structure and quote data as **independent contracts**.
No provider knows about the other; strategies are the only layer that
combines them.

```
StructureSnapshot                      OptionChainSnapshot
  symbol, spot, quote_ts                 underlying, spot, expiry
  exposures: ExposureContext             quotes: list[OptionQuote]
    - total_gex_bn / total_vex_bn        quote_ts, provider_name
    - gamma_flip, call_wall, put_wall
    - maxvol, gamma_regime               OptionQuote (per strike, per side)
    - da_gex_signed                        - underlying, expiry, option_type
    - put_ceiling_2k / 5k                  - strike
    - call_floor_2k / 5k                   - bid, ask, mid
    - ddoi_pin                             - volume, open_interest
                                           - optional Greeks (iv, delta, ...)
                                           - quote_time, vendor_symbol
```

Strategy contract:

```python
class Strategy(Protocol):
    def generate_candidates(
        self,
        structure: StructureSnapshot,
        chain:     OptionChainSnapshot,
        params:    dict[str, Any],
    ) -> list[Candidate]: ...
    def score(self, c, structure, chain, params) -> float: ...
    def select(self, candidates, params) -> StrategyDecision: ...
```

**Why this separation matters:**

- Production: structure (ZS API) and quotes (broker API) are two different
  external services with different cadences, auth, and rate limits. Bundling
  them up-front would force lock-step polling.
- Testability: the mock dataset in `src/providers/_mock_data.py` feeds both
  providers but each can be stubbed independently in tests.
- Future broker swap: changing brokers means dropping in a new
  `QuoteProvider` implementation — zero impact on the structure pipeline.
- Strategy independence: strategies state explicitly what they need
  (`required_data_fields`) and the framework can refuse to scan if a
  provider isn't supplying it.

### 6.1 StructureProvider (read-only, ZS API)

Interface: `src/providers/structure/base.py::StructureProvider`

Phase 1 implementation: stub (`src/providers/structure/zerosigma_api.py`).
Phase 2 implementation: real HTTP client against the ZerσSigma public API.

**Planned consumed endpoints** (see `docs/reference_notes.md` for full notes):

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/market/spot?symbol=SPX` | spot + quote timestamp |
| `GET /api/v1/market/chain?symbol=SPX` | chain metadata + wide CSV |
| `GET /api/v1/market/exposures?symbol=SPX` | total GEX/VEX, gamma flip, call/put walls |
| `GET /api/v1/market/snapshot?symbol=SPX` | combined spot + chain + exposures |
| `GET /api/v1/market/prev-wings?symbol=SPX` | prior-day wings snapshot |
| `GET /api/v1/exposure/series?symbol=SPX&metric={raw_gex,da_gex,dex,vex,cex,volume}&mode={net,split}` | per-strike exposure arrays |
| `GET /api/v1/exposure/ddoi?symbol=SPX` | DDOI history (JSONL from Spaces) |
| `GET /api/v1/market/es-factor` | ES factor for SPX→ES conversion |

**Auth**: Bearer JWT in `Authorization` header. Token issued via
`POST /api/v1/auth/login` or `POST /api/v1/auth/service-token`
(if `ADMIN_SERVICE_KEY` configured).

**Refresh cadence**: ~60s for snapshots (matches Dashboard worker cadence).
Spot can refresh faster (2–10s) when broker quotes are wired (Phase 5).

**Local fallback rule**: if a Phase 8+ task ever adds local Greek/exposure
calc, it **must** mirror ZerσSigma exposure conventions exactly
(see "Read-only ZerσSigma Integration Notes" below).

### 6.2 QuoteProvider (broker, TBD)

Interface: `src/providers/quotes/base.py::QuoteProvider`

Phase 1 implementation: `null_provider.py` — returns `None` for everything,
forcing the cockpit into "manual mark" mode (user enters fill prices by hand).

Future implementations (one of):
- Tastytrade · Webull · Alpaca · Tradier · IBKR · Schwab

The QuoteProvider interface is intentionally narrow:
- `get_spot(symbol)`
- `get_option_quote(symbol, expiry, strike, right)`
- `get_vertical_mark(short_leg, long_leg)` (mid-of-mids OR vertical mark if broker exposes one)
- `quote_timestamp()`

### 6.3 ExecutionProvider (TBD)

Interface: `src/providers/execution/base.py::ExecutionProvider`

Allowed modes (Phase 1):

| Mode | Behavior |
|---|---|
| `disabled` | Reject all order placement. Cockpit shows candidates but no buttons. |
| `local_paper` | Simulate fills at provider mark (or manual mark). Writes to `paper_trades.csv`. |
| `manual_trade_tracking` | User enters fills manually; cockpit just records. |

Future modes (stubbed, raise `NotImplementedError`):

- `broker_paper` — broker sandbox / paper account
- `manual_confirm` — cockpit prints the order ticket; user confirms in broker UI; cockpit then logs it
- `live_tiny` — real money, hard-capped contracts (e.g., 1 contract)
- `live` — full

Mode is set via `EXECUTION_MODE` env var or `config/providers.yaml`. Escalating
to a live mode must require **two** confirmations: a code-side guard and an
explicit user confirmation in the UI.

---

## 7. Risk engine

### 7.1 Two risk concepts (planned vs theoretical)

For every credit-spread candidate the cockpit tracks **two independent**
risk numbers:

| Concept | Per-spread formula | Question it answers |
|---|---|---|
| **Theoretical max loss** | `spread_width − credit` | What's lost if the spread goes fully ITM with no stop fired |
| **Planned stop risk** | `credit × (stop_multiple − 1)`, capped at theoretical | What we *intend* to lose if our stop fires |

Convert to dollars with `× 100 × contracts`. Both have separate caps on
the active risk profile:

- `max_planned_trade_loss_percent` / `..._dollars` — **primary** "can I take this?" gate
- `max_theoretical_trade_loss_percent` / `..._dollars` — hard ceiling on full defined risk

Worked example — 5-wide vertical, $0.80 credit, 5 contracts, `SL_150_PERCENT_LOSS`:

| Metric | Value |
|---|---|
| Theoretical max loss | `(5.00 − 0.80) × 100 × 5 = $2,100` |
| Planned stop risk | `((0.80 × 2.5) − 0.80) × 100 × 5 = $600` |
| Under `aggressive_paper_10k` (planned 10%, theoretical 30%) | both pass |
| Under `conservative_paper_10k` (planned 3%, theoretical 7%) | both fail at 5 lots; both pass at 1 lot |

**`BASELINE_CASH_SETTLE` (no-stop) fallback decision**: planned risk falls
back to theoretical max loss. Rationale (also documented at the top of
`src/risk/limits.py`):

- *Safer*: a no-stop trade is sized as if the full defined risk could realize,
  instead of being waved through with infinite implied risk.
- *Clearer*: one consistent formula (`planned = min(stop_derived, theoretical)`)
  rather than an "undefined" special case.

The UI always displays both numbers regardless of which gate the trade
clears, so a user can see the full picture before approving a manual entry.

### 7.2 Templates, not hardcoded rules

`config/risk_profiles.yaml` ships **session-start templates**, not immutable
production rules. The current templates are:

- **`aggressive_paper_10k`** (active default) — 5 contracts, $5 width,
  `SL_150_PERCENT_LOSS`, planned 10%, theoretical 30%, daily 10%.
- **`conservative_paper_10k`** — 1 contract, $5 width,
  `SL_100_PERCENT_LOSS`, planned 3%, theoretical 7%, daily 5%.

The Streamlit cockpit will (later phase) let the user edit every field
before kicking off a session; every edit appends to
`outputs/runs/{date}/config_change_log.jsonl`. See §11 for the full list
of planned dashboard controls.

### 7.3 Layers

- **Hard filters** (`src/risk/filters.py`) — black/white gates run before
  scoring. Includes `_f_planned_trade_loss_within_cap` (primary gate) and
  `_f_theoretical_trade_loss_within_cap` (hard ceiling). Each is a no-op
  if its respective cap isn't configured.
- **Sizing & circuit-breakers** (`src/risk/limits.py`) — daily P&L stop
  (`daily_loss_breach`), max open positions (`position_cap_breach`),
  per-spread risk arithmetic (`planned_loss_per_spread`,
  `theoretical_max_loss_per_spread`, dollar variants).

---

## 8. Paper / Manual trade tracking

`src/paper/account.py` — paper account state.

```
PaperAccount {
  starting_balance, current_balance,
  realized_pnl, unrealized_pnl,
  open_positions: list[PaperPosition],
  equity_curve: list[(ts, equity)]
}
```

`src/paper/positions.py` — `PaperPosition` (strategy_id, side, short_strike,
long_strike, credit, contracts, entry_time, stop_variant, current_mark,
unrealized_pnl, exit_*).

`src/paper/manual_tracker.py` — Streamlit-facing helpers to enter / update /
close a manual trade. Persists to `outputs/runs/manual_trades.csv` (append).

Defaults:
- Starting balance: **10000**
- Contracts per trade: **5**
- Max open positions: **1**

---

## 9. Reporting

`src/reporting/decision_log.py` — append-only JSONL. Each scan tick writes:

```json
{
  "ts": "2026-05-31T14:55:03-04:00",
  "strategy_id": "vertical_wing_v1",
  "decision": "TRADE_CALL_CREDIT" | "TRADE_PUT_CREDIT" | "NO_TRADE",
  "selected_candidate": { ...full candidate... } | null,
  "all_candidates": [ ... ],
  "score": 0.72,
  "rejection_reasons": [ ... ],
  "snapshot_summary": { "spot": ..., "maxvol": ..., "regime": ... }
}
```

`src/reporting/eod.py` — generates `outputs/daily/{YYYY-MM-DD}/eod_summary.md`
+ `eod_summary.json`. Includes:

- starting balance, ending balance, realized P&L, unrealized P&L
- trades taken (paper + manual)
- trades skipped + reasons
- best candidate of the day (whether or not we took it)
- no-trade decisions count
- max intraday drawdown
- max intratrade heat (MAE)
- stop hits, profit target hits
- largest spread risk (max-loss exposure at any moment)
- notes on MaxVol / structure behavior

---

## 10. Outputs (file contracts)

All under `outputs/`:

| Path | Format | When written |
|---|---|---|
| `latest/snapshot.json` | JSON | every scan tick (overwrites) |
| `runs/{YYYY-MM-DD}/ranked_candidates.csv` | CSV append | every scan tick |
| `runs/{YYYY-MM-DD}/decision_log.jsonl` | JSONL append | every scan tick |
| `runs/{YYYY-MM-DD}/manual_trades.csv` | CSV append | on manual entry |
| `runs/{YYYY-MM-DD}/paper_trades.csv` | CSV append | on simulated fill |
| `runs/{YYYY-MM-DD}/paper_positions.csv` | CSV (snapshot) | every tick (overwrites) |
| `runs/{YYYY-MM-DD}/paper_equity_curve.csv` | CSV append | every tick |
| `runs/{YYYY-MM-DD}/missed_signals.csv` | CSV append | post-hoc replay (Phase 3+) |
| `runs/{YYYY-MM-DD}/config_change_log.jsonl` | JSONL append | on any config edit |
| `daily/{YYYY-MM-DD}/eod_summary.md` | Markdown | EOD script |
| `daily/{YYYY-MM-DD}/eod_summary.json` | JSON | EOD script |

All paths are resolved through `src/storage/paths.py` so they honor
`DATA_DIR` / `OUTPUT_DIR` env vars (portability).

---

## 11. Streamlit cockpit

`src/app/streamlit_main.py`. Planned panels (Phase 3):

- Active **strategy selector** (from registry, only `enabled: true`)
- Strategy-specific controls (volume threshold, time window, etc.)
- **Session risk controls** (override the loaded template before scanner start):
  starting balance · contracts per trade · max daily loss
  (dollars/percent) · max planned trade loss (dollars/percent) ·
  max theoretical trade loss (dollars/percent) · spread width · stop variant ·
  profit targets · max open positions · no-trade score threshold ·
  scan start/end time · preferred entry windows · minimum credit ·
  max bid/ask width · minimum distance from spot. Every edit appends to
  `outputs/runs/{date}/config_change_log.jsonl`.
- **Provider status** (StructureProvider OK? last refresh? QuoteProvider mode?)
- Current spot
- Latest broker quote timestamp (or "manual mode")
- Latest ZS API context timestamp
- **MaxVol** + strategy-specific key levels (PUT_CEILING / CALL_FLOOR)
- **Ranked candidates** table (with rejection reasons for filtered ones).
  Each candidate card shows credit, max risk per spread, R:R, distance
  from spot, score, **planned stop risk (dollars under the active stop
  variant)**, and **theoretical max loss (dollars)** — both always visible.
- **Selected decision** card: `TRADE_CALL_CREDIT` / `TRADE_PUT_CREDIT` / `NO_TRADE`
  with credit, max risk, reward:risk, breakeven, score, rejection reasons
- **Manual trade entry** form (strategy, side, strikes, credit, contracts, entry time, stop profile, notes)
- **Open tracked positions** with current mark + unrealized P&L
- **Realized + unrealized P&L** + paper equity curve
- **EOD summary** view (today's)

Refresh cadence:
- UI auto-refresh every **2s**
- Structure poll every **~60s** (matches Dashboard worker)
- Future broker quote poll every **~2s** (provider permitting)
- Future full candidate rebuild every **~10s**

---

## 12. Configuration model

```
.env                              → secrets, base URLs (machine-specific)
config/strategies.yaml            → registered strategies + defaults
config/risk_profiles.yaml         → risk profiles (default + user-named)
config/providers.yaml             → structure / quote / execution provider wiring
config/scanner.yaml               → poll cadences, scan windows, global thresholds
```

Loaded by `src/utils/config.py::load_config()` which:
1. Loads `.env` via `python-dotenv`.
2. Reads YAML files.
3. Substitutes `${ENV_VAR}` references inside YAML.
4. Validates with Pydantic models (`AppConfig`).

---

## 13. Read-only ZerσSigma Integration Notes

The cockpit depends on the ZerσSigma stack (Dashboard, Worker, API) only as a
**read-only consumer**. We do not modify any production code. Full contract
notes live in [`docs/reference_notes.md`](docs/reference_notes.md). High-level
summary:

- **Data origin**: Schwab API → `worker_watchlist.py` (Dashboard) → Redis
  (`zs:latest:{SYMBOL}:*`) → ZS API (FastAPI) → us.
- **Refresh cadence**: spot 2–10s, chain 60s, exposures recomputed every 60s,
  DDOI daily 07:00 ET, ES factor at 17:00 ET, wings at 16:05 ET.
- **Wide chain CSV columns**: `snapshot_ts, snapshot_date, symbol, spot,
  expiry, strike, dte`, then `c_*` / `p_*` for `bid, ask, mid, iv, delta,
  gamma, vega, theta, rho, vanna, charm, speed, vomma, zomma, gex_1pct,
  raw_gex_1pct, da_gex_1pct, dex_1pct, vex_1vol, vex_skew_1vol, cex,
  cex_skew, charm_skew, speed_exp, vomma_exp, zomma_exp, oi, volume`.
- **Exposure units**: all per-strike exposures are in $Bn, OI-weighted
  unless `weight=volume` requested.
- **Auth**: JWT bearer; tokens 15-minute TTL; refresh via `/api/v1/auth/refresh`.
- **Rate limits**: market endpoints 60/min, exposure endpoints 30/min — the
  cockpit's poll cadence is well inside these.

### Future recommendations for ZerσSigma (do not implement here)

These are **suggestions** for the ZS team, recorded so we don't lose them.
They are NOT to be implemented by this repo:

1. Consider a `/api/v1/market/structure-levels?symbol=SPX` endpoint that
   returns `{put_ceiling, call_floor, maxvol, gamma_flip, call_wall, put_wall,
   ddoi_pin}` in one payload. Today the cockpit will derive these from
   `/exposure/series` + `/market/exposures`.
2. Consider exposing a `last_updated_ts` field on `/market/snapshot` so the
   cockpit can short-circuit polling when nothing has changed.
3. Consider a server-sent-events / websocket variant of `/market/snapshot` so
   sub-second cockpits don't have to poll. (Not needed for 60s cadence; only
   if we ever go faster.)

---

## 14. Open questions

1. **Broker choice**: undecided. Phase 4 will run the capability probe.
   Preference ordering today: Tastytrade > Tradier > Alpaca > Webull > IBKR > Schwab fallback.
1a. ~~Default risk profile is over-sized for the default account~~ —
    **resolved.** Split into `aggressive_paper_10k` (5-lot, planned 10%,
    theoretical 30%) and `conservative_paper_10k` (1-lot, planned 3%,
    theoretical 7%). Per-trade risk now uses planned stop risk under the
    selected stop variant, not raw spread width — see §7.
2. **PUT_CEILING / CALL_FLOOR exact definition**: should we use *cumulative*
   volume above/below a threshold, or *single-strike* volume? Current plan:
   single-strike. Revisit after first replay.
3. **Score weights**: need calibration against historical decision_log once
   we have 4 weeks of paper data.
4. **Event day source**: who tells the cockpit "today is FOMC"? Manual flag
   in `risk_profiles.yaml > no_trade_dates` for now; integrate an economic
   calendar in Phase 6+.
5. **Multi-symbol**: Phase 1 plans SPX only. SPY / NDX / RUT will require
   per-symbol risk profiles and per-symbol thresholds. Architecture supports
   it; configs don't yet.
6. **Wings worker dependency**: prev-wings is only useful pre-open. Cockpit
   should warn (not fail) when prev-wings is stale.
7. **Replay mode**: would be valuable for backtesting candidate generation
   against historical chain snapshots in `history/raw/`. Out of scope Phase 1.
8. **ZS API fields not exposed** (Phase 2 gap): `gamma_flip`, `call_wall`,
   `put_wall`, `ddoi_pin` aren't surfaced by any `/api/v1/*` endpoint. The
   Dashboard derives them internally from chain CSV + OI distribution.
   Two ways to close the gap:
   - **(a)** Ask the ZS team for a bundled `/api/v1/market/structure-levels`
     endpoint (lowest algo-side complexity; see §13 recommendations).
   - **(b)** Pull `/api/v1/exposure/series?metric=raw_gex&mode=net` and
     derive walls + flip locally (mirrors production logic in the algo repo).
   Phase 3 picks one based on ZS-team bandwidth. Until then those four
   fields stay `None` and the strategy scorer treats them as neutral.

---

## 15. Broker Capability Probe → Phase 4 production provider (status: DONE)

The Phase 3 probe (`scripts/probe_tastytrade.py`) confirmed Tasty supports
everything VW needs for live REST quotes: OAuth refresh auth, account
list, SPX/SPXW chain with daily/0DTE, per-strike bid/ask/mid via
`/market-data/by-type`. Live capability matrix from Dan's account:
`has_auth=true, has_accounts=true, has_chain=true, has_quotes=true,
chain_has_0dte_today=true, has_dxlink=false,
order_submission_enabled=false, probe_exposes_submit_path=false`.

**Phase 4 outcome**: `TastytradeQuoteProvider`
(`src/providers/quotes/tastytrade_provider.py`) is implemented as a real
`QuoteProvider` and wired through the existing scanner / Streamlit
cockpit. It **composes** the Phase 3 probe (auth + REST + root resolution)
and adds:

  - The full `QuoteProvider` Protocol (`get_spot`, `get_option_quote`,
    `get_option_chain`, `quote_timestamp`, `status`).
  - `QuoteValidation` — broker-side per-quote thresholds (crossed,
    zero-bid, spread-abs, spread-pct, stale-age). Tuned via
    `TASTY_QUOTE_*` env vars.
  - Quote-provider factory (`src/providers/quotes/factory.py`) mirroring
    the structure factory. Precedence: `--quote-provider` CLI →
    `QUOTE_PROVIDER` env → `config/providers.yaml` → `"mock"`.
  - New `ranked_candidates.csv` columns: `quote_provider`,
    `quote_timestamp`, `quote_age_seconds`, `quote_chain_root`,
    `quote_root_resolution_source`, `{short,long}_validation_passed`,
    `{short,long}_rejection_reason`, `quote_validation_passed`,
    `quote_rejection_reason`.
  - Streamlit: sidebar quote-provider selector, `root=` chip in
    Provider status, per-candidate `quote ✓/✗` column, per-leg
    validation metrics in each candidate expander.

**Phase 4 boundary** (intentionally narrow — not in scope this phase):

  - No live execution. No order submission. No order preview /
    dry-run. No order tickets. Tests assert the provider does not
    even define `submit_order` / `preview_order` / `place_order`.
  - No DXLink WebSocket — REST polling only.
  - No snapshot worker. Provider fetches what the scanner asks for.
  - No whole-chain pulls — REST cost is per-symbol; the scanner
    always supplies `request.required_strikes` (since Phase 2.6).
  - ZS API remains structure-only (MaxVol, exposures, ceilings, floors).
  - Mock provider stays the default. `tastytrade` is opt-in.
  - The scanner fails LOUDLY on misconfigured `tastytrade`; the
    Streamlit cockpit falls back to mock visibly so the UI stays
    loadable.

### Historical broker probe checklist (for any future broker)

When evaluating a non-Tasty broker, the probe should attempt — for that
broker — the following in order, recording pass/fail per step:

1. Auth (key + secret accepted)
2. Account list / account balances
3. Paper / sandbox account availability
4. SPX (or SPX-equivalent index) quote
5. SPX 0DTE option chain retrieval
6. Bid / ask / mid availability on individual option contracts
7. Quote streaming (websocket) OR polling cadence support
8. Per-contract volume
9. Vertical spread order preview (no submit)
10. Paper / sandbox order submission (if supported)
11. Order, fill, position readback
12. Close / cancel workflow

The output is a comparison matrix in `docs/broker_probe_{date}.md` that
informs the QuoteProvider choice.

---

## 16. Non-goals (Phase 1)

- No live broker connection.
- No automatic execution of any kind.
- No modifications to Dashboard / ZS API / workers / Redis / Schwab ingest.
- No backtest engine yet (replay deferred).
- No multi-symbol scanning (SPX only).
- No persistence beyond flat files (no DB, no Redis writes from cockpit).
- No web deployment — strictly local Streamlit.

---

## 17. Definition of done — Phase 1

- ✅ Scaffold present, importable, lint-clean.
- ✅ `strategies.yaml` registers `vertical_wing_v1` and the registry loads it.
- ✅ Strategy returns at least one `Candidate` object given the stub snapshot
  (both CALL_CREDIT and PUT_CREDIT candidates produced from the deterministic
  chain).
- ✅ Risk filters reject a non-positive credit; planned + theoretical trade-loss
  gates wired and tested under both `aggressive_paper_10k` and
  `conservative_paper_10k` profiles.
- ✅ Manual trade entry writes rows to `outputs/runs/{date}/manual_trades.csv`
  AND mirrors to `outputs/latest/manual_trades.csv`.
- ✅ Decision log writes records (TRADE_CALL_CREDIT, TRADE_PUT_CREDIT, or
  NO_TRADE) to both `outputs/runs/{date}/decision_log.jsonl` and
  `outputs/latest/decision_log.jsonl`.
- ✅ EOD script runs from `python -m scripts.run_eod_summary`; emits md + json
  to both `outputs/daily/{date}/` and `outputs/latest/`.
- ✅ Streamlit shell launches end-to-end with: strategy + risk-profile
  selectors, editable session controls (with config-change log), structure
  panel (spot/MaxVol/walls/gamma/PUT_CEILING/CALL_FLOOR/DDOI), candidate
  table with planned + theoretical $, decision card, manual trade entry,
  open positions panel, P&L + equity curve, "Generate EOD" button.
- ✅ One-shot scanner runner (`python -m scripts.run_scanner`) writes
  `ranked_candidates.csv` + `decision_log.jsonl` to both `outputs/latest/`
  and `outputs/runs/{date}/` without requiring Streamlit.
- ✅ 34 tests, 0 failures, ruff clean.

### Still mock / stubbed (Phase 3+)

- `ZeroSigmaApiStructureProvider` is **implemented** as of Phase 2. Default
  stays on stub for safety; switch via `ZS_STRUCTURE_PROVIDER=zerosigma_api`
  in `.env` or `--structure-provider zerosigma_api` on the scanner CLI.
  Fields not exposed by the current ZS API land as `None` with their names
  tracked in `snapshot.raw["missing_fields"]`: `gamma_flip`, `call_wall`,
  `put_wall`, `ddoi_pin`. Filed as open question §14.8.
- `QuoteProvider` defaults to `MockQuoteProvider` (deterministic chain
  from `_mock_data.MOCK_CHAIN`) + `NullQuoteProvider`. **Phase 4 added
  `TastytradeQuoteProvider`** — live REST quotes via the Phase 3
  probe's OAuth path; opt-in via `QUOTE_PROVIDER=tastytrade` or
  `--quote-provider tastytrade`; conservative per-quote validation
  enforced by `QuoteValidation`. Phase 5+ may add DXLink streaming or
  additional broker providers.
- Execution provider modes available: `disabled`, `local_paper`,
  `manual_trade_tracking`. Live modes stubbed only.
- `force_stop` on a `BASELINE_CASH_SETTLE` position is intentionally a no-op
  at the paper-account level — the docs make this explicit; you should not
  call it on no-stop positions.
