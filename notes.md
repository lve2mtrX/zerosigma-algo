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

---

## 2026-06-01 (Phase 2.6) — structure↔quote alignment + ZS shape fix

**Root cause of the smoke-test gap (auth_mode=login working, but
`spot=0.0`, `total_*_bn=None`, scanner emitting NO_TRADE):** the
`ZeroSigmaApiStructureProvider` mapper was written against an
*assumed* response shape (`snapshot.spot.price`, `total_gex_bn`,
`da_gex_bn`, `vex`, `dex`, `cex`). The actual ZS API serves whatever
`worker_watchlist.py` writes into Redis, where the canonical names are:

  - `spot.spot`           (scalar — the price)
  - `total_gex_1pct`, `total_raw_gex_1pct`, `total_da_gex_1pct`,
    `total_dex_1pct`, `total_vex_1vol`, `total_cex`
  - `wings.{call_floor, put_ceiling, midline}`
  - `gamma.{regime, flip, cluster_primary, ...}` (regime is `"Positive"`
    / `"Negative"` — capitalized)
  - `max_call_oi_strike`, `max_put_oi_strike`,
    `max_call_vol_strike`, `max_put_vol_strike`, `atm_strike`

**Mapper rewrite (`src/providers/structure/zerosigma_api.py`):**

- Spot now walks an alias chain: `spot.spot` → `spot.price` →
  `spot.last` → `spot.close` → `spot_price` → `exposures.spot`.
- Exposure field names accept the real ZS names FIRST, fall through to
  the older test-fixture names so Phase 2 / 2.5 fixtures still pass.
- New: surface `wings.put_ceiling` / `wings.call_floor` as the 2K-tier
  values when the subscription-gated `/exposure/series` isn't available.
  Means `public_only` mode now populates anchors automatically.
- New: derive `gamma_flip`, `call_wall` (`max_call_oi_strike`), and
  `put_wall` (`max_put_oi_strike`) from the public payload. The
  `missing_fields` list shrinks from 9 to 3 (only `put_ceiling_5k`,
  `call_floor_5k`, `ddoi_pin` stay None without the subscription).
- `gamma_regime` is now lowercased on intake (ZS sends `"Positive"`).
- Locals for `total_dex_1pct` and `total_cex` read but not yet stored —
  `ExposureContext` doesn't expose them. Reserved for a follow-up.

**Second root cause of zero candidates (even after the mapper fix):**
live SPX structure puts ceilings/floors around 7580 while the
`MockQuoteProvider` had a hardcoded 5800-centered chain. The strategy
asked the chain for strikes at 7600 / 7605 / 7560 / 7555 and got
`None` for every leg — no candidates generated, but the explanation
falsely said "all rejected by filters."

**Structure-aware quote alignment:**

- New `QuoteRequest` dataclass in `src/providers/quotes/types.py` with
  `symbol`, `expiry`, `spot_hint`, `required_strikes`, `strike_min/max`,
  `spot_hint_source`. Carried in `QuoteProvider.get_option_chain(...,
  request=...)`. Real broker providers ignore it; synthesis providers
  use it.
- `MockQuoteProvider.get_option_chain` now has two modes:
  - **default** (no request): returns the static `MOCK_CHAIN`
    centered on 5800 — Phase 1.5 / 2 / 2.5 behavior is unchanged.
  - **aligned** (request with `spot_hint` or `required_strikes`):
    synthesizes a chain centered on the hint, builds a 5-pt grid
    spanning ±25pt, and UNIONs in every required strike (even if
    off-grid). Each synthesized strike that happens to match a row in
    `MOCK_CHAIN` inherits its static `c_mid/p_mid/c_volume/p_volume`
    — preserves Phase 1.5 default behavior to the byte when the hint
    is near 5800.
- New `Strategy.required_quote_strikes(structure, params) -> list[float]`
  contract. `VerticalWingV1` implements it: collects the active
  ceiling/floor (per `volume_threshold`) and the long-leg partners (per
  `spread_width`). No VW-specific code leaks into the scanner.
- Scanner runner now derives a `QuoteRequest` from
  `_pick_spot_hint(structure, required_strikes)` — precedence:
  `structure.spot if > 0` → `structure.exposures.maxvol` → median of
  required strikes → mock_default — and passes it through.

**Sharpened zero-candidate explanation:**

`_refine_decision_explanation` (scanner) replaces the generic
"all rejected by filters" message when `decision.all_candidates` is
empty, distinguishing three cases:

  1. `no_structure_anchors` — `put_ceiling_*` and `call_floor_*` all None
  2. `quote_chain_missing_legs` — anchors present but chain missed the
     required strikes
  3. `all_candidates_rejected` — fall-through (original message kept)

The decision log's `snapshot_summary` gains:

  - `required_strikes` (list)
  - `quote_chain_min_strike`, `quote_chain_max_strike`
  - `missing_required_quote_strikes`
  - `quote_spot_source`  ∈ `structure_spot | maxvol | structure_midpoint | mock_default`
  - `quote_spot_hint`

**Smoke script:**

- `--endpoint {spot|exposures|snapshot}` probes a single ZS endpoint
  directly via the provider's HTTP client.
- `--debug-shape` renders the response as a sanitized SHAPE: scalar
  fields pass through (so spot=0.0 is visible), string values are
  reduced to `<str len=N>` (so a token-shaped field can never leak),
  list values become `<list len=N, first_item_shape=...>`, and any key
  matching `token`/`password`/`service_key`/`secret`/`authorization`/
  `bearer`/`api_key`/`apikey`/`private`/`jwt` is replaced with
  `<REDACTED>`.
- Combined: `--endpoint exposures --debug-shape` is the right tool to
  validate that ZS's `/market/exposures` payload still matches the
  contract in `docs/reference_notes.md §8a`.

**Test additions (17 new):**

- `test_real_zs_shape_maps_spot_and_exposures_correctly` — locks the
  real-shape mapper.
- `test_real_zs_shape_with_volume_series_populates_5k_tier_too` —
  series wins over wings when both are present.
- `test_mock_quote_provider_recenters_around_spot_hint` (and 3 more) —
  alignment + default-mode back-compat + required-strikes inclusion.
- `test_vertical_wing_required_quote_strikes_uses_{2k,5k}_tier_*` —
  threshold-driven anchor selection.
- `test_vw_produces_both_sides_against_real_like_structure_plus_mock_chain`
  — end-to-end: structure at 7580 → aligned mock chain → both
  CALL_CREDIT and PUT_CREDIT candidates with `credit > 0`.
- `test_scanner_decision_log_includes_phase2p6_diagnostics` — locks
  the new audit fields.
- `test_zero_candidate_explanation_{no_structure_anchors, quote_chain_missing_legs, preserves_real_rejection_text}`
  — locks the three branches.
- `test_debug_shape_redacts_secret_keys_and_string_values` — sanitizer
  contract.
- `test_endpoint_probe_via_mocked_provider` — smoke `--endpoint
  exposures --debug-shape` end-to-end without live network.

Total: **77/77 passing** (was 60, +17). Ruff clean.

**Still missing from `ExposureContext` after Phase 2.6:**

- `put_ceiling_5k` / `call_floor_5k` — require subscription-gated
  `/exposure/series` (Phase 2 path; works when `auth_mode != public_only`
  and `enable_exposure_series=true` + subscribed account).
- `ddoi_pin` — `/exposure/ddoi` is also subscription-gated AND requires
  `DO_SPACES_*` to be configured server-side. None on launch.
- (None of the above blocks VW v1 — `put_ceiling_2k` and `call_floor_2k`
  are populated from `wings.*` under `public_only`.)

### Next step

Phase 3: VW v1 end-to-end against live ZS structure + mock quotes.
With Phase 2.6 the smoke output should show `spot ≈ 7580`,
`total_gex_bn ≈ 1234` (real ZS `total_gex_1pct`), and the scanner
should produce candidates at the structure-derived strikes. If
candidate generation still fails, the refined explanation tells the
operator whether to blame structure (anchors missing), the chain
alignment (required strikes outside chain bounds), or the risk filters
(legitimate gating).

---

## 2026-06-01 (Phase 2.7) — score-breakdown observability

**Observed pain (live structure, mock quotes):** both
CALL_CREDIT (7600/7605 @ 0.50, score 0.4412) and PUT_CREDIT
(7550/7545 @ 0.50, score 0.4639) cleared the hard filters but scored
below the 0.60 no-trade threshold. The scanner emitted `NO_TRADE` but
the operator couldn't tell WHICH score components were pulling each
candidate down — and the explanation read "all rejected by filters"
even though nothing was filter-rejected.

**Important framing**: this round is observability, not tuning. No
scoring weights changed. No threshold moved. No components added or
removed. The goal is to make scoring readable.

### Data-model changes

`Candidate` (in `src/strategies/base.py`) gained four optional fields,
all populated by `Strategy.select()`:

- `score_threshold: float | None` — the `no_trade_score_threshold` the
  decision was measured against
- `score_gap_to_threshold: float | None` — `threshold − score` (negative
  for candidates that cleared)
- `weak_components: list[str]` — top-2 lowest non-meta components,
  formatted `"name=0.42"`
- `rejection_type: RejectionType | None` — one of
  `selected | score_below_threshold | filter_rejected | no_candidates | missing_quotes | missing_structure`

`StrategyDecision` gained `threshold_used`, `rejection_type`,
`best_score`, `weak_components` — same data at the decision level.

New helper `weak_components_of(breakdown, n=2)` in `base.py`. The
`SCORE_META_KEYS` constant lists the keys it skips
(`final_score`, `no_trade_threshold`, `score_gap_to_threshold`).

### `score_breakdown` enrichment

`score_candidate()` now stamps `final_score` into the breakdown dict
before returning. `VerticalWingV1.select()` also stamps
`no_trade_threshold` and `score_gap_to_threshold` per candidate. So a
typical post-`score` + post-`select` breakdown has 11 keys: 8 components
+ 3 meta.

`time_decay_headroom` is now explicitly documented as a placeholder
(returns 0.5 regardless of time-of-day). Calling it out in the score
breakdown rather than removing it keeps the contract stable for when
intraday clock data lands.

### Decision-explanation rewrite

`VerticalWingV1.select()` distinguishes three NO_TRADE paths in its
explanation:

1. **All filter-rejected** — `"NO_TRADE — all N candidate(s) rejected by hard filters. Reasons: [...]"` + sets `decision.rejection_type = "filter_rejected"`.
2. **Best below threshold** — `"NO_TRADE — best candidate <SIDE> <K1>/<K2> @ <credit> scored <score>, below threshold <T> by <gap>. Weakest components: <a=v>, <b=v>."` + sets `decision.rejection_type = "score_below_threshold"`.
3. **Selected** — unchanged ("Selected <SIDE> K=<K1>/<K2> credit=<credit> score=<score>") + `decision.rejection_type = "selected"`.

Phase 2.6's `_refine_decision_explanation` in
`scripts/run_scanner.py` still runs on top of `select()` to handle the
two upstream branches it doesn't see (no anchors / missing chain legs).

### CSV (`outputs/latest/ranked_candidates.csv`)

Columns added:

- `final_score`, `no_trade_threshold`, `score_gap_to_threshold`,
  `rejection_type`, `weak_components`
- One column per scoring component:
  `score_credit_size`, `score_credit_to_risk`, `score_distance_from_spot`,
  `score_structure_strength`, `score_maxvol_alignment`,
  `score_gamma_regime`, `score_bid_ask_quality`,
  `score_time_decay_headroom`
- `score_breakdown_json` — the full dict serialized for tools that
  don't want to enumerate the per-component columns

The existing `planned_loss_dollars` column is kept for back-compat —
README documents that it equals planned stop risk dollars under the
session's `default_stop_variant`.

### JSONL (`outputs/latest/decision_log.jsonl`)

Per-decision: `threshold_used`, `rejection_type`, `best_score`,
`weak_components`.

Per-candidate (in `all_candidates` + `selected_candidate`):
`score_threshold`, `score_gap_to_threshold`, `weak_components`,
`rejection_type` (in addition to the existing `score_breakdown`).

### Streamlit cockpit

Candidate dataframe gains four columns (`gap`, `rejection`, `weak`,
keeping `rejection_reasons` separate). Below the dataframe, every
candidate gets its own `st.expander` with:

- top-row metrics (Score / Threshold / Gap / Rejection type)
- "Weakest components" as inline code chips
- Full `score_breakdown` JSON in a nested expander
- The "selected" winner's expander is `expanded=True` by default

### 12 new tests in `tests/test_phase2p7_observability.py`

- `score_breakdown` contains every component + `final_score`
- `select()` stamps threshold + gap onto every candidate (including filter-rejected)
- `weak_components_of` excludes meta keys + returns lowest n
- `weak_components_of` handles None / empty / all-meta input
- `rejection_type` is "selected" for winner, "score_below_threshold" for runner-up
- Manual filter-reject path correctly tags `filter_rejected`
- All-filter-rejected branch: decision and every candidate tagged `filter_rejected`
- Below-threshold explanation names best/threshold/gap/weak
- CSV includes per-component columns + `score_breakdown_json` + meta cols
- CSV `rejection_type` distinguishes selected vs below-threshold
- JSONL decision + candidate dicts carry Phase 2.7 fields
- Streamlit module imports without error

Total: **89/89 passing** (was 77, +12). Ruff clean.

### Next step

Compare scoring output against discretionary expectations. Run the
scanner against the live ZS structure on a market session, capture the
ranked_candidates.csv, and Dan walks each candidate to say "this score
matches my read" or "this score is too high/low because of X." Then —
and only then — parameterize the scoring weights into
`config/strategies.yaml` so the session config can override them.
