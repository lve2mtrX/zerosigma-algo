# notes.md — append-only running notes

> Drop-in dated entries. Newest at the bottom. Anything ephemeral, scratch,
> half-thought goes here. Real decisions belong in plan.md.

---

## 2026-05-31 — Phase 0 scaffold landed

- Empty `zerosigma-algo/` folder bootstrapped.
- Inspected (read-only) sibling repos:
  - `..\Dashboard` — Schwab → worker → Redis → API pipeline; chain CSV
    contract documented in `docs/reference_notes.md`.
  - `..\zerosigma-api` — JWT-auth REST surface at `/api/v1/market/*` and
    `/api/v1/exposure/*`; cockpit will consume these only.
  - No production files were modified.
- Architecture decisions captured in `plan.md`.
- `StructureProvider` Phase 1 = stub. `ZeroSigmaApiStructureProvider` is wired
  but raises `NotImplementedError` — flip in Phase 2.
- `QuoteProvider` = `NullQuoteProvider`. Forces manual marks.
- `ExecutionProvider` default = `local_paper` (env-overridable).
- Strategy registry registers `vertical_wing_v1` only. Adding another
  strategy = drop a module under `src/strategies/<name>/` + add a yaml entry.

### Open follow-ups (not blocking)

- [ ] Broker capability probe (Phase 4 brief in `plan.md` §15)
- [ ] PUT_CEILING / CALL_FLOOR — confirm single-strike vs cumulative-volume
  definition once we have replay data.
- [ ] Score weight calibration after 4 weeks of paper data.
- [ ] Event-day source — manual `no_trade_dates` list for now.
- [ ] Multi-symbol scanning — Phase 1 = SPX only.
- [ ] Replay mode against `history/raw/` snapshots — deferred.

---

## 2026-06-01 — Phase 1 wiring complete (local demo runnable)

- Cockpit is now end-to-end runnable on stub/mock providers.
- New modules:
  - `src/app/session_state.py` — `SessionConfig` dataclass with `from_profile`,
    `to_filter_params`, `diff_against`. 16 editable fields.
  - `src/reporting/config_change_log.py` — per-field + session-snapshot JSONL
    writers under `outputs/runs/{date}/config_change_log.jsonl`.
  - `src/providers/quotes/mock_provider.py` — `MockQuoteProvider` (deterministic
    intrinsic-plus-time mids).
- Stub provider now produces a chain with 2K + 5K PUT_CEILING / CALL_FLOOR levels,
  MaxVol, gamma regime, DDOI pin. Tuned so the default profile yields a real
  `TRADE_CALL_CREDIT` decision (score 0.62 > 0.60 threshold).
- `scripts/run_scanner.py` does the real pipeline now: load → generate → filter
  → score → select → log. Writes to both `outputs/latest/` and per-day folders.
- `src/reporting/eod.py` also mirrors to `outputs/latest/eod_summary.{md,json}`.
- `src/reporting/decision_log.py` exposes `build_decision_record` +
  `log_decision_to_file` so writers can target arbitrary paths.
- `src/paper/manual_tracker.py` now provides `build_manual_trade_record`,
  `unrealized_pnl_dollars`, `realized_pnl_dollars`, `spread_width_from_strikes`.
- Streamlit cockpit fully wired: strategy + profile selectors, "Reset to defaults",
  provider status, editable session-control form (config-change-logged on submit),
  structure panel with all VW levels, candidate table with planned + theoretical $,
  decision card, manual-trade entry, open positions w/ live marks, P&L,
  equity-curve chart, EOD button surfacing the latest summary.
- Tests: 34/34 pass (was 18). New suites: `test_stub_provider.py`,
  `test_scanner_runner.py` (subprocess-driven, tmp OUTPUT_DIR),
  `test_session_state.py`, `test_manual_pnl.py`, `test_eod_summary.py`.
- Ruff clean.

### Known limitations / deferred

- Scanner does not register the mock-quote provider yet — it doesn't currently
  need bids/asks beyond what the stub chain carries; we'll wire `QuoteProvider`
  into candidate scoring once a real broker provider lands (Phase 5+).
- Streamlit's `paper_account` lives in `st.session_state` for the running
  session; refreshing the browser tab resets it. Persistence across cockpit
  restarts is Phase 3+ work (load latest `paper_positions.csv` on boot).
- `_f_max_bid_ask_width` is referenced in the session_state filter-params dict
  but isn't yet a registered filter in `DEFAULT_FILTERS`; the wide-bid/ask ATM
  strikes in the stub are correct setup for adding that filter next.

---

## 2026-06-01 (PM) — Phase 1.5: provider split

- Cleanly separated StructureProvider (structure context only) from
  QuoteProvider (chain + pricing).
- New: `src/providers/quotes/types.py` with `OptionType`, `OptionQuote`,
  `OptionChainSnapshot`, `SpreadQuote`, `QuoteProviderStatus`.
- New: `src/providers/_mock_data.py` — single canonical mock dataset
  (`MOCK_CHAIN` + helpers). Both providers read from it; neither imports
  the other.
- `StructureSnapshot` no longer carries chain quotes (`ChainRow` removed).
  Strategies receive `(structure, chain, params)`. The `Strategy` protocol
  was updated accordingly.
- `MockQuoteProvider.get_option_chain(symbol)` returns an
  `OptionChainSnapshot` matching the mock dataset; `NullQuoteProvider`
  matches the same interface for the disconnected case.
- VW candidate generation now: reads `PUT_CEILING`/`CALL_FLOOR` from
  `structure.exposures.put_ceiling_2k/5k` (or `call_floor_*`); looks up
  leg quotes from `chain.find(strike, OptionType)`; computes credit, R:R,
  bid/ask quality, and stores leg + spread metadata on `candidate.meta`.
- Scoring updated to use the new `(structure, chain, params)` signature
  and the new `meta["anchor_volume"]` field (put-volume at ceiling for
  CALL_CREDIT, call-volume at floor for PUT_CREDIT — caught a subtle bug
  where the anchor was incorrectly read from the same-side leg).
- Scanner runner: explicit `StructureProvider` + `QuoteProvider`; decision
  log carries both `*_provider` names + both `*_ts` timestamps + spot from
  the quote provider; ranked CSV adds leg bid/ask/mid + b/a quality.
- Streamlit cockpit: dual-provider status panel; "Spot (quote)" with a
  structure-spot delta; structure-vs-chain timestamps both displayed;
  candidate table now shows `short b/a/m` and `long b/a/m` columns plus
  `b/a quality`.
- Tests: 42/42 pass (was 34). New: `test_mock_quote_provider.py` (6 tests
  covering chain shape, determinism, status, SpreadQuote math, back-compat
  `get_option_quote`); `test_no_vw_leak.py` (regression: no `vertical_wing`
  imports outside its folder). Updated: `test_stub_provider.py` asserts
  StructureSnapshot lacks a `chain` attribute; `test_registry.py` exercises
  the new `(structure, chain)` signature; `test_scanner_runner.py` checks
  both provider names + timestamps in the decision log + leg b/a columns
  in the ranked CSV.
- Ruff clean. Demo still emits `TRADE_CALL_CREDIT` SPX 5815/5820 @ $0.60
  credit (score 0.61, planned $450, theoretical $2,100).

### Next step

Phase 2: wire `ZeroSigmaApiStructureProvider` against `/api/v1/market/*`
and `/api/v1/exposure/*`. The provider boundary is now clean — that work
only needs to populate `StructureSnapshot` / `ExposureContext` from JSON
responses; it does NOT touch quote-side code or the strategy contract.

---

## 2026-06-01 (later) — Phase 2: read-only ZS API StructureProvider

- Inspected `zerosigma-api` + `Dashboard` read-only — no external files
  modified. Documented the contract in `docs/reference_notes.md §8a`.
- Implemented `src/providers/structure/zerosigma_api.py` against the public
  ZS API surface:
  - `GET /api/v1/market/snapshot` → spot + aggregate exposures.
  - `GET /api/v1/exposure/series?metric=volume&mode=split` → per-strike
    call/put volumes → derives `PUT_CEILING_{2K,5K}`, `CALL_FLOOR_{2K,5K}`,
    `maxvol`.
- Three auth modes wired (`bearer`, `login`, `service_token`); none of the
  three is the default — `ZS_API_AUTH_MODE=none` keeps the cockpit on the
  stub provider with no network attempts.
- `gamma_regime` derived from `sign(da_gex_bn)`. `total_vex_bn` <-
  `exposures.vex` (ZS uses unsuffixed `vex`/`dex`/`cex`).
- Fields the current ZS API does NOT expose: `gamma_flip`, `call_wall`,
  `put_wall`, `ddoi_pin` — set to `None`, listed in
  `snapshot.raw["missing_fields"]`. Tracked in plan.md §14.8.
- Subscription gate: when `/exposure/series` returns 403 (user not
  subscribed), the provider degrades gracefully — VW levels drop to None,
  the rest of the snapshot still populates, `status().subscription_active`
  flips to `False`, and the UI surfaces a warning.
- New module: `src/providers/structure/factory.py` — resolves the active
  provider name → instance, with stub fallback on any error. Scanner
  + Streamlit both go through it.
- `scripts/run_scanner.py --structure-provider {stub|zerosigma_api}` flag;
  decision log now carries `structure_missing_fields` and
  `structure_subscription_active`.
- Streamlit sidebar: structure-provider dropdown; clear warning if user
  picks `zerosigma_api` but env isn't configured (or boot fails); status
  panel shows `provider.status()` (no secrets) + the missing-field list.
- Env: `.env.example` lists `ZS_API_AUTH_MODE`, `ZS_API_TOKEN`,
  `ZS_API_USERNAME`, `ZS_API_PASSWORD`, `ZS_API_SERVICE_KEY`,
  `ZS_API_TIMEOUT_SECONDS`, `ZS_API_VERIFY_SSL`, `ZS_API_MAX_RETRIES`,
  `ZS_API_ENABLE_EXPOSURE_SERIES`, `ZS_API_ENABLE_DDOI`,
  `ZS_STRUCTURE_PROVIDER`. **No real secrets in the repo.**
- 9 new tests against `httpx.MockTransport` — happy path, 403 graceful
  degrade, missing exposures payload, service-token auth handshake,
  unconfigured no-network behavior, no-secret-leak invariant, factory
  default/explicit/unknown selection. Total: 51/51 passing.
- Ruff clean.
- Demo unchanged when running with default safe mode: stub structure +
  mock quotes → `TRADE_CALL_CREDIT` SPX 5815/5820 @ $0.60 credit, score 0.61.

### Next step

Phase 3: Vertical Wing v1 end-to-end paper P&L runs against live ZS API
context (where available) + mock quotes. Decide gap-closure path for the
four unexposed structure fields (plan.md §14.8). Phase 4 broker probe can
run in parallel since it's independent.

---

## 2026-06-01 (Phase 2.5) — `public_only` auth mode + smoke script

- Added fifth auth mode `public_only` to
  `ZeroSigmaApiStructureProvider`. It allows live calls to
  `/api/v1/market/snapshot` (public endpoint) without an `Authorization`
  header AND silently skips `/api/v1/exposure/series` (subscription-gated)
  regardless of `enable_exposure_series` — so volume-derived VW levels
  (`PUT_CEILING_{2K,5K}`, `CALL_FLOOR_{2K,5K}`, `MaxVol`) come back as
  `None` and are listed in `missing_fields`. **No secrets required.**
- New module-level constant `_AUTHED_MODES = {"bearer", "login",
  "service_token"}` and helper `_use_authed_endpoints()` — the
  authoritative gate for whether the provider may attach a Bearer header.
  `public_only` is explicitly NOT in that set.
- `status()` now reports `public_only: bool` and
  `exposure_series_effective: bool` (true only if `enable_exposure_series`
  AND the auth mode actually supports auth headers). The cockpit reads
  the effective flag, not the raw config flag, so the warning is honest.
- New `scripts/smoke_zs_api.py` — a credentials-free smoke test for the
  ZS API integration. Loads `.env` + config, builds the real provider,
  calls `get_snapshot(symbol)` once, prints a sanitized summary (allow-listed
  status + exposure fields). Never prints tokens/passwords/service keys.
  Exit codes: 0 on success, 0 with warning when unconfigured (CI-safe),
  1 with a clean type-only message when configured-but-failed.
- `.env.example` defaults shifted: `ZS_API_AUTH_MODE=public_only`,
  `ZS_API_ENABLE_EXPOSURE_SERIES=false`, `ZS_API_MAX_RETRIES=1`,
  `ZS_STRUCTURE_PROVIDER=stub`. Added a comment block explaining the
  three read paths (stub → public smoke → authenticated).
- Streamlit cockpit: provider status panel now prominently shows
  `auth_mode`, `configured`, `exposure_series_effective`. When
  `public_only` is active a blue info banner explains why VW levels are
  None and how to flip to a credentialed mode.
- 9 new tests covering: snapshot WITHOUT Authorization header under
  `public_only`, `/exposure/series` correctly skipped, `status()` reports
  the effective flag, status doesn't leak left-over secrets,
  `auth_mode=none` makes zero HTTP calls, regression on the bearer flow,
  smoke script in three states (unconfigured warning, mocked happy path,
  500 → exit 1 with no traceback), scanner subprocess in stub mode.
  Total: 60/60 passing (was 51).
- Ruff clean.

### Next step

Phase 3: VW v1 end-to-end runs against either stub (default) or
`public_only` live ZS context + mock quotes. After Dan tests
`scripts.smoke_zs_api` against the real API and confirms response shapes
match the contract in `docs/reference_notes.md §8a`, we can enable
authenticated `/exposure/series` to populate VW levels. Phase 4 broker
probe remains parallelizable.
