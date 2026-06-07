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

---

## 2026-06-01 (Phase 2.8) — anchor-volume correctness

**Symptom**: live ZS produced
`CALL_CREDIT 7600/7605 @ 0.50 score 0.4182` and
`PUT_CREDIT 7550/7545 @ 0.50 score 0.4775`, both with
`structure_strength=0.00` in the weak-components list. But `/exposure/series`
returned real volumes that qualified the 7600 / 7550 anchors — the
scores should have been > 0.

**Root cause**: `anchor_volume` in the candidate metadata was being read
from the **QuoteProvider**, not from the **StructureProvider**. Code
path:

```
zerosigma_api._build_exposures
  → _highest_strike_where(strikes, puts, 2000)
  → returns the WINNING STRIKE only (5815 or 7600), discards the volume

candidates.build_put_ceiling_call_credit
  → chain.find(short_k, OptionType.PUT).volume
  → in ALIGNED mock mode for a 7600 strike that's NOT in MOCK_CHAIN,
    MockQuoteProvider._synth_quote returns volume=100.0 (token)

scoring._structure_strength_score
  → (100 - 1000) / 4000 = -0.225 → clipped to 0.0
```

The ZS volume series carried `puts[strikes.index(7600)] = 2400`
(or whatever the live number was) — but my mapper never recorded it.

### Fix — carry structure volumes through the data path

`ExposureContext` (in `src/providers/structure/types.py`) gained five
optional fields:

- `put_ceiling_2k_volume` / `put_ceiling_5k_volume`
- `call_floor_2k_volume` / `call_floor_5k_volume`
- `maxvol_volume` (combined call+put volume at the maxvol strike)

`ZeroSigmaApiStructureProvider._build_exposures` now uses a small
`_volume_at(strikes, series, strike)` helper to lift the actual volume
at each derived anchor. The new `maxvol_volume` records the **combined**
volume (calls + puts) at the maxvol strike, not just one side.

`StubStructureProvider` was updated for parity. New helpers
`put_volume_at(strike)`, `call_volume_at(strike)`, `maxvol_total_volume()`
in `src/providers/_mock_data.py` expose the MOCK_CHAIN volumes.

### VW candidate construction

`_ceiling_for_threshold` and `_floor_for_threshold` now return a
3-tuple: `(strike, anchor_source, anchor_volume_from_structure)`.
`anchor_source` is one of `"put_ceiling_2k" | "put_ceiling_5k" |
"call_floor_2k" | "call_floor_5k"` — exactly identifies which level the
strategy picked under the current threshold.

In `build_put_ceiling_call_credit` and `build_call_floor_put_credit`:

- **If** the structure-reported volume is not None → use it; tag
  `anchor_volume_source = "zs_exposure_series"`.
- **Else** fall back to the QuoteProvider's volume at that strike;
  tag `anchor_volume_source = "quote_provider_fallback"`. (This is
  what runs under `public_only`, where the public payload gives us the
  level via `wings.*` but no volume.)

The fallback is honest about itself: a downstream auditor can grep for
`quote_provider_fallback` to find candidates whose structure_strength
was driven by chain volume instead of ZS structure volume.

### Scoring neutral-fallback policy

`_structure_strength_score(volume_at_anchor, anchor_source=...)` now
returns `(score, source_label)`:

| Input | Score | Source label |
|---|---|---|
| Volume present | `(vol - 1000) / 4000`, clipped | `zs_volume_series` |
| Volume None, anchor_source present | **0.5** (neutral) | `missing_anchor_volume_neutral` |
| Volume None AND anchor_source None | 0.0 | `no_anchor` |

Rationale: the structure provider giving us a level (even without the
volume magnitude) already implies SOME structure existed at that
strike. Scoring it to 0 silently was the bug we just fixed; this
policy preserves the signal under public_only while keeping the
"genuinely no anchor" path at 0.

The label lands on `candidate.meta["structure_strength_source"]` so the
CSV / JSONL / Streamlit can show it.

### CSV / JSONL / Streamlit

CSV gains four columns:
`anchor_source, anchor_volume, anchor_volume_source, structure_strength_source`.

JSONL carries them under each candidate's `meta` (no separate field —
they ride with `meta`, which the decision_log already serializes whole).

Streamlit candidate expander gains a 4-metric row showing all four.

### 15 new tests in `tests/test_phase2p8_anchor_volume.py`

- ZS mapper stores volume at each anchor strike (from
  `_volume_at(strikes, series, strike)`)
- ZS mapper leaves anchor volumes None under public_only (no series)
- Stub provider populates anchor volumes from MOCK_CHAIN
- VW CALL_CREDIT uses `structure.put_ceiling_2k_volume`, NOT chain volume
- VW PUT_CREDIT uses `structure.call_floor_2k_volume`, NOT chain volume
- VW falls back to chain volume with `quote_provider_fallback` source tag
  when structure has the level but no volume
- `_structure_strength_score` parametrized over the volume → score curve
- Neutral 0.5 when level present but volume missing
- 0.0 only when no anchor at all
- CSV includes all four new columns + structure_strength > 0 in stub mode
- JSONL per-candidate meta carries all four labels

Total: **104/104 passing** (was 89, +15). Ruff clean.

### Worked example — what changed for the user

Before Phase 2.8 (live ZS, 7600 ceiling, mock-aligned chain):

```
weak_components: structure_strength=0.00, credit_to_risk=0.14
score_structure_strength: 0.0
anchor_volume: 100  (from synthesized mock — token)
anchor_volume_source: (not recorded)
```

After Phase 2.8 (same scenario, structure now carries actual volume):

```
weak_components: credit_to_risk=0.14, time_decay_headroom=0.50
score_structure_strength: 0.35     # = (2400 - 1000) / 4000
anchor_source: put_ceiling_2k
anchor_volume: 2400                # the real puts[i] from /exposure/series
anchor_volume_source: zs_exposure_series
structure_strength_source: zs_volume_series
```

The final candidate score still won't clear 0.60 with $0.50 credit on a
5-wide — that's `credit_to_risk` doing its job, exactly as Phase 2.7
intended. But `structure_strength` no longer falsely lies that the
trade has no structure behind it.

### Still NOT in this round

- No scoring weights changed.
- No threshold changed.
- No broker integration. **Tastytrade QuoteProvider remains the next
  phase** — Phase 3 broker capability probe, then real per-strike
  volumes from the broker (which will INVALIDATE the
  `quote_provider_fallback` path because real broker quotes are
  authoritative). Until then, ZS structure volume beats broker
  fallback.

### Next step

Run the live scanner again. The expected outcome:

```powershell
python -m scripts.run_scanner --structure-provider zerosigma_api

Import-Csv .\outputs\latest\ranked_candidates.csv |
  Select-Object side,short_strike,credit,score,
                score_structure_strength,anchor_source,anchor_volume,
                anchor_volume_source |
  Format-Table -AutoSize
```

Expected: `anchor_volume` shows the ZS-reported volume at the live
anchor strike (some 4-5 digit number), `anchor_volume_source =
zs_exposure_series`, `score_structure_strength` between 0.2 and 1.0
depending on how strong that strike's volume is.

---

## 2026-06-01 (Phase 3) — Tastytrade capability probe scaffold

**Why now**: ZS structure is correct enough for live VW scoring. The
remaining blocker for end-to-end is real per-strike option quotes —
mock prices can't validate the strategy. Tastytrade is Dan's account
broker of choice; before wiring a production QuoteProvider we need to
confirm what the Tasty API actually supports for this use case.

**Research method**: parallel workflow agent against
`developer.tastytrade.com` + the unofficial `tastyware/tastytrade`
Python SDK source code. Findings landed verbatim in
`docs/reference_notes.md §8b` with URL citations.

### Headline findings (full contract in §8b)

- **Base URLs**: `api.tastyworks.com` (prod), `api.cert.tastyworks.com`
  (sandbox). Note the `tastyworks.com` domain, NOT `tastytrade.com`.
- **Auth**: two flows coexist. Legacy `POST /sessions` with
  `{login, password, remember-me}` returns `data.session-token`, used
  as a BARE `Authorization: <token>` header (no `Bearer ` prefix).
  Tastytrade announced sunset (community references say Dec 1, 2025 —
  verify empirically before relying long-term). OAuth2 (`/oauth/token`,
  Bearer-prefixed, 900s access tokens) is the durable path forward.
- **Accounts**: `GET /customers/me/accounts`.
- **Chains**: `GET /option-chains/{symbol}/nested` (expirations →
  strikes → call/put + streamer symbols). SPX and SPXW are SEPARATE
  underlyings on the same `/option-chains/SPX/...` payload —
  AM-settled vs PM-settled, the latter is the 0DTE family VW targets.
- **Quotes**: REST via `GET /market-data/by-type?equity-option=SYM1,SYM2,...`
  up to 100 symbols per call. Returns bid/ask/mid/last/mark.
- **DXLink**: `GET /api-quote-tokens` returns
  `{token, dxlink-url, level}`. WebSocket protocol is DXFeed DXLink
  (SETUP → AUTH → CHANNEL_REQUEST → FEED_SUBSCRIPTION + KEEPALIVE).
- **Dry-run** (no-routing preview): `POST /accounts/{n}/orders/dry-run`
  and `/complex-orders/dry-run`. Safe by design.
- **Sandbox**: 15-min delayed quotes; 24-hour position reset. Index
  options (SPX/SPXW) availability in cert is undocumented — treat as
  empirical (catch 422 on chain).
- **Rate limits**: not publicly documented. Community SDKs self-
  throttle ~2 req/s. Tastytrade inspects User-Agent — descriptive UA
  required.

### Probe scaffold

`src/providers/quotes/tasty_probe.py`:

- `TastyProbeConfig` dataclass — env, base_url, username, password,
  account_number, use_dxlink, timeout, verify_ssl, user_agent.
  `__repr__` redacts password and account number to last-4. Read from
  `.env` via `config_from_env()`.
- `TastyProbeStatus` dataclass — `configured`, `auth_attempted`,
  `auth_success`, `session_token_present`, `last_http_status`,
  `last_error`. `sanitize()` returns a dict safe to print.
- `TastyProbeClient` — narrow class with just the read-only methods:
  `login()`, `list_accounts()`, `get_option_chain_summary(symbol)`,
  `get_option_quotes(equity_option_symbols)`, `get_dxlink_token()`,
  `capabilities_summary(symbol)`. HTTP client is injectable via
  `client_factory=...` so tests use `httpx.MockTransport` — no live
  network in CI.

Explicitly **NOT implemented** — three stubs that raise
`NotImplementedError`:

```python
TastyProbeClient.submit_order()
TastyProbeClient.submit_complex_order()
TastyProbeClient.open_streaming()
```

These exist so future code that imports the class and tries to do
something dangerous fails loudly. The probe's `dir(class)` is checked
in tests for `place_order` / `route` / `execute` / `preview` /
`dry_run` — none of those names exist on the class.

### CLI (`scripts/probe_tastytrade.py`)

Subcommands (mutually exclusive):

- `--auth-only` — POST `/sessions` only
- `--accounts` — login + list accounts (redacted)
- `--chain --symbol SPX` — login + nested chain summary
- `--quotes --symbol SPX --expiry YYYY-MM-DD --strikes K1,K2,... --right C|P` — bulk REST quotes
- `--capabilities --symbol SPX` — full matrix

Plus `--json` and `--symbol` modifiers. Builds OCC 21-char symbols
locally for `--quotes` (root padded to 6 chars, expiry `YYMMDD`,
right C/P, strike `*1000` as 8-digit padded integer).

Exit codes:

- `0` configured + probe ran
- `0` unconfigured (clean warning, no traceback)
- `1` configured but a hard failure (network / unexpected) — exception
  TYPE only, never values
- `2` bad arguments

### Sanitization invariants (locked by tests)

- `TastyProbeStatus.sanitize()` does not contain
  `password` / `hunter2` / `session-token` / `Bearer ` substrings.
- `TastyProbeConfig.__repr__` does not contain the password, full
  account number, session token, or remember token; instead shows
  `password_present=True/False` and `account='****1234'`.
- Authenticated requests use BARE `Authorization: <token>`, NOT
  `Bearer <token>` — there's a regression test for this.
- `--quotes` request never echoes the requested OCC symbols back into
  log output as a way to confirm they look right; tests assert the
  query string carries them but the rendered output uses only counts.
- `--capabilities` never opens the WebSocket. The test uses
  `httpx.MockTransport` which physically can't carry a `wss://` URL.

### 19 tests in `tests/test_phase3_tasty_probe.py`

Coverage matrix:

| # | What it locks |
|---|---|
| 1 | Unconfigured client never makes HTTP, never echoes secrets in status |
| 2 | `_redact_account` helper math |
| 3 | `__repr__` redacts password + account + tokens |
| 4 | Login body shape (`login` + `password` + `remember-me: true`) |
| 5 | Login output never includes raw token values |
| 6 | Authenticated requests use BARE token (NO `Bearer ` prefix) |
| 7 | Auth HTTP error sanitized (no traceback, no values) |
| 8 | Account list redacts account numbers to last-4 |
| 9 | Chain summary maps SPX vs SPXW roots + 0DTE detection |
| 10 | Quotes map bid/ask/mid/last/mark from `/market-data/by-type` |
| 11 | Quotes cap at 100 symbols per Tasty `by-type` limit |
| 12 | DXLink token check confirms token presence WITHOUT opening WS |
| 13 | `submit_order` / `submit_complex_order` / `open_streaming` all raise NotImplementedError |
| 14 | Probe class doesn't expose forbidden names (`place_order` / `route` / etc.) |
| 15 | `capabilities_summary` runs the full sequence under mock |
| 16 | `capabilities_summary` under auth failure returns a partial matrix (not a crash) |
| 17 | CLI `--help` renders without credentials |
| 18 | CLI unconfigured returns 0 with a clean warning (no traceback) |
| 19 | CLI `--capabilities --json` drives the full path under mocked transport, never leaks creds |

Total project tests: **123/123** (was 104, +19 new). Ruff clean.

### Still NOT in this round

- **No production `TastytradeQuoteProvider`.** That comes after Dan
  runs the probe against a real account (sandbox or live) and we
  review the capability matrix together. Key questions the probe will
  answer empirically:
  - Does cert support SPX/SPXW chains end-to-end?
  - Does the legacy `/sessions` flow still work, or has it been
    migrated to OAuth2 only?
  - Are real-time quotes available without a DXFeed entitlement
    purchase?
- **No DXLink WebSocket implementation.** Phase 3 only confirms the
  token endpoint is reachable. Real streaming is a separate task.
- **No order paths of any kind** — not even `/dry-run`. That belongs
  behind an explicit opt-in CLI flag after the rest is stable.
- **No scanner wiring.** The scanner still uses `MockQuoteProvider`.

### Next step (after the probe runs)

1. Dan adds `TASTY_USERNAME` + `TASTY_PASSWORD` to local `.env`.
2. Run `python -m scripts.probe_tastytrade --capabilities --symbol SPX`.
3. Capture the capability matrix + any 4xx / 5xx + which root symbols
   appeared in the chain.
4. Phase 4 plan based on the results:
   - If cert supports SPX/SPXW + real quotes work → implement
     `TastytradeQuoteProvider` as a thin wrapper over the probe client
     plus the DXLink WebSocket.
   - If cert returns 422 on SPX → move probe to production with a
     paper-only sandbox account; production DXFeed entitlement may be
     required for real-time SPX/SPXW.
   - If OAuth2 is required → implement `/oauth/token` flow in the
     probe before the real provider.

---

## 2026-06-01 (Phase 3 extension) — OAuth refresh + scope parser + hard safety gate

**Driver**: Dan's actual `.env` was already populated with OAuth fields
(`TASTY_CLIENT_ID`, `TASTY_CLIENT_SECRET`, `TASTY_REDIRECT_URI`,
`TASTY_SCOPES=read trade openid`) plus the new safety knobs
(`TASTY_ALLOW_TRADE_SCOPE=true`, `TASTY_ENABLE_ORDER_SUBMISSION=false`).
The Phase 3 probe shipped with only the legacy `/sessions` path —
extending so it can handle the real config without leaking secrets and
without ever lifting the execution gate just because trade scope happens
to be granted.

### `TastyProbeConfig` (extended)

New fields:
- `client_id`, `client_secret`, `redirect_uri`, `refresh_token` —
  OAuth Personal Application credentials.
- `scopes: list[str]` — parsed via `_parse_scopes()`.
- `allow_trade_scope: bool` (default True) — lets the OAuth app keep
  `trade` in its scope list without the probe complaining.
- `enable_order_submission: bool` (default **False**) — the HARD
  execution gate. Phase 3 only ever READS this for reporting.

Derived helpers:
- `has_oauth()` → True when client_id + client_secret + refresh_token
  are ALL set.
- `has_legacy_session()` → True when username + password are set.
- `auth_mode()` → `"oauth"` | `"legacy_session"` | `"none"`.
- `trade_scope_present()` → True if `"trade"` is in the parsed scopes.
- `missing_fields()` → list of TASTY_* env names still empty.

`__repr__` extended to surface auth_mode + every credential's
`*_present` boolean + safety-gate state, but NEVER a single secret
value. Sample under partial config:

```
TastyProbeConfig(env='certification', base_url='...',
  auth_mode='none',
  username_present=False, password_present=False,
  client_id_present=True, client_secret_present=True,
  refresh_token_present=False,
  scopes=['read', 'trade', 'openid'], trade_scope_present=True,
  enable_order_submission=False,
  account='', use_dxlink=False)
```

### `_parse_scopes()` helper

```python
_parse_scopes("read trade openid")   # → ["read", "trade", "openid"]
_parse_scopes("read,trade,openid")   # → ["read", "trade", "openid"]
_parse_scopes("read, trade openid")  # → ["read", "trade", "openid"]  (mixed OK)
_parse_scopes("  READ  Trade  ")     # → ["read", "trade"]            (case + ws)
_parse_scopes("read trade trade")    # → ["read", "trade"]            (deduped)
_parse_scopes(None)                  # → []
```

Splits on commas first, then whitespace within each piece. Lowercases.
Dedupes preserving order. Locked by `@pytest.mark.parametrize`.

### OAuth refresh login

`login_oauth()` POSTs to `/oauth/token` with
`grant_type=refresh_token&client_secret=...&refresh_token=...`
(form-urlencoded — the ONE Tasty endpoint that's not kebab-case JSON).
On success it stores the `access_token` internally and switches
`_auth_mode_used = "oauth"` so `_auth_headers()` returns
`Authorization: Bearer <token>` instead of the legacy BARE format.

`login()` is the dispatcher:
1. If config has OAuth (`client_id + client_secret + refresh_token`),
   → `login_oauth()`.
2. Else if config has legacy (`username + password`),
   → `login_legacy_session()`.
3. Else → sanitized "not configured" reply, NO HTTP call.

Both login flows emit:
- `auth_mode` (so callers can tell which path ran)
- `trade_scope_present` + `order_submission_enabled` on success
  (informational; the gate isn't read for anything else in Phase 3)

### `SafetyGateError`

```python
class SafetyGateError(RuntimeError):
    """Raised when code tries to perform an account-changing action while
    the safety gate is closed."""
```

`submit_order()` and `submit_complex_order()` now raise this (was
generic `NotImplementedError`). The message explicitly references
"Phase 3 is read-only" and "trade scope and TASTY_ENABLE_ORDER_SUBMISSION
are tracked for future capability ONLY." `open_streaming()` stays
`NotImplementedError` because it's a feature gap, not a safety boundary.

### `--config` CLI subcommand

New `python -m scripts.probe_tastytrade --config` prints a sanitized
config dump via `TastyProbeClient.config_summary()`:

```jsonc
{
  "provider":     "tasty_probe",
  "configured":   false,
  "auth_mode":    "none",
  "env":          "certification",
  "base_url":     "https://api.cert.tastyworks.com",
  "redirect_uri": "https://localhost:8000",
  "scopes":       ["read", "trade", "openid"],
  "trade_scope_present":           true,
  "trade_scope_allowed":           true,
  "order_submission_enabled":      false,
  "execution_blocked_by_safety_gate": true,
  "execution_status_note":         "trade scope is FUTURE-only; this phase NEVER submits orders",
  "credentials_present": {
    "username":       false, "password":     false,
    "client_id":      true,  "client_secret": true,
    "refresh_token":  false, "account_number": false
  },
  "account_redacted": "",
  "use_dxlink":       false,
  "timeout_seconds":  10,
  "verify_ssl":       true,
  "missing_fields":   ["TASTY_REFRESH_TOKEN", "TASTY_USERNAME", "TASTY_PASSWORD"]
}
```

**Critical**: `--config` runs BEFORE the unconfigured short-circuit in
`main()`, so it works without ANY credentials. Test
`test_cli_config_runs_before_unconfigured_short_circuit` locks this.

### `capabilities_summary` extensions

Reports the following new keys (all keyed off config, no extra HTTP):
- `trade_scope_present` (bool)
- `trade_scope_allowed` (bool)
- `order_submission_enabled` (bool — reflects the gate)
- `execution_blocked_by_safety_gate` (bool — inverse of above)
- `probe_exposes_submit_path` (bool — always **False**)
- `has_dxlink` (bool — aliases `has_streaming_token`)
- `has_certification_or_sandbox` (bool — env=='certification')
- `has_paper_or_sandbox_order_support` (`"yes_per_docs"` |
  `"unknown_in_production"`)

### Tests added (+19 → 38 in this module)

| # | What it locks |
|---|---|
| `_parse_scopes` parametrized | 7 input variants (space / comma / mixed / case / dedup / "" / None) |
| `enable_order_submission_defaults_false` | Trade scope alone doesn't open the gate; status reports `execution_blocked_by_safety_gate=True` |
| `trade_scope_alone_does_not_enable_execution` | `submit_order` / `submit_complex_order` raise `SafetyGateError` even with token + trade scope |
| `safety_gate_message_mentions_trade_scope_and_phase3` | Error message context is informative |
| `oauth_login_uses_refresh_token_grant_and_form_body` | POST `/oauth/token`, form-urlencoded body has all three OAuth fields; no token value in output |
| `oauth_authenticated_requests_use_bearer_prefix` | `Authorization: Bearer <token>` (NOT bare) for OAuth flow |
| `login_picks_oauth_when_both_oauth_and_legacy_present` | OAuth precedence — never falls through to legacy when OAuth is fully configured |
| `oauth_login_http_error_is_sanitized` | 400 → exit-clean, no traceback |
| `oauth_unconfigured_short_circuits_without_http` | Partial OAuth (no refresh_token) makes zero HTTP calls |
| `capabilities_includes_trade_scope_and_safety_gate` | All five new capability keys present + correct values |
| `cli_config_works_without_credentials` | `--config --json` runs without ANY env, output sanitized |
| `cli_config_with_partial_creds_lists_missing_fields` | `missing_fields` populated; client_secret value never appears |
| `cli_config_runs_before_unconfigured_short_circuit` | Dispatch order is correct |

### Tasty `.env.example` block (extended)

`.env.example` now contains the full TASTY_* block (~55 lines) with
explanatory comments grouping into:
- environment selector (`TASTY_ENV` / `TASTY_BASE_URL` / `TASTY_ACCOUNT_NUMBER`)
- OAuth refresh credentials (`TASTY_CLIENT_ID` / `TASTY_CLIENT_SECRET`
  / `TASTY_REDIRECT_URI` / `TASTY_REFRESH_TOKEN` / `TASTY_SCOPES`)
- legacy fallback (`TASTY_USERNAME` / `TASTY_PASSWORD`)
- safety gates (`TASTY_ALLOW_TRADE_SCOPE` / `TASTY_ENABLE_ORDER_SUBMISSION`)
- transport (`TASTY_USE_DXLINK` / `TASTY_TIMEOUT_SECONDS` / `TASTY_VERIFY_SSL`)

### What this round still does NOT do

- **No production `TastytradeQuoteProvider`**. The probe is still the
  only Tasty-aware code, still isolated from the scanner.
- **No DXLink WebSocket connection**. Only `/api-quote-tokens`.
- **No order paths of any kind** — `submit_*` raise `SafetyGateError`,
  no `/dry-run`, no `--dry-run-vertical` flag yet.
- **No ZS API chain quotes**. ZS stays structure-only by design.
- **No scanner wiring**. `MockQuoteProvider` is still the source of
  truth for bid/ask in scoring.

### Next step

1. Dan re-runs `python -m scripts.probe_tastytrade --config` against his
   live `.env`. The output will tell him whether the OAuth bootstrap is
   done (`refresh_token` present) or whether the one-time interactive
   authorization step still needs to happen.
2. If the OAuth bootstrap isn't done, capture the refresh_token via the
   Tasty dev-UI authorization-code dance (one-time manual step), then
   drop it in `.env`.
3. Then `python -m scripts.probe_tastytrade --capabilities --symbol SPX
   --json | Out-File phase3_capabilities.json` — the capability matrix
   decides what Phase 4 looks like (`TastytradeQuoteProvider` shape,
   DXLink integration scope, etc.).

---

## 2026-06-01 (Phase 3.1) — root auto-resolution + capability quote probe + missing_fields fix

**Driver — Dan's live probe results against production**:

- OAuth auth: ✅ success
- `--accounts`: ✅ returns 2 accounts (safely redacted to `****1234` etc.)
- `--chain --symbol SPX`: ✅ returns both SPX (monthlies) AND SPXW (weeklies + 0DTE)
- `--quotes --symbol SPX --expiry 2026-06-01 --strikes 7550,7570,7600 --right C` → **`quote_count: 0`**
- `--quotes --symbol SPXW --expiry 2026-06-01 --strikes 7550,7570,7600 --right C` → **`quote_count: 6`**
- Execution: still blocked by `TASTY_ENABLE_ORDER_SUBMISSION=false`,
  `execution_blocked_by_safety_gate=true`, `probe_exposes_submit_path=false`

**Conclusion**: Tasty is viable as the quote provider — the only thing
missing was that the probe blindly stuffed `--symbol` into the OCC
symbol root, so `--symbol SPX` for a 0DTE produced `SPX  ...` OCC
symbols that match nothing (SPX has no 0DTE — those are all SPXW).

### Fix: `resolve_root_for(underlying, expiry)`

New method on `TastyProbeClient` that walks the chain payload, builds
a `{root → [expirations]}` map, and picks the right root. Rules:

1. **Direct match** — caller said `--symbol SPXW` AND the chain confirms
   the expiry is in SPXW. Source: `direct_match`. No second chain lookup.
2. **Auto-resolve** — caller said `--symbol SPX`, the chain has both
   SPX and SPXW roots, and the expiry is in one of them. Source:
   `auto_chain`. **SPXW preferred** when both list the same date.
3. **Unresolved** — expiry doesn't appear under any root. Returns a
   sanitized error with `available_roots` + `sample_expirations_by_root`
   (first 8 per root) so the user can see what they SHOULD have asked
   for. No traceback. No silent guess.

### Fix: `get_option_quotes_for_strikes(...)` high-level method

New convenience method that:
1. Auto-resolves (via `resolve_root_for`) OR honors explicit
   `root_symbol=` kwarg.
2. Builds OCC symbols against the resolved root via the new
   `_build_occ_option_symbol(root, expiry, strike, right)` helper
   (extracted from `scripts/probe_tastytrade.py` so module + CLI share it).
3. Calls `get_option_quotes(...)` with the right symbols.
4. Annotates the response with `requested_underlying_symbol`,
   `resolved_root_symbol`, `root_resolution_source`, `available_roots`.

The low-level `get_option_quotes(equity_option_symbols)` is unchanged —
power users who already have OCC symbols still hit it directly.

### CLI additions

- `--root-symbol SPX|SPXW|RUT|NDX|XSP` — explicit root override.
  When supplied, skips the chain lookup entirely (faster, deterministic).
  When omitted, the probe auto-resolves.
- `--capability-expiry YYYY-MM-DD`, `--capability-strikes K1,K2,...`,
  `--capability-right C|P` — when ALL THREE are supplied to
  `--capabilities`, the probe runs a real quote probe and reports
  `has_quotes: true|false` with `quote_probe_count`,
  `quote_probe_resolved_root_symbol`, `quote_probe_root_resolution_source`,
  `quote_probe_http_status`. Default behavior (no quote-probe args) is
  the legacy `has_quotes: 'unknown_via_capabilities_use_quotes_subcmd'`.

### Cosmetic fix: `--config` no longer reports legacy as missing under OAuth

`TastyProbeConfig.missing_fields()` was a flat list — both auth modes'
missing fields merged. So Dan's OAuth-complete `.env` still reported
`TASTY_USERNAME` and `TASTY_PASSWORD` as "missing." Phase 3.1 returns:

```jsonc
{
  "oauth_missing_fields":  [],                   // empty when OAuth complete
  "legacy_missing_fields": ["TASTY_USERNAME", "TASTY_PASSWORD"],
  "usable_auth_modes":     ["oauth"],            // empty when neither complete
  "fully_configured":      true,
}
```

`config_summary()` exposes:

- `missing_fields` (top-level) — empty when ANY mode is complete;
  otherwise the SHORTER of the two missing lists (so the user sees
  which mode they're closer to completing).
- `oauth_missing_fields` + `legacy_missing_fields` — always present
  for full diagnostic visibility.
- `usable_auth_modes` — new top-level key.

### 23 new tests in `tests/test_phase3p1_root_resolution.py`

| Category | Tests |
|---|---|
| `resolve_root_for` | SPX-daily→SPXW, SPX-monthly→SPX, direct-match-SPXW, unresolved-expiry-clean-error, chain-unavailable-clean-error |
| `get_option_quotes_for_strikes` | auto-resolve SPX→SPXW for 0DTE (Dan's actual failure mode), explicit root override, unresolved-expiry-sanitized, output schema has all required keys |
| `capabilities_summary` | optional quote-probe args set `has_quotes=True` + `quote_probe_*` keys; legacy behavior preserved when args omitted |
| `missing_fields` | OAuth-complete suppresses legacy at top-level; legacy-complete suppresses OAuth at top-level; partial-OAuth shows shorter list at top-level + per-mode breakdowns |
| CLI | `--root-symbol` flows through to OCC symbol on the wire; auto-resolve works without `--root-symbol`; `--capability-{expiry,strikes,right}` triggers real quote probe |
| Safety gate | Phase 3 safety guarantees unchanged after Phase 3.1 — `submit_*` still raise `SafetyGateError`, `execution_blocked_by_safety_gate` still True |
| OCC builder | parametrized math, rejects bad inputs |

Plus 2 legacy tests in `test_phase3_tasty_probe.py` updated for the new
`missing_fields` shape (cleared their assertion-on-flat-list to match
the per-mode dict).

Total: **165/165 passing** (was 142, +23 new). Ruff clean.

### Also added to `.gitignore`

```
# Phase 3 probe — user-generated capability matrix dumps
phase*_capabilities.json
phase*_*.json
tasty_probe_*.json
```

So Dan's `phase3_tasty_capabilities.json` (and any future probe output)
doesn't surface as untracked in `git status`.

### Still NOT done

- No production `TastytradeQuoteProvider` — still deferred until Dan
  reviews the Phase 3.1 capability matrix.
- No DXLink WebSocket — token-only check via `/api-quote-tokens` still
  the only DXLink-aware code.
- No scanner wiring — `MockQuoteProvider` is still the scanner's only
  quote source.
- No order submission paths — `submit_order` / `submit_complex_order`
  still raise `SafetyGateError`.
- No `--dry-run-vertical` flag — `/orders/dry-run` is documented as
  safe but stays behind a future explicit opt-in.

### Next step

1. Dan re-runs the failed quote command:
   ```powershell
   python -m scripts.probe_tastytrade --quotes --symbol SPX `
       --expiry 2026-06-01 --strikes 7550,7570,7600,7605 --right C
   ```
   Expected output: `resolved_root_symbol: SPXW`,
   `root_resolution_source: auto_chain`, `quote_count: 4`.

2. Dan runs the full capability matrix with the quote probe:
   ```powershell
   python -m scripts.probe_tastytrade --capabilities --symbol SPX `
       --capability-expiry 2026-06-01 `
       --capability-strikes 7550,7570,7600 `
       --capability-right C --json | Out-File phase3p1_capabilities.json
   ```

3. If `quote_probe_count > 0` AND `has_dxlink` is True → Phase 4 is
   "design the TastytradeQuoteProvider class shape." If `has_dxlink`
   is False but REST quotes work → Phase 4 is "REST-only first, DXLink
   later" — slower polling but ships sooner.

4. Validate quote freshness during RTH (the after-hours probe may
   return EOD-stale values that look fine but aren't actionable).

---

## Phase 4 — `TastytradeQuoteProvider` (live REST quotes)

Phase 3.1 capability run on Dan's account confirmed everything VW needs:
`has_auth=true, has_accounts=true, has_chain=true, has_quotes=true,
chain_supports_spxw=true, chain_has_0dte_today=true,
quote_probe_count=2, quote_probe_resolved_root_symbol=SPXW,
quote_probe_root_resolution_source=auto_chain,
quote_probe_http_status=200, has_streaming_token=false,
has_dxlink=false, trade_scope_present=true,
order_submission_enabled=false, execution_blocked_by_safety_gate=true,
probe_exposes_submit_path=false`.

So Phase 4 is **REST-only first, DXLink deferred** — slower polling but
ships immediately. Tasty is treated strictly as a quote provider. ZS API
remains structure-only.

### What landed

1. **`src/providers/quotes/tastytrade_provider.py`** —
   `TastytradeQuoteProvider`. Composes `TastyProbeClient` for auth + REST
   + root resolution; implements the full `QuoteProvider` Protocol;
   builds OCC symbols for BOTH C+P sides of each `required_strike`;
   applies `QuoteValidation` per quote; wraps in `OptionChainSnapshot`
   with `resolved_root_symbol` + `root_resolution_source` so downstream
   code can audit the SPX→SPXW pick. No order paths even defined.

2. **`src/providers/quotes/types.py`** — added optional `validation_passed`
   + `validation_rejection_reason` fields on `OptionQuote`; optional
   `resolved_root_symbol` + `root_resolution_source` fields on
   `OptionChainSnapshot`; new `QuoteValidation` frozen dataclass with a
   `.validate(quote, now=None) -> (bool, reason | None)` method enforcing
   crossed / zero-bid / spread-abs / spread-pct / stale-age checks.

3. **`src/providers/quotes/factory.py`** — `build_quote_provider()` with
   precedence `--quote-provider` CLI → `QUOTE_PROVIDER` env → YAML →
   `"mock"`. Raises `TastytradeConfigurationError` on Tasty misconfig
   when `fallback_on_misconfig=False` (the scanner's strict path); the
   Streamlit cockpit passes `fallback_on_misconfig=True` so the UI
   never blocks on bad creds.

4. **`scripts/run_scanner.py`** — added `--quote-provider {mock,null,tastytrade}`
   CLI; replaced the hardcoded `MockQuoteProvider()` with the factory;
   surfaced `quote_provider`, `quote_chain_root`, `quote_ts` in the
   scan-tick log; added new `ranked_candidates.csv` columns —
   `quote_provider`, `quote_timestamp`, `quote_age_seconds`,
   `quote_chain_root`, `quote_root_resolution_source`,
   `{short,long}_validation_passed`, `{short,long}_rejection_reason`,
   `quote_validation_passed` (overall AND, None when both legs
   unvalidated), `quote_rejection_reason` (concat).

5. **`src/app/streamlit_main.py`** — sidebar quote-provider selector;
   `root=…` chip in Provider status panel; per-candidate `quote ✓/✗`
   column on the table; per-leg validation metrics inside each
   candidate's expander.

6. **`config/providers.yaml`** — `quotes` section now env-driven
   (`active: "${QUOTE_PROVIDER}"`, `default_if_unset: mock`); added
   `mock` to implementations; rewrote tasty params to point at the
   real `TASTY_*` env vars (not invented `TASTY_OAUTH_*` names).

7. **`.env.example`** — added `QUOTE_PROVIDER=mock` + the five
   `TASTY_QUOTE_*` validation knobs with conservative defaults
   (10s max age, 50% max pct, $5 max abs, reject zero-bid, reject
   crossed).

8. **`tests/test_phase4_tastytrade_provider.py`** — 33 tests covering
   `QuoteValidation` thresholds, `validation_from_env` parsing, factory
   precedence + misconfig handling + null + unknown fallback,
   `TastytradeConfigurationError` raises on missing creds, provider
   exposes NO order methods, `get_option_chain` happy path via
   monkey-patched probe (no real HTTP), failed-quote-kept-with-reason,
   auth/root failure paths, status reports safety-gate-off, and
   `_candidate_row` emits the new columns. Also a smoke check that
   `scripts.run_scanner --help` accepts `--quote-provider`.

### Boundary (NOT in Phase 4)

- No order submission. No order preview / dry-run. No order tickets.
- No DXLink WebSocket (REST polling only; `has_dxlink=false` confirmed).
- No snapshot worker.
- No historical Tasty storage.
- No whole-chain pulls — `get_option_chain()` requires
  `request.required_strikes`. Returns `None` and logs a warning
  otherwise.
- ZS API remains structure-only.
- Mock stays the default. Existing scanner with mock provider unchanged.

### Validation results

- `198 passed in 8.36s` (full pytest suite, including the 33 new Phase 4
  tests).
- `ruff check .` → `All checks passed!`
- `python -m scripts.run_scanner --quote-provider mock --dry-run` →
  ran end-to-end; log line shows `quote_provider=mock quote_root=-`;
  no regression vs. previous scanner behavior.

---

## 2026-06-01 (Phase 4.1) — audit metadata cleanup + target-DTE plumbing

**Live Tasty result that triggered Phase 4.1**: with `TastytradeQuoteProvider`
wired (Phase 4), one tick produced two candidates:

- `CALL_CREDIT 7610/7615 credit 0.95 score 0.6013` — *selected*, but the
  score only edged threshold by 0.0013. Weak components included
  `bid_ask_quality=0.00` despite the validator passing both legs (the abs-
  dollar 0.20 cap on `_bid_ask_quality_score` clipped a slightly-wider quote
  to 0).
- `PUT_CREDIT 7575/7570 credit 2.20 score 0.8259` — would have been selected,
  but the planned-stop-risk filter rejected it ($1400 > $1000 cap).

Conclusion: provider works, risk guard works, audit metadata needs cleanup
before adding selector modes. Phase 4.1 is observability + plumbing only —
no scoring weight changes, no execution.

### What landed (additive only — no existing schema changed)

**1) `Candidate` (in `src/strategies/base.py`)** — three new optional fields:
   - `score_edge` (signed `score - threshold`)
   - `score_edge_passed` (`score_edge >= MIN_SCORE_EDGE`)
   - `marginal_score` (`score >= threshold` AND `score_edge < MIN_SCORE_EDGE`)

   Decision branches in `VerticalWingV1.select()` are UNTOUCHED — observability
   only. Phase 5 will widen `RejectionType` to include `marginal_edge`.

**2) `Candidate.meta` extras stamped by candidate-builders and risk filters**:
   - `spread_bid`, `spread_ask`, `spread_mid`, `spread_width`,
     `spread_width_pct_of_mid`, `worst_leg_bid_ask_abs`,
     `worst_leg_bid_ask_pct_of_mid`
   - `risk_rejections{}` — keyed by `'planned_loss_cap' | 'theoretical_loss_cap'`
     with sub-fields `type, risk_dollars, cap_dollars, stop_variant, contracts,
     passed, reason`
   - Scalar mirrors: `risk_rejection_type`, `planned_stop_risk_dollars`,
     `planned_stop_risk_cap_dollars`, `planned_stop_risk_passed`,
     `theoretical_loss_dollars`, `theoretical_loss_cap_dollars`,
     `theoretical_loss_passed`

   The human-readable `c.rejection_reasons: list[str]` is UNTOUCHED — Phase 4.1
   is additive.

**3) New `src/selector/readiness.py`** — pure `compute_readiness(c, *, session,
   threshold, min_score_edge, target_dte, available_expiries, today_et, ...)`.
   Returns a FLAT dict suitable for `row.update(...)` with:
   - bucketed quote quality (`good | acceptable | poor | wide | invalid | unknown`)
   - four per-bucket `candidate_passes_*` flags + composite
     `selector_eligible_base`
   - `selector_blockers` list ("score_below_threshold", "score_below_min_edge",
     "risk_rejected:planned_loss_cap", "quote_invalid:...", "trade_filter:...")
   - `target_dte`, `selected_expiry`, `candidate_dte`, `expiry_selection_reason`
   - `planned_stop_risk_pct` (planned_stop_risk_dollars / session.starting_balance)

   Called from BOTH `_candidate_row` (scanner) AND the Streamlit per-candidate
   expander, so the two reflect the same view.

**4) New `src/utils/expiry.py`** — pure module with `pick_target_expiry(...)`,
   `is_trading_day`, `next_trading_day`, `add_trading_days`,
   `us_market_holidays`. Hardcoded NYSE holiday list for 2025-2027. **REVIEW
   ANNUALLY** in Nov-Dec — update the year-cap in `_SUPPORTED_YEARS` AND extend
   the hardcoded dict.

**5) Scanner CLI flags + plumbing (`scripts/run_scanner.py`)**:
   - `--target-dte 0|1|2` (env `TARGET_DTE`, default 0)
   - `--dte-mode calendar_days|trading_days` (env `DTE_MODE`, default trading_days)
   - `--allow-after-hours-roll` (env `ALLOW_AFTER_HOURS_EXPIRY_ROLL`, default false)
   - `--print-candidates` — per-candidate audit blocks to stdout, grouped
     Identity / Risk / Score / Quote / Selector. NEVER prints tokens / Authorization
     headers / credentials (tested).
   - YAML: `scanner.expiry: {target_dte, dte_mode, allow_after_hours_roll,
     after_hours_cutoff_et}`.
   - Precedence: CLI > env > YAML > default.
   - Decision-log `snapshot_summary` gains `target_dte`, `dte_mode`,
     `selected_expiry`, `expiry_selection_source`, `expiry_selection_reason`,
     `expiry_root_symbol`, `expiry_days_out`, `available_expiries_count`, plus an
     `expiry_override: {from, to, source, reason, root_hint,
     structure_expiry_matches_quote_expiry}` block when the chosen expiry
     differs from `structure.expiry`.

**6) `tasty_probe.validate_root_hint(underlying, root_hint, expiry)`** — pure
   READ of the cached chain summary. Returns `ok=True` when the hint is a real
   root in the chain AND the expiry is listed under it; `ok=False` with reason
   `root_not_in_chain` / `expiry_not_in_root` / `chain_unavailable` plus a
   fallback root suggestion. Wired into `get_option_quotes_for_strikes(...)`
   when an explicit `root_symbol` is supplied:
   - Default (lax) mode: an invalid hint AUTO-FALLS-BACK to the resolver's pick
     with `root_resolution_source='auto_chain_after_hint_mismatch'`.
   - `STRICT_ROOT_HINT=true` env: hard-fails with
     `root_resolution_source='explicit_invalid'` and no fallback.
   - `chain_unavailable` (transient network/auth failure) PRESERVES the
     explicit hint — Phase 3.1 back-compat.

**7) CSV — 22 new columns APPENDED at end of `_DEFAULT_RANKED_FIELDS`**.
   Existing column indices preserved byte-for-byte. New columns: `score_edge,
   score_edge_passed, marginal_score, spread_bid, spread_ask, spread_mid,
   spread_width_pct_of_mid, worst_leg_bid_ask_abs, worst_leg_bid_ask_pct_of_mid,
   quote_quality_bucket, quote_quality_reason, risk_rejection_type,
   planned_stop_risk_dollars, planned_stop_risk_cap_dollars,
   planned_stop_risk_pct, planned_stop_risk_passed,
   theoretical_loss_cap_dollars, theoretical_loss_passed, risk_rejection_reason,
   candidate_passes_score_threshold, candidate_passes_score_edge,
   candidate_passes_trade_filters, candidate_passes_risk_filters,
   candidate_passes_quote_filters, candidate_is_marginal,
   selector_eligible_base, selector_blockers, selector_readiness_note,
   target_dte, selected_expiry, candidate_dte, expiry_selection_reason`.

**8) Streamlit** — per-candidate expander gains a "Selector readiness" 4-metric
   row (Score edge / Quote bucket / Risk type / Eligible) + blockers list. Main
   candidate table gains three columns (`edge`, `bucket`, `risk_type`).

**9) `.env.example`** — adds `TARGET_DTE=0`, `DTE_MODE=trading_days`,
   `ALLOW_AFTER_HOURS_EXPIRY_ROLL=false`, `MIN_SCORE_EDGE=0.02`,
   `STRICT_ROOT_HINT=false`. Defaults match today's behavior — no operator
   action required to upgrade.

### Phase 4.1 verification

- **Pytest**: `272 passed in 8.98s` (was 198, +74 new).
- **Ruff**: `All checks passed!`
- **Mock smoke**: `python -m scripts.run_scanner --quote-provider mock
  --structure-provider zerosigma_api --dry-run` runs cleanly with target_dte=0;
  `--print-candidates` smoke confirms audit blocks have all five groups + new
  fields; no token / Authorization / credential leaks in stdout (tested).

### Bug surfaced for Phase 4.2

The `_bid_ask_quality_score` abs-dollar cap (default 0.20) clipped legitimate
SPX wings at 0.0 under live Tasty quotes. Phase 4.1 documents this and adds
the `quote_quality_bucket` so the legible bucket name shows up even when the
old scorer clips. Phase 4.2 should switch the cap to a relative (% of mid)
threshold — that's a calibration change, deliberately out of Phase 4.1 scope.

### Flag for Phase 5

The `RejectionType` literal should be widened to include `marginal_edge` so
the existing `c.rejection_type` field can carry it without abuse of the
`score_below_threshold` value. Phase 4.1 keeps it as a Candidate-side flag
only (`marginal_score: bool | None`) to avoid touching the literal.

### Flag for annual review

`src/utils/expiry.py.us_market_holidays(year)` hardcodes 2025-2027 NYSE
holidays. Update the dict + `_SUPPORTED_YEARS` annually each Nov-Dec; tests
will fail loudly if `pick_target_expiry` is called for a year outside the
supported range.

---

## 2026-06-02 — Phase 4.2 (quote-scoring recalibration / strict DTE / clock skew)

Motivation: the hardcoded ABSOLUTE bid/ask cap (default $0.20) made valid Tasty
quotes score `bid_ask_quality=0.00`. Live 4.1 tick: CALL_CREDIT 7610/7615,
worst leg $0.20 wide on a ~$3.10 mid (= 6.45% of mid) -> old scorer clipped to
0.0 and the candidate fell below the 0.60 threshold, while the
`quote_quality_bucket` (then on absolute-$ bins) read a contradictory label.
**The selector should wait until the quote-quality score is relative-aware** —
a 6.45%-of-mid market is a perfectly tradeable spread, not a 0.0.

Three surgical changes (NOTHING else in scoring/weights/threshold/risk-caps
touched; no execution):

1. **Relative `bid_ask_quality`** — new pure module `src/utils/quote_quality.py`
   (stdlib-only, neutral path so both `vertical_wing/candidates.py` and
   `src/selector/readiness.py` import it without tripping `test_no_vw_leak`;
   the module never contains the `vertical_wing` substring). pct-of-mid
   cutoffs (good<=3% -> 1.0; <=7% -> 0.8..0.6; <=15% -> 0.5..0.2; >15% -> 0.0;
   None/neg -> 0.0; crossed/missing leg -> 0.0/`invalid`). The SAME cutoffs
   drive the score AND `quote_quality_bucket`, so they can't disagree again.
   `candidates.py` STAMPS score + mode + reason + bucket + bucket-reason into
   `Candidate.meta`; `readiness.py` PREFERS the stamped bucket and falls back
   to the shared helper for fixtures. The `quote_quality_bucket` semantics
   MIGRATED from absolute-$ bins to pct-of-mid bins (deliberate). Legacy
   `absolute` mode retained as a knob (`BID_ASK_QUALITY_MODE`,
   `BID_ASK_MAX_ABS_CAP`; set cap 0.20 for 4.1 parity — default cap is 1.00,
   NOT 0.20) and auto-used when a leg has no usable mid.

2. **Strict target-DTE** — `--strict-target-dte` / `STRICT_TARGET_DTE` /
   `scanner.expiry.strict_target_dte` (default false). When `target_dte` can
   only be served by an expiry FALLBACK, strict forces NO_TRADE (blocker +
   esr `strict_target_dte_unavailable`). Enforced in `run_scanner.py` +
   `readiness.py` ONLY; `pick_target_expiry` is byte-identical (its 18 tests
   stay green — a None expiry there is silently rescued by `eff_expiry`, so a
   sentinel can't force NO_TRADE; strict is detected from
   `expiry_decision.source in {fallback, fallback_only_available}` after
   `strat.select`). NOTE: the orchestrator task item said to add strict
   handling in `expiry.py`, but the DESIGN said do NOT edit it — followed the
   design.

3. **Clock-skew clamp** — negative oldest-leg `quote_age_seconds` (quote ts
   ahead of scanner clock) clamps to 0.0 with `quote_clock_skew_detected` /
   `quote_clock_skew_seconds`. `QUOTE_AGE_CLOCK_SKEW_TOLERANCE_SECONDS`
   (default 2.0) labels magnitude only; both within- and beyond-tolerance
   negatives clamp to 0.0. None stays None. `QuoteValidation.validate` is
   untouched -> positive-age staleness rejection byte-identical.

### Design deviation recorded (mock data)

The design's `documented_choices` claimed the mock's selected wings score
relative 1.0 and `_mock_data.py` needs NO change — that premise was WRONG (it
analyzed the SHORT anchor legs, not the WORST/long legs). The selected
CALL_CREDIT long leg is 5820 (`c_mid=0.50`); a flat $0.10 spread there is 20%
of mid -> 0.0/`wide`, which dropped the mock CALL_CREDIT below 0.60 and broke
the smoke invariant. Sanctioned (constraint #7) minimal fix: tightened
`bid_ask_width` 0.10 -> 0.02 on the 4 legs (5780/5785/5815/5820) of the two
default-selected spreads. All mids/volumes/OI and every other strike's width
UNCHANGED. Mock now trades CALL_CREDIT again.

### New CSV/JSONL/audit fields

Six CSV columns APPENDED at the tail (indices preserved): `bid_ask_quality_mode`,
`bid_ask_quality_reason`, `quote_clock_skew_detected`, `quote_clock_skew_seconds`,
`strict_target_dte`, `strict_target_dte_passed`. JSONL auto-rides via meta.
`bid_ask_quality` SCORE reuses the existing column; `quote_quality_bucket/reason`
+ `worst_leg_*` reuse existing columns (no duplicates).

### Tests touched / added

- `test_phase4p1_readiness.py::TestQuoteQualityBucket` — rewrote the 4 band
  cases to set `worst_leg_bid_ask_pct_of_mid` (good 0.01 / acceptable 0.07 /
  poor 0.12 / wide 0.20); validator-fail still `invalid`, no-data still
  `unknown` (now keyed on pct). Added an abs-only -> `unknown` guard.
- `test_phase4p1_live_replay.py` — the live CALL_CREDIT's worst leg is 15.38%
  of mid -> bucket now `wide` (was `acceptable` under abs); decision branch
  unchanged.
- `test_phase4p1_csv_columns.py` — added `PHASE_4P2_APPENDED`; index-preserve
  loop allows both 4.1+4.2 tuples; new tail-order test; `quote_quality_bucket`
  spot-check updated to the pct value.
- NEW `test_phase4p2_quote_quality.py` (module endpoints, bucket+score
  agreement, candidates.py stamping, absolute legacy, crossed-leg floor).
- NEW `test_phase4p2_strict_and_skew.py` (strict off/on/exact-match via the
  scanner harness + `--help`; clock-skew clamp on `_candidate_row`).

### Absolute-mode parity caveat (operators)

`BID_ASK_MAX_ABS_CAP` defaults to 1.00, NOT the legacy 0.20. Selecting
`BID_ASK_QUALITY_MODE=absolute` will NOT reproduce Phase 4.1 behavior unless
`BID_ASK_MAX_ABS_CAP=0.20` is set explicitly.

### Streamlit / UI-parity gap

`strict_target_dte` is intentionally NOT threaded into the inline Streamlit
`compute_readiness` (the new strict params are keyword-only with defaults, so
the inline caller works unchanged). The candidate table gains `b/a mode` +
`bucket_reason` columns; the per-candidate expander gains bid_ask_quality
mode/reason tiles, clock-skew tiles, and a strict-status caption. Wiring strict
into the inline preview is a Phase 5 nicety.

---

## 2026-06-02 — Phase 4.2.1: scanner pre-fetch quote-request guard

**Bug (live, premarket):**
`run_scanner --structure-provider zerosigma_api --quote-provider tastytrade --target-dte 1 --print-candidates`
aborted with:
```
TastytradeQuoteProvider.get_option_chain: no required_strikes in QuoteRequest — production provider does not pull whole chains
QuoteProvider returned no chain for SPX @ 2026-06-02 (target_dte=1, src=fallback) — aborting tick.
```

**Root cause:** during a premarket / public-only ZS read the structure carries
no anchors, so `_collect_required_strikes` returns `[]`. The scanner still
called `quote_provider.get_option_chain(...)` with an empty `required_strikes`.
`TastytradeQuoteProvider` correctly refuses whole-chain pulls (returns `None`),
and the scanner treated that `None` as a hard failure (`return 3`). Same for
`--target-dte 2 --strict-target-dte`. Strict-DTE was also enforced *after*
`select()` — i.e. *after* Tasty had already been called.

**Fix (`scripts/run_scanner.py` only):** added a pre-fetch guard right after the
`QuoteRequest` is built and BEFORE `get_option_chain`. Two conditions
short-circuit to a clean NO_TRADE without calling the provider:
  1. `strict_unavailable` (strict mode + a fallback expiry source) →
     `quote_request_skipped_reason=strict_target_dte_unavailable`;
  2. empty `required_strikes` →
     `quote_request_skipped_reason=no_required_strikes`.
New helper `_emit_skipped_quote_no_trade(...)` logs a WARNING
("quote request skipped: no required strikes available"), writes the usual
`decision_log.jsonl` (NO_TRADE + rich machine-readable `snapshot_summary`:
`required_strikes_available`, `missing_required_quote_strikes`,
`selector_blockers`, `target_dte`, `selected_expiry`, `candidate_dte`,
`expiry_selection_reason`, ...) and a header-only `ranked_candidates.csv`, and
`--print-candidates` prints a `QUOTE REQUEST SKIPPED` block. The dead
post-select strict override was removed (strict is now enforced pre-fetch); the
genuine-failure `None`-chain path (real strikes requested, provider still fails)
still `return 3`.

**Boundary unchanged:** Tasty still refuses whole-chain pulls — the scanner just
never sends an unservable request. No execution, no order preview, no scoring
changes, no risk-cap changes. Mock behavior only changes under the same
missing-strikes condition (premarket no-anchor → clean skip vs the old
synthesize-then-zero-candidates; both NO_TRADE).

**Tests:** new `tests/test_phase4p2_skip_quote_fetch.py` (5 tests, recording
fake Tasty-like provider — empty strikes → provider NOT called; strict → NOT
called; normal → called with strikes; no whole-chain pull ever; mock also skips).
Full suite **318 passed**, ruff clean. Mock smoke confirmed the clean skip path.

---

## 2026-06-02 — Phase 5: configurable daily trade selector (SELECTION ONLY)

Added a daily trade selector that picks AT MOST ONE candidate (configurable via
`MAX_TRADES_PER_DAY`, default 1) from the candidates a strategy already
generated/scored/filtered. **No execution, no orders, no preview, no change to
candidate generation / quote fetching / risk filters / scoring.**

**New module:** `src/selector/daily_selector.py` — PURE (no I/O, no network, no
`vertical_wing` import → `test_no_vw_leak` stays green). Operates on candidate
ROW dicts (the same dicts the CSV writer uses, already carrying Phase 4.1
readiness fields) + a `SelectorConfig`; `gamma_regime` passed as context.

**Nine modes:** score_best_valid (default), best_credit_valid, closest_wing_valid,
farthest_wing_valid, call_credit_only, put_credit_only, lowest_breach_risk_valid
(transparent distance/risk/credit components; partial when planned_stop_risk_pct
missing — no crash), regime_aligned_valid (positive/neutral → best eligible;
negative → blocked; missing gamma_regime → insufficient_regime_data), no_trade.

**Eligibility gate** (shared): never selects rejected / selector_eligible_base=false
/ filter-failing candidates; REQUIRE_QUOTE_VALIDATION, REQUIRE_SCORE_EDGE, side
filters (both off → no_sides_allowed), MIN/MAX score/credit/distance. Conflict
detection: an unbreakable tie at the selection boundary → NO_TRADE when
NO_TRADE_ON_SELECTOR_CONFLICT (default). NO_TRADE_ON_SELECTOR_CONFLICT,
selector_conflict_detected surfaced.

**Scanner wiring (`scripts/run_scanner.py`):** selector runs ONCE per tick over
the union of all strategies' rows (so MAX_TRADES_PER_DAY caps the tick); decision
logs + candidate prints are deferred until after selection. Preserves the
strategy decision as `pre_selector_decision`; adds `post_selector_decision` +
`selected_trade`. 13 CSV columns APPENDED at the tail (no reorder). Decision log
gains `selector_result` (per-candidate metadata + after-selector pick) +
pre/post_selector_decision. `--print-candidates` adds a `--- daily selector ---`
block per candidate + a tick-level `=== DAILY SELECTOR ===` summary. The
pre-fetch skip path stamps selector defaults (selected_trade=False,
selector_no_trade_reason=quote_request_skipped:*).

**Config:** `config/scanner.yaml → scanner.selector` + `.env.example`
(DAILY_TRADE_SELECTOR, MAX_TRADES_PER_DAY, ALLOW_*, REQUIRE_*, MIN/MAX_SELECTOR_*,
LOWEST_BREACH_RISK_*_WEIGHT). CLI: --daily-selector, --max-trades-per-day,
--allow/--no-allow-{call,put}-credit, --require-score-edge, --min-selector-{score,credit}.
Streamlit: selector-mode dropdown + `selected` column + selection caption.

**Tests:** `tests/test_phase5_daily_selector.py` (26 pure unit tests — every mode,
tie-breakers, eligibility gates, filters, conflict, regime, no_trade, invariants,
no-vw-leak guard) + `tests/test_phase5_scanner_selector.py` (5 scanner-harness
tests — CSV columns, ≤1 selected, decision-log metadata, print section, both-sides
-disabled, --help). `tests/test_phase4p1_csv_columns.py` extended additively for
the Phase 5 tail columns.

**Process note:** the workflow approach failed twice on Phase 5 (a trivial `${ENV}`
template-literal typo, then a recurring StructuredOutput formatting crash on the
big synthesis agent after ~2h). Pivoted to direct implementation — the spec was
fully prescriptive, so the discover/design synthesis added fragility without
value. Full suite **351 passed**, ruff clean.

**Next:** strategy-config persistence / forward runner (Phase 5.x) — NOT execution.

---

## 2026-06-02 — Phase 6: strategy config persistence + run profiles

Save named, versioned run-profiles and run the scanner from one instead of long
CLI strings. **Config/persistence only — no execution, no orders, no forward loop.**

**New:** `src/config/strategy_profiles.py` (`StrategyProfile` dataclass +
`validate_profile_dict` → clean error strings + `load_profile_file` (never raises)
+ deterministic `profile_hash`). `scripts/manage_profiles.py`
(`--list/--show/--validate/--validate-all/--copy/--create-template`, `--force`).
`profiles/` with 4 committed examples (all `enabled: false`, `stub`+`mock`, no
secrets): score_best_1dte, call_only_1dte, best_credit_1dte, no_trade.

**Scanner `--profile <id|path>`:** applies profile values as defaults.
**Precedence: CLI > profile > env > YAML/default** — pre-filling profile values
onto `args` (for CLI-backed knobs) + injecting into the SelectorConfig build (for
the non-CLI selector knobs), so CLI flags still win.

**Flag rename:** `--profile` now = strategy run-profile; the former risk flag is
`--risk-profile`. Back-compat: a `--profile` value matching a known risk-profile
name is treated as the risk profile (logged). Risk precedence: `--risk-profile`
> run_profile.risk_profile > back-compat > YAML active. (No test used the old
`--profile`, so the rename is safe; README updated.)

**profile_hash:** sha256 of profile content EXCLUDING `created_at`/`updated_at`/
`profile_path` (so cosmetic re-saves don't churn it; config changes do). Stamped
+ `profile_id/name/version/path/loaded` + `config_source_summary` into
`ranked_candidates.csv` (7 columns APPENDED at the tail), the `decision_log.jsonl`
snapshot (+ `pre/post_selector_decision` already there), and scan logs — so a
future backtest/forward run can prove which exact profile produced a signal.

**Streamlit:** read-only run-profile dropdown that prefills the daily-selector
default (full control prefill deferred to Phase 6.1).

**Tests:** `tests/test_phase6_profiles.py` (schema/validation/enum/type/secrets,
hash determinism + timestamp-exclusion, example-profile validity/safety, CLI
list/validate-all/show/copy/create-template + overwrite-guard) and
`tests/test_phase6_scanner_profile.py` (profile loads values, CLI overrides
profile, profile metadata in CSV+JSONL, unknown profile → clean rc=5, risk
back-compat, no-profile default unchanged). `test_phase4p1_csv_columns.py`
extended additively for the 7 Phase 6 tail columns. Full suite **376 passed**,
ruff clean.

**Process note:** workflows failed twice on Phase 5 (this codebase) on the
StructuredOutput crash; Phase 5 + 6 were done via direct implementation, which is
more reliable for prescriptive specs. Phase 6 also avoided a non-obvious
`--profile` flag collision a workflow agent might have missed.

**Next:** forward runner / start-stop local paper monitoring (Phase 6.x) — NOT execution.

---

## 2026-06-02 — Phase 7: forward runner / local paper monitoring

`scripts/run_forward.py` repeatedly runs the EXISTING scanner from a saved Phase 6
run-profile and records a local ledger. **Monitoring + ledger only — NO execution,
NO broker/paper orders, NO order preview, NO position reconciliation, NO backtest
adapter.** Manifest stamps `no_execution=true`,
`execution_mode=disabled_local_monitoring`.

**No duplicated scanner logic:** one-line refactor `run_scanner.main(argv=None)` +
`parser.parse_args(argv)` (existing CLI byte-identical, argv=None → sys.argv). The
forward runner calls `run_scanner.main([...])` IN-PROCESS pointed at a per-run
scanner output dir, then reads that tick's `latest/decision_log.jsonl` (new lines
since pre-count) + `latest/ranked_candidates.csv` (overwritten each tick) to
capture the decision + selected rows. Same code path → consistent output fields.

**Commands:** `--profile`, `--interval-seconds` (default 60; 0=no sleep),
`--max-ticks N`, `--once`, `--dry-run`, `--market-hours-only`, `--output-dir`,
+ safe `--quote-provider`/`--structure-provider` passthrough.

**Ledger** (`outputs/forward/runs/{run_id}/`, gitignored): run_manifest.json,
tick_log.jsonl, signal_log.jsonl, selected_trades.csv, no_trade_log.jsonl,
heartbeat.json, scanner/. Mirror at `outputs/forward/latest/`.

**Choices documented:** (1) `--dry-run` writes a `dry_run` manifest + heartbeat but
does NOT scan (no tick/signal logs). (2) Ledger duplicate protection: identity =
profile_hash+symbol+selected_expiry+side+short_strike+long_strike+target_dte+
trade_date — a repeat within a run is NOT re-appended to signal_log/selected_trades;
the tick is still logged with `duplicate_selected_signal=true`. (3) Market-hours:
simple RTH 09:30–16:00 ET weekday rule (no holiday calendar this phase); default off
for deterministic tests; skipped ticks log `status=skipped_market_closed` and don't
scan. (4) Ctrl+C → manifest `stopped`, exit 0. Scanner nonzero rc or a tick
exception → tick `status=error`, manifest `error`, exit nonzero, run stops. Unknown
profile → clean exit 2, no run folder.

**Streamlit:** read-only "Forward runs (monitoring)" section (latest manifest +
heartbeat + per-run counts). No start/stop buttons (Phase 7.1). `.gitignore` now
ignores `outputs/forward/*` (+ `.gitkeep`).

**Tests:** `tests/test_phase7_forward_runner.py` (13) — _is_rth pure, dry-run
manifest-only, --once ledger, --max-ticks 2, selected→signal_log+csv,
no-trade→no_trade_log, duplicate-not-appended, heartbeat each tick,
market-hours skip, unknown-profile exit 2, scanner-failure→error, KeyboardInterrupt
→stopped, and a no-execution-surface grep guard. Full suite **389 passed**, ruff clean.

**Process note:** direct implementation again (workflows failed twice on Phase 5);
the spec was prescriptive and the in-process `main(argv)` seam kept the patch small
+ behavior-preserving.

**Next:** dashboard start/stop controls and/or a backtest adapter (Phase 7.x) — NOT execution.

---

## 2026-06-02 — Phase 8: forward run review + control UX (read-only)

Made forward runs easy to inspect. **Review/control UX only — NO execution, NO
broker/paper orders, NO order preview, NO process management.** The Streamlit panel
never launches or stops a run; it only inspects ledgers + shows copy-only commands.

**New:** `src/forward/__init__.py` + `src/forward/review.py` — pure, read-only
inspection (discover_runs newest-first; load_latest_{pointer,manifest,heartbeat};
resolve_run_dir with the `latest` alias; load_{tick,signal,no_trade}_log +
load_selected_trades; summarize_run with run_id/profile/status/timestamps +
tick_count/signal_count/duplicate_signal_count/no_trade_count/error_count +
latest_{tick_time,decision,selected_trade,no_trade_reason,heartbeat_status} +
selected_trade_summaries). Every reader tolerates missing/empty/corrupt files →
no traceback. `scripts/review_forward.py` CLI:
`--list/--latest/--run/--signals/--no-trades/--ticks/--export-summary`,
`--limit N`, `--forward-root`; missing run → clean exit 1 + helpful message.

**Phase 7 polish (non-breaking):** `_persist_manifest` now also writes
`outputs/forward/latest/latest_run_pointer.json` (run_id + run_path + status) so
`latest`/`--latest` resolve robustly. No ledger schema change.

**Streamlit:** the Phase 7 "Forward runs (monitoring)" section now uses the review
module: run-selector dropdown over discovered runs, latest-heartbeat caption, the 5
count metrics, tables of selected signals / no-trade reasons / latest 25 ticks, the
run-folder path, and a COPY-ONLY `st.code` block of the run_forward + review_forward
commands. No start/stop buttons, no subprocess (a test greps `subprocess`/order
terms out of both review files).

**Tests:** `tests/test_phase8_forward_review.py` (16) — discover sorted newest-first
+ empty-root clean, latest heartbeat/manifest/pointer, `latest` alias, summarize
with signals+dupes (1 signal / 1 dup across 2 ticks), summarize no-trade, summarize
missing-optional-files clean, selected_trades.csv load, unknown-run→None, CLI
list/latest/run/signals/missing→1/export-json, no-execution-surface grep,
streamlit parses + imports review. Most seed REAL ledgers via the Phase 7 runner
into a tmp --output-dir. Full suite **405 passed**, ruff clean.

**Next (pick one):** (A) dashboard start/stop process control, or (B) a
historical/backtest adapter replaying snapped data through the same scanner path —
still NOT live broker execution.

---

## 2026-06-02 — Phase 9A: local forward-runner process control (start/stop/status)

Picked option (A) from Phase 8's fork, but as a **CLI** (not Streamlit buttons).
**LOCAL PROCESS CONTROL ONLY — NO execution, NO broker/paper orders, NO order
preview, NO account selection, NO position reconciliation, NO auto-execution, NO
snapshot workers, NO backtest storage.** Every control-state file stamps
`no_execution=true` + `execution_mode=disabled_local_monitoring`; a test greps
`submit_order/place_order/preview_order/create_order/broker./execute_trade` out of
both new files.

**New:** `src/forward/control.py` — process-state dir `outputs/forward/control/`
(`forward_runner.pid`, `control_state.json`, `stop_requested.json`, `logs/`).
- PID liveness is **non-destructive** and `psutil`-free: Windows ctypes
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION=0x1000)` + `GetExitCodeProcess`
  (`STILL_ACTIVE=259`); POSIX `os.kill(pid, 0)`. Wrapped in a monkeypatchable
  `_pid_alive` (and `_terminate_pid`) so tests never touch a real process.
  Deliberately NOT `os.kill(pid,0)` on Windows (that can deliver CTRL_C_EVENT).
- `status()` reconciles stored state vs the live probe: alive → `running`/active;
  dead + stored "running" → `stale` (NOT running); terminal states
  (completed/stopped/error) preserved; no state → `stopped`.
- `cleanup_stale()` removes pid/state/stop files **only** after confirming the PID
  is not alive — refuses loudly ("ALIVE") otherwise.
- `start()` loads the Phase 6 profile, refuses if a live runner is active, opens
  out/err logs, and `subprocess.Popen`s a DETACHED background runner using
  `sys.executable` (Win `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`, POSIX
  `start_new_session`); writes pid + `starting` state.
- `stop()` writes the stop sentinel + sets `stopping`; `--force` additionally
  `_terminate_pid`s **only** the stored PID, only if alive.

**New:** `scripts/control_forward.py` — `--forward-root` + subcommands
`status` / `command` (prints the safe run command, **never launches**) /
`start` / `stop [--force]` / `cleanup-stale`. Windows PowerShell, no admin.

**`run_forward.py` wiring (additive):** `--control-state-path` (writes live
`run_id`/`status`/`last_seen_at`/latest heartbeat+manifest paths/latest
decision+selected-trade into the shared control state) and `--stop-file` (checked
at the top of each tick → clean exit with manifest `status=stopped`). With neither
flag the runner is **byte-identical to Phase 7/8** (a test asserts no `control/`
dir is created).

**Streamlit:** "Forward runs (monitoring)" gains a READ-ONLY control block —
Runner/Active/PID metrics, a `stale` warning, and a copy-only `st.code` of the
`control_forward` commands. **No start/stop buttons; the UI never spawns/kills.**

**Tests:** `tests/test_phase9a_control.py` (17) — status no-state/alive/stale,
cleanup-stale removes-dead / refuses-alive, command prints-without-launch, start
creates pid+state+logs (mocked Popen, pid 4242) / refuses-when-active, stop writes
sentinel / force targets ONLY stored pid (`killed == [4242]`) / no-op when not
alive, run_forward exits-on-stop-file / updates-control-state / standalone-unchanged,
CLI start-requires-profile / status-runs, no-execution grep. Full suite
**422 passed**, ruff clean.

**Validated live:** `control_forward status` (no state → `stopped`), `command`
(prints argv, writes nothing), `start --once` → status later showed `completed`
with `run_id` filled by the child, `review_forward --latest` reads the run,
`cleanup-stale` clears files once the PID is dead.

**Next:** Phase 9B = historical / snapped-data (backtest) adapter — replay archived
snapshots through the same `run_scanner.main(argv)` path + Phase 7/8 ledger/review.
Live broker execution (manual-confirm → broker paper/live) stays deferred to
Phases 10–11.

---

## 2026-06-02 — Phase 9B: multi-strategy local paper trade lifecycle + P&L

Built the paper-trade lifecycle (TP/SL/EOD) + a multi-profile portfolio runner.
**LOCAL PAPER ACCOUNTING ONLY — NO broker orders, NO order preview, NO paper-broker
orders, NO live execution, NO historical backtest yet.** Every ledger stamps
`no_execution=true` + `execution_mode=local_paper_lifecycle_only`; a test greps
`submit_order/place_order/preview_order/create_order/broker./execute_trade/order_preview`
out of all 5 new files. (Renumber: 9B is now the paper lifecycle; snapped-data
backtest moved to Phase 10; Tasty execution readiness/live to Phase 11.)

**Plan-vs-actual notes (chose direct authoring for the coupled core):** the user
picked the "workflow" path, so Stage 1 was a 5-agent parallel **understand**
workflow (mapped src/paper, the signal schema, run_forward mechanics, review
patterns, config/clock/re-pricing). 4/5 agents returned clean; the
config-clock-reprice agent hit the StructuredOutput non-call failure again, so I
recovered that area with direct reads. Then I authored the tightly-coupled
trading-math core directly (drift-prone to parallelize) and reserved a workflow
for **verification**.

**New modules:**
- `src/paper/models.py` — `PaperTrade` (all spec fields, all defaulted for
  incremental build + tolerant `from_row` coercion) + `PaperLifecycleConfig`
  (`from_env` / `from_env_and_overrides`; TP 0.50, SL 1.50, EOD 15:55, limits).
- `src/paper/lifecycle.py` — pure engine: `make_trade_identity` (mirrors
  run_forward's `_signal_identity` order), `spread_quote_from_row`
  (mid=`short_mid−long_mid`, bid=`short_bid−long_ask`, ask=`short_ask−long_bid`),
  `find_repricing_row` (match by side+strikes+expiry), `open_trade_from_signal`,
  `can_open` (documented precedence: identity-dup → dup-strikes → multiple-per-
  profile → per-profile-max → total-max), `update_trade_mark` (MAE/MFE, ticks),
  `evaluate_exit` (TP→SL→EOD; EOD fires even with no quote), `close_trade`.
  **REUSES** `manual_tracker.{unrealized,realized}_pnl_dollars` +
  `spread_width_from_strikes` + `OPTION_MULTIPLIER` — no re-derived P&L.
- `src/paper/ledger.py` — portfolio paths/writers (open=snapshot, closed=snapshot,
  events=append jsonl), `compute_summary`, tolerant readers mirroring
  `forward/review.py`, `latest` alias resolution, and `reconcile_run`
  (local-only; `broker_position_reconciliation: "deferred"`).

**New scripts:**
- `scripts/run_portfolio_forward.py` — `--profiles A,B` / `--profiles-file` /
  `--interval-seconds` / `--max-ticks` / `--once` / `--market-hours-only` /
  `--output-dir` + lifecycle override flags. Runs the scanner once per profile per
  tick in-process (`OUTPUT_DIR=run_dir/scanner/{profile_id}`, reads back
  `latest/ranked_candidates.csv`), opens trades from `selected_trade=true` rows,
  re-prices + exit-checks every open trade each tick, writes all portfolio
  ledgers, reconciles in `finally`. Per-profile scanner errors are non-fatal
  (logged in profile_tick); Ctrl+C → `stopped` (exit 0).
- `scripts/review_portfolio_forward.py` — `--latest/--list/--run/--open/--closed/
  --events/--reconcile`, `--limit`, `--output-dir`; missing run → exit 1.

**Config:** `PAPER_*` block added to `.env.example`; example
`config/portfolio_profiles.yaml` (profiles list + lifecycle block). **Phase 6
profile schema UNCHANGED** (lifecycle config is env/CLI/portfolio-file only).
`.gitignore` now ignores `outputs/portfolio_forward/*` (+ `.gitkeep`).

**Streamlit:** read-only "Portfolio forward (paper lifecycle)" panel (heartbeat,
open/closed tables, P&L metrics, event log, reconciliation). No buttons.

**Tests:** `tests/test_phase9b_portfolio.py` (18) — open-from-signal, dup signal,
multi-profile tick, total/per-profile/dup-strike limits, TP/SL/EOD exits,
quote-unavailable-no-false-exit, ledger write/read, summary P&L, reconciliation
(issues + clean), runner `--once` files, runner `--max-ticks 2` updates an open
trade (`ticks_held==2`, dup-skipped≥1), review CLI commands, no-execution grep.
Engine/ledger tests use synthetic rows + explicit ET datetimes (wall-clock
independent); runner tests use `--no-exit-on-eod` so trades persist regardless of
run time. Full suite **440 passed**, ruff clean.

**Live-validated:** `run_portfolio_forward --once` on score_best + no_trade →
score_best opened a CALL_CREDIT 5815/5820 @0.60, EOD-closed same tick (ran at
16:09 ET ≥ 15:55) → realized 0.0; reconciliation OK; review CLI subcommands all
print + exit 0.

**Gotcha:** the no-execution grep tripped on my own docstrings ("never touches a
broker.") — reworded to "brokerage" (same Phase 9A fix). The EOD-at-wall-clock
behavior makes naive runner tests time-dependent → solved with `--no-exit-on-eod`
+ explicit-datetime engine tests.

**Next:** Phase 10 = historical / snapped-data backtest adapter (replay snapshots
through the scanner + this paper lifecycle). Phase 11 = Tastytrade execution
readiness / live (manual_confirm → broker_paper → live), still deferred.

---

## 2026-06-02 — Phase 9C: ZerσSigma Algo Cockpit UI refresh + Strategy Builder + safe controls

UI / profile-management only. NO trading-logic change, NO broker execution, NO
orders, NO order preview. Dan committed 9A+9B as `7574cfe` first, so 9C started on
a clean main. (Workflow path again: a 3-agent parallel **understand** workflow
mapped the 1153-line streamlit file + mined the Dashboard brand palette + pinned
the profile/control APIs; then I authored the coupled UI directly; then a
verification workflow.)

**Brand palette** (adapted from the Dashboard `assets/zerosigma.css`, values only —
no code copied): bg `#0b0f14`, panel `#141a22`, text `#e8e8e8`, muted `#a7b0bd`,
line `#232a33`, accent-green `#00E5A8`, blue `#2d6cff`, IBM Plex fonts, 14px cards,
subtle green glow on the active tab.

**New modules:**
- `src/app/ui_helpers.py` — PURE (stdlib only, no `import streamlit`): `BRAND`,
  `brand_css()` (targets stable selectors `.stApp` / `[data-testid="stMetric"]` /
  `.stTabs`), `pill`/`hero`/`brand_title`/`metric_card` (HTML strings, escaped),
  `fmt_money`/`fmt_num`/`dash`/`pnl_kind`.
- `src/app/profile_builder.py` — PURE, imports ONLY strategy_profiles (no cycle):
  `PROFILE_FIELDS` metadata (by section), `new_template_dict` / `load_dict_for_edit`
  / `clone_dict` / `build_profile_dict` (type-coerce form values onto a base) /
  `validate_dict` / `hash_for` / `save_profile` (overwrite guard + returns hash) /
  `list_summaries`.
- `src/app/control_ui.py` — guards over the Phase 9A `control` module:
  `status_view`, `can_start` (refuse if active/alive/transitional/stale),
  `start_runner` (status-check THEN control.start — refuses a 2nd runner),
  `stop_runner` (graceful; `force` only when asked), `cleanup`, `safe_command`.

**streamlit_main.py refactor:** sidebar selectors → a top `⚙ Controls & providers`
expander; shared computation (providers/structure/chain/candidate context) stays
ABOVE the tabs; each panel became a `render_*()` function (candidate/scoring/
decision render logic preserved VERBATIM) called inside `st.tabs([...6...])`. Hero
banner + `LOCAL · NO BROKER EXECUTION` pill. Forward Runner tab gained real
Start/Stop/Cleanup/Refresh buttons (force-stop behind an explicit checkbox).
Portfolio tab gained a realized-P&L `st.bar_chart`. Manual desk + EOD + session
debug + session-controls form all preserved, re-homed into tabs.

**Tests:** `tests/test_phase9c_cockpit.py` (20) — streamlit imports clean +
ast.parse; ui_helpers (css palette, pill/card/format, σ highlight); profile_builder
(template valid, build+coerce, reject execution/secret keys, **refuse overwrite
unless explicit**, save writes YAML + list_summaries sees it, reject invalid, clone
round-trip); control_ui (status_view states, can_start guard, **start refuses 2nd
live runner via mocked control.status**, happy-path calls control.start, requires
profile, **stop writes graceful stop force=False**, cleanup/safe_command); and a
no-execution grep over all 4 UI files. Full suite **460 passed**, ruff clean.

**Gotchas:** (1) removed the now-unused `forward_control` import (replaced by
`control_ui`). (2) a "No broker." caption tripped the no-exec grep → "No
brokerage." (same 9A/9B fix). (3) `control_ui.cleanup(())` bug — passed an empty
tuple as root → fixed to `cleanup()`. (4) re-homing sections into `render_*()`
functions relies on Streamlit running ALL tab bodies each rerun, so module-level
vars (structure/chain/quote_spot/session/paper_account) computed above the tabs are
visible to every panel.

**Live-validated:** `streamlit_main` imports + runs the full tabbed script headless
on stub/mock (bare-mode ScriptRunContext warnings only); `manage_profiles
--validate-all`, `control_forward status`, `review_forward --latest`,
`review_portfolio_forward --latest` all green.

**Next:** Phase 10 = historical / snapped-data backtest adapter. Phase 11 =
Tastytrade execution readiness / live, still deferred.

---

## 2026-06-02 — Phase 9D: cockpit UX polish + clearer operational workflow

UX / operational only. NO scanner / selector / quote / lifecycle / risk-cap
changes, NO broker execution. Committed 9C first (`4625f45`) so 9D is a clean diff.
(Workflow path: a 2-agent understand workflow confirmed exposure units + provider-
configured detection; then direct build; then verify workflow.)

**Key facts the understand workflow pinned:** `da_gex_signed` is ALREADY in
billions (stub=1.8) → format `4.18B`/`735M`; walls/floors/maxvol/ddoi_pin are plain
STRIKE prices; `gamma_regime` is `str|None` (derive from DA-GEX sign if None); spot
fallback = `chain.spot` → `structure.spot` (always present) → `spot_quote.last`,
with `0.0` treated as a missing sentinel. Provider "configured" is detectable via
env-var PRESENCE only (no secret values): `TASTY_CLIENT_ID/SECRET/REFRESH_TOKEN`
or `TASTY_USERNAME/PASSWORD`; `ZS_API_BASE_URL` + non-`none` `ZS_API_AUTH_MODE`.

**New module `src/app/cockpit_helpers.py` (pure, stdlib + read-only review/ledger):**
`fmt_exposure`/`fmt_strike`/`fmt_price`/`fmt_pct`/`fmt_money`/`fmt_count`,
`gamma_regime_badge`, `spot_with_source` (returns (val, source_badge)),
`tasty_configured`/`zs_configured`/`default_provider`/`provider_index`/
`provider_label`, `chain_unavailable_actions`, `STRICT_DTE_LABEL`/`strict_dte_help`,
`status_strip_cells`, `review_prompt`, `forward_export_files`/`portfolio_export_files`
(graceful when the run dir/files are missing).

**streamlit_main.py polish (targeted edits, render logic preserved):**
- provider selectboxes default to zerosigma_api/tastytrade WHEN configured (env
  presence) else sandbox; `format_func=provider_label` marks sandbox/live.
- top operational status strip above the tabs (7 metrics + NO BROKER EXECUTION pill).
- `render_market`: spot fallback w/ source badge; DA-GEX `fmt_exposure`; strikes
  `fmt_strike`; chain-unavailable reason + actions.
- Run Strategy panel: 👁 Preview scan once (start once) + ▶ Start / ■ Stop / 🧹
  Cleanup / 🔄 Refresh + `control_ui.safe_command` exact command + latest decision +
  open-paper P&L line.
- Logs: download_buttons for the latest forward/portfolio artifacts + Copy review
  prompt; graceful empty state.
- Portfolio: open-trades + unrealized first; empty state + no-run setup steps.
- Strategy Builder: "profiles are saved strategy recipes" explainer + basics-then-
  Advanced-expanders form (Advanced selector filters / expiry controls / risk
  fields / strategy params). Settings → "Session & Paper Settings" + explanation +
  advanced expanders.

**ui_helpers.py:** tighter CSS (metric padding 12→7px, value 22px→1.15rem, block
gaps, `.block-container` padding). **profile_builder.py:** additive advanced-group
metadata (`ADVANCED_FIELDS`, `is_advanced`, `ADVANCED_GROUPS`, `advanced_group_fields`,
`basic_fields`) + strict_target_dte label→"Require exact DTE match" with help.

**Tests:** `tests/test_phase9d_polish.py` (20) — formatting (4.181966→4.18B), spot
fallback, provider configured/default, strict-DTE label, advanced grouping, log
export missing+seeded, review prompt language, streamlit import, no-exec grep. Full
suite **480 passed**, ruff clean.

**Gotchas:** (1) `zip(strip_cols, strip_cells)` length mismatch (extra badge col) →
`strict=False`. (2) removed unused `forward_control` import already done in 9C.
Candidate table left functionally intact (reformatting every cell risks the dense
display logic the spec says not to touch).

**Next:** Phase 10 = historical / snapped-data backtest adapter. Phase 11 =
Tastytrade execution readiness / live, still deferred.

---

## 2026-06-02 — Phase 9E: Operator Mode + Zσ Strat Tester + first-class symbols

UX + symbol/profile wiring only. NO trading-logic / scanner / selector / quote /
lifecycle / risk changes, NO broker execution. Committed 9D first (`bc4aaf7`) so 9E
is a clean diff. (Workflow path: a 2-agent understand workflow traced symbol flow +
mapped the post-9D cockpit; then direct build; then verify workflow.)

**Architecture clarification (mid-phase):** ZerσSigma API = **exposure** engine only
(DA-GEX/VEX/DEX/CEX/TEX, gamma regime, walls/floors, MaxVol/DDOI); Tastytrade =
**market-data** engine (quotes/chain/bid-ask/mid/mark/volume/OI/contract metadata,
eventual order routing — not this phase). UI copy uses "Exposure source" /
"Market data source" (internal names + CLI flags unchanged).

**Symbol flow facts (understand workflow):** `profile.symbol` IS used by the scanner
(precedence `--symbol` > profile.symbol > cfg.scanner.symbols[0]=SPX); `run_forward`
/ `run_portfolio_forward` have NO `--symbol`, so the profile is the ONLY way to set
symbol — perfect for the save-to-profile approach. BUT: Sandbox (stub+mock) ignores
the symbol and returns SPX data (labels QQQ, prices SPX); Live (ZS+Tasty) is
symbol-specific but ZS exposure coverage may be missing even when Tasty serves
quotes. The UI is honest about all of this (symbol-health panel + sandbox caveat).

**New module `src/app/operator_mode.py` (pure, stdlib only):** Simple/Advanced copy
+ `DEFAULT_SIMPLE_MODE`; `tab_labels()` (🧪 Zσ Strat Tester / 💼 Paper Portfolio — no
"Forward Runner"); `side_preference_to_fields` + `selector_style_to_selector` +
`build_simple_fields` (selector style overrides side default; Observe forces
no_trade); `data_source_to_providers`/`providers_to_data_source` (Live→
zerosigma_api+tastytrade, Sandbox→stub+mock) + the corrected exposure/market-data
labels + engine-label helpers; `normalize_symbol` (uppercase, default SPX, arbitrary
OK); `symbol_health` (market_data_available vs exposures_available vs eligible +
reason) + the two unavailable-warnings; `friendly_log_label`.

**streamlit_main.py edits (render logic preserved):** app-level `simple_mode` toggle
(default ON); Controls expander → Live/Sandbox radio (Simple) or Exposure-source /
Market-data-source dropdowns (Advanced) + first-class ticker text input → `SYMBOL`;
`render_symbol_health()` at the top of Live Cockpit; Strategy Builder Simple compact
form (maps to profile fields via operator_mode) vs the existing Advanced form
(shared `_show_result_and_save`); "Forward Runner" → **🧪 Zσ Strat Tester** (Preview
strategy / Start paper test / Stop test; commands under an expander; active profile /
symbol / data-source metrics); "Portfolio forward" → **Zσ Paper Portfolio**; logs use
`friendly_log_label` (raw filenames only in Advanced).

**Tests:** `tests/test_phase9e_operator.py` (15) — side/selector/data-source mappings,
`build_simple_fields` combine + observe-override, symbol normalization, arbitrary
symbol accepted + saved at the builder layer, symbol_health engine split, branded
tab labels (no "Forward Runner"), strict-DTE label replaced, friendly log labels,
streamlit clean import + `om.tab_labels()` used, no-exec grep. Full suite **495
passed**, ruff clean.

**Gotchas:** none new — `operator_mode` is pure; `streamlit_main` imports headless;
`build_profile_dict` only copies known profile fields, so the simple-form extras map
cleanly. Paper TP/SL/contracts/EOD are PAPER_* env (not profile fields) → shown
read-only in the Simple builder with a clear note, not fake-saved.

**Next:** Phase 10 = historical / snapped-data backtest adapter. Phase 11 =
Tastytrade execution readiness / live, still deferred.

---

## 2026-06-03 — Phase 9F: final operator pass + Zσ Strat Builder + Strategy Stats + Dashboard-style controls

UI / copy / layout only. NO scanner/strategy/selector/quote/lifecycle/risk changes;
no broker execution. Committed 9E first (44e086e) so 9F is a clean diff. (Workflow
path: a 2-agent understand workflow mined the Dashboard control CSS + mapped the
flat-file stats sources; then direct build; then a verify workflow.)

Header-first layout: branded ZerσSigma Algo Cockpit header moved to the TOP (above
the controls expander); Simple/Advanced toggle now sits in the header strip (not
clipped by Streamlit chrome); subtitle = "Scanner · Zσ Strat Builder · Zσ Strat
Tester · Paper Portfolio · Strategy Stats" (no "forward runner").

operator_mode.py additions (pure): HEADER_TITLE/HEADER_SUBTITLE; renamed tab_labels
(Zσ Strat Builder / Stats / Review); button_labels() + BTN_* constants;
active_profile_display ("No active profile selected"); runner_busy_message;
PRESET_DESCRIPTIONS + profile_description (4 committed profiles + generic fallback) +
profile_info_fields; is_sandbox + symbol_health_view (sandbox → "sandbox mock/stub/
eligible", live → real availability + reason). cockpit_helpers.py additions (read-
only flat files): eod_export_file, latest_run_stats, historical_stats,
common_no_trade_reasons, latest_best_candidate (graceful empties).

ui_helpers.brand_css Dashboard match: primary = green pill linear-gradient(135deg,
#00e5a8,#81ffd8)/#03130e radius 999px; secondary/danger = dark-outlined gradient;
disabled opacity .42; SELECTBOX pill (rgba(16,24,38,.96) + caret-color:transparent +
cursor:pointer) to kill the "text input cursor" feel. Streamlit limitation: select is
a baseweb component (not native <select>), so the caret/cursor are tamed, not fully
replaced; keyboard accessibility preserved.

streamlit_main edits (render logic preserved): header moved up; symbol-health uses
symbol_health_view (fixes the confusing "No ZerσSigma exposures and no Tasty market
data for SPX" while stub structure renders below — sandbox now reads sandbox);
Strategy Builder -> Zσ Strat Builder + preset info card + Create/Edit/Clone buttons;
Zσ Strat Tester button relabels + runner-busy warning + active-profile display;
Logs/Review -> Strategy Stats & Review (latest run + historical stats + downloads +
review prompt); manual desk "Record manual paper trade" + no-broker note; Settings
"Apply local session settings" + note.

Tests: tests/test_phase9f_polish.py (16) — subtitle no "forward runner", tab renames
(no Logs/Review), sandbox vs live symbol health, is_sandbox, preset + generic
descriptions, profile_info_fields, button labels, active-profile, runner-busy,
friendly EOD label, stats empties, Dashboard CSS classes (caret-color/primary pill/
disabled), streamlit header-first import, no-exec grep. Updated one 9E assertion
("Strategy Builder" -> "Strat Builder"). Full suite 511 passed, ruff clean.

Gotcha: manual-desk note "...any broker." tripped the 9C no-exec grep -> "brokerage".

Next: Phase 10 = historical / snapped-data backtest adapter. Phase 11 = Tastytrade
execution readiness / live, still deferred.

## 2026-06-03 — Phase 9G: dynamic-first preset stack + balanced selector + adjustable TP/SL

Backtest-derived presets, a NEW dynamic both-side selector, and TP/SL + dynamic-exit
profile metadata. Direct build (no workflow). NO change to scanner/quote/risk/paper
P&L math; no broker execution. Architecture language kept: ZerσSigma API = exposures/
structure engine; Tastytrade = market-data/quote engine.

DYNAMIC-FIRST framing (per the user's correction): dynamic side-selection presets are
the PRIMARY live presets; call-only presets are explicit CONTROLS to measure what
dynamic selection adds. Dropdown orders dynamic FIRST.

New selector `balanced_structure_premium_valid` (src/selector/daily_selector.py):
evaluates BOTH CALL_CREDIT + PUT_CREDIT among eligible/quote-valid/risk-valid rows and
picks the better side on a TRANSPARENT combined score — never highest-premium-only,
never farthest-distance-only. Components min-max normalized WITHIN the eligible set
(bounded [0,1], relative tradeoff, deterministic): premium_score, distance_safety_
score, structure_score, maxvol_gamma_alignment_score, quote_quality_score, existing_
candidate_score, planned_risk_penalty. total = Σ w·score − w_risk·risk. Default weights
struct=1.0, prem=0.75, dist=0.75, maxvol=0.75, quote=0.50, score=0.75, risk=0.50 (all
configurable on SelectorConfig). Two-pass: collect eligible → normalize → stamp
selector_score_components → rank (total > score > |distance|). Emits a human
explanation comparing the winner to the best OTHER-side runner-up ("Selected
CALL_CREDIT because it had stronger structure, acceptable credit, safer distance from
spot ... than the PUT_CREDIT alternative"), on SelectorResult.selector_explanation +
the winner row reason. Missing fields → neutral 0.5 (never dominate). ALLOWED_SELECTORS
= tuple(SELECTOR_MODES) so profiles using it auto-validate.

Profile schema (src/config/strategy_profiles.py): added OPTIONAL, backward-compatible
fields — preset_kind, side_policy, threshold_label, target_time, stop_loss_pct,
stop_loss_mode, take_profit_pct, take_profit_mode, dynamic_exit_enabled (bool, default
False), dynamic_exit_policy. New _OPT_STR_FIELDS validation loop + dynamic_exit_enabled
in _BOOL_FIELDS + the two pct in _OPT_FLOAT_FIELDS. template_profile_dict + summary_row
updated. from_dict ignores unknowns + fills defaults → the 4 legacy profiles still
validate (they just gain default Nones/False; profile_hash shifts but no test pins a
literal hash).

10 new presets (profiles/*.yaml), all SAFE: stub exposures + mock market data +
enabled:false (operator switches providers + enables in the cockpit to go live). 0DTE
SPX, lowercase ids, human display names.
  Dynamic core (primary): morning_5k_dynamic_tp75 (10:55–11:05, SL150, TP75),
    morning_2k_dynamic_no_tp (SL150, no TP), eod_5k_dynamic_sl150_no_tp (15:00–15:30
    target 15:15, SL150, no TP), eod_5k_dynamic_sl200_no_tp (SL200, no TP).
  Call-only controls: morning_5k_call_tp75_control, morning_2k_call_no_tp_control,
    eod_5k_call_sl150_no_tp_control, eod_5k_call_tp50_control (SL200, TP50).
  Regime: regime_put_credit_test (put_credit_only, calls disabled).
  Observe: observe_dynamic_5k (no_trade selector, both sides for scoring only).

WIRED vs DEFERRED (important — no faked behavior):
  WIRED now: balanced selector (full + tested); preset metadata; TP/SL + dynamic-exit
    fields saved + validated + shown in the info card and Simple-Mode controls;
    dynamic-first dropdown ordering + friendly labels; Tester UX cleanup.
  DEFERRED: per-profile TP/SL EXECUTION + dynamic exits in the paper lifecycle. The
    paper runner still reads PaperLifecycleConfig.from_env() (PAPER_* env). The UI is
    explicit: "Your profile's TP/SL is saved as metadata; the paper lifecycle applies
    the PAPER_* env values (per-profile wiring deferred)", and dynamic_exit_status()
    always reads "configured … not active yet" even when the flag is true. paper
    lifecycle math UNCHANGED.

operator_mode.py (pure) additions: balanced selector style ("Dynamic — balanced both
sides"); PRESET_DESCRIPTIONS for all 10; PRESET_ORDER + order_profiles_for_dropdown
(dynamic first) + preset_kind_badge + profile_dropdown_label; side_policy_display;
take_profit_display / stop_loss_display / dynamic_exit_status / entry_window_display /
threshold_display; profile_info_fields enriched to the full card (Profile, Profile ID,
Symbol, Strategy, Entry window, Target time, DTE, Threshold, Side policy, Selector
mode, TP, SL, Dynamic exits, Risk, Data source, Designed to test, Safety); friendly_
run_label (e.g. "Vertical Wing · Jun 2 · 10:31 PM") + strategy_display_name +
short_run_id + running_display. _fmt_started_at parses a GIVEN ISO ts (never reads the
clock) → deterministic.

profile_builder.py: new "Exit management" section + STOP_LOSS_PRESETS (150/200/custom)
+ TAKE_PROFIT_PRESETS (None/50/75/custom) + PRESET_KIND_OPTIONS; SL/TP pct are BASIC,
the modes + dynamic-exit + preset metadata are advanced groups ("Advanced exit
management", "Advanced preset metadata"). Advanced builder form auto-iterates these.

streamlit_main.py: shared _render_profile_info_card (Builder + Tester); Simple-Mode SL
(150/200/custom radio) + TP (None/50/75/custom radio) controls wired into the saved
profile; Tester cleanup — "Interval (s)"→"Scan every (seconds)" (+help "How often the
local paper tester checks for a new signal" + "Scan every: 60 seconds" caption); "Max
ticks (0=∞)" hidden in Simple, Advanced "Stop after scans"; "Active"→"Running: Yes/No";
"Run id" metric→"Latest test run" friendly label; PID hidden in Simple; full run id +
PID + interval moved into an "Advanced details" expander; dropdown uses friendly
labels + dynamic-first order + a "Selected profile" details card.

Tests: test_phase9g_presets.py (24), test_phase9g_balanced_selector.py (12),
test_phase9g_operator_ui.py (43). Updated test_phase5_daily_selector::test_all_modes_
are_known (+balanced) and one 9F info-card assertion (Side preference→Side policy /
"call only"). Full suite 590 passed, ruff clean.

Gotchas: ruff UP032 (.format → f-string in SelectorConfig.summary) + RUF046 (int(round
(x)) → round(x)). A cute walrus in a test import block was invalid syntax — rewrote
the import cleanly.

Next: Phase 10 = historical/snapped-data backtest adapter (will let these presets be
measured on archived data). Phase 11 = per-profile TP/SL + dynamic-exit lifecycle
wiring + Tastytrade execution readiness, still deferred.

## 2026-06-03 — Phase 9H / 10-prep: operator decision layer + 10K wings + primary/secondary gamma + backtest plan

Operator-cockpit cleanup + structure-display depth + Phase 10 prep. NO scanner/
selector/quote/lifecycle/risk MATH change; no broker execution. (9G was committed by
Dan as 2ffa705 before this turn — clean tree.)

Structure model (src/providers/structure/types.py): ExposureContext gained
put_ceiling_10k / call_floor_10k (+ _volume), gamma_primary / gamma_secondary — all
optional defaults (backward compatible; ExposureContext() still valid). ddoi_pin field
KEPT (Advanced/raw only now).

ZS mapper (zerosigma_api.py): 10K wings derived the SAME way as 2K/5K
(_highest/_lowest_strike_where at threshold 10000) from the subscription volume series;
gamma_primary/secondary mapped from gamma.cluster_primary / cluster_secondary (+ aliases
primary/secondary/_strike). Both tracked in `missing`. EXTRACTED a reusable
`build_snapshot_from_payload(snap_payload, vol_series, *, symbol, source)` method (the
post-fetch mapping moved out of get_snapshot, behavior-preserving — 53 provider tests
green) so the Phase 10 replay loader reuses the EXACT live mapping (no fork). Stub:
10K derives honestly to None (mock chain peaks ~5.5K volume; demonstrates the
"unavailable" path) + demo gamma clusters 5795/5825 for the sandbox.

DDOI: per the updated requirement, REMOVED from the prime Live Cockpit cards (it was
never wired — zerosigma_api sets ddoi_pin=None; "still not in the public payload"). It
now lives ONLY in the Advanced structure / raw diagnostics expander with the help text
"DDOI is a dealer-positioning pin/gravity reference. It is only shown when available
and relevant." Replaced in prime by Primary Gamma + Secondary Gamma.

Pure helpers (src/app/cockpit_helpers.py): wing_stack() (2K/5K/10K put ceilings + call
floors, nearest wing = min |dist|, primary wing = strongest available tier nearest
spot, signed distances); primary_secondary_gamma() (source ∈ payload_cluster /
derived_from_walls / unavailable — derivation ranks call_wall/put_wall/gamma_flip by
closeness to spot, deterministic; never invents); ddoi_advanced() + DDOI_HELP;
operator_decision_layer() → the 5-part summary (Structure Read / Trade Bias / Candidate
Risk / Best Eligible Setup / Why·Why Not), every part guarded so missing data reads
"unavailable", references primary/secondary gamma + nearest wing + regime; fmt_distance.
operator_mode.py: profile_category / group_profiles_by_category / profiles_in_category
(Primary live paper tests → Controls → Research/Observe → Legacy; Primary first) +
DEFAULT_SIMPLE_CATEGORY; run_profile_mismatch(selected, latest_run) → warning.

Live Cockpit (streamlit_main.py): new render_operator_decision() inserted ABOVE Market/
structure (tab_live order: symbol_health → operator_decision → provider_status → market
→ candidates). Best Eligible Setup uses a guarded read-only _compute_best_eligible()
(re-derives the top eligible candidate; any failure → None → honest "no eligible setup
surfaced" — does NOT change scanner/selector math). render_market reworked: prime cards
= Spot / Gamma regime / DA-GEX / MaxVol / Primary gamma / Secondary gamma (DDOI gone);
new Wing Stack section (put ceilings + call floors 2K/5K/10K + nearest/primary + signed
distance + a "10K requires upstream volume ≥10,000" note when absent); walls/flip/DDOI
moved into an "Advanced structure / raw diagnostics" expander.

Tester + Builder: Simple Mode now shows a "Profile group" radio (Primary first) that
filters the dropdown to one category; Advanced Mode exposes all. Tester gained a
"Latest completed test" section (profile/name/status from the forward manifest) + a
mismatch warning when the latest run's profile ≠ the selected profile ("Start a new
local paper test …"). Builder preset dropdown now uses friendly badge labels + grouping.

Backtest prep (Phase 10): docs/phase10_backtest_plan.md (discovery → file locations →
snapshot schema (raw payload OR {snapshot, exposure_series, symbol} bundle) →
wingonomics review → StructureSnapshot mapping via the shared method → quote/chain
availability + fallbacks (mock re-centered on snapshot spot) → same selector + same
paper lifecycle reuse → per-preset output comparison → the 7 backtest questions →
build order). Minimal SAFE scaffold: src/replay/ (snapshot_loader: map_payload_to_
snapshot / load_snapshot_record (raw+bundle) / load_snapshot_file / discover_snapshot_
files — pure, reuses build_snapshot_from_payload) + scripts/discover_replay_data.py
(read-only; reports 0 files today + documents the needed capture step). No scanner/
selector fork; no execution.

Tests: test_phase9h_structure.py (12) + test_phase9h_helpers.py (13) +
test_phase9h_ui.py (8) — 10K derivation, gamma mapping + aliases, stub honesty, shared
mapper + replay parity, wing stack tiers/nearest/primary, gamma source modes, DDOI
advanced-only + help, decision-layer parts/gamma-reference/unavailable/no-chain,
mismatch, grouping (primary first), prime cards have gamma not DDOI, operator panel above
market, no-exec scan. Full suite 623 passed, ruff clean.

WIRED vs DEFERRED: 10K wings + gamma clusters are WIRED (mapped + displayed + in
candidate-visible ExposureContext). 10K is only POPULATED when the subscription volume
series carries ≥10,000-volume strikes (sandbox/public → None, shown as "—" + note).
Backtest is PLAN + scaffold only (no capture step / no ReplayProvider yet).

Next: Phase 10 implementation (capture step + ReplayStructureProvider/ReplayQuoteProvider
+ run_backtest + per-preset comparison). Phase 11 = per-profile TP/SL + dynamic-exit
lifecycle wiring + Tastytrade execution readiness, still deferred.

## 2026-06-03 — Phase 9I: trader-first UI cleanup + live-test readiness + stats charts + backtest research

Made the cockpit feel like a trader cockpit, not a debug console. NO scanner/
selector/risk/paper-P&L MATH change; no broker execution. Used a 5-agent read-only
research WORKFLOW first to map every UI/data-source/stats/EOD surface + discover the
real backtest data on disk, then implemented directly (one coupled streamlit file →
sequential). 9H was uncommitted on entry (Dan committed 9G as 2ffa705) — expected
dirtiness, no divergence; built 9I on top.

Pure helpers (testable, no streamlit):
- operator_mode: resolve_run_source(app_ds, profile_struct, profile_quote, prefer) →
  {mismatch, winner, data_source(Live/Sandbox), exposure_label, market_data_label,
  providers, message} — NEVER silently mismatches; run_source_status (ready/warning/
  unavailable); data_source_short; RUN_SOURCE_APP/PROFILE; simple_mode_profile_ids
  (Main-only vs show-all). Category RELABEL: Primary live paper tests→**Main
  Strategies**, Controls→**Comparison Tests**, Research/Observe→**Research / Disabled**,
  Legacy→**Legacy / Archived** (updated 9H tests).
- cockpit_helpers: quote_chain_status(...) → {available, reason_code, simple_reason,
  advanced} mapping last_error patterns (auth_failed/chain_unresolved/quote_fetch_
  failed) + provider mock/null + config-fallback + structure_error → concise reason;
  never overclaims (unknown→"provider returned no usable chain"). Stats math from
  closed trades: equity_curve_from_closed_trades, drawdown_series, max_drawdown
  (+pct vs peak equity when starting_balance given), daily_pnl, pnl_by_profile,
  trade_outcome_counts, exit_reason_counts. EOD staleness: is_eod_stale (tz-safe) +
  eod_summary_status (eod file mtime vs forward manifest started_at).

control_ui: start_runner + safe_command gained quote_provider/structure_provider
passthrough (control.start/build_command already supported them — additive, no runner
behavior change) so the Tester can run a profile on the APP data source.

streamlit_main (items 1–9):
1. Data source: top Tester metric = **App data source**; a "Data source for this run"
   panel resolves App vs Profile (Data/Exposure/Market source + Status badge) + a
   mismatch WARNING; Advanced toggle (app vs profile wins), Simple Mode = app wins
   (explicit caption). Resolved providers passed as overrides to Preview/Start when app
   wins.
2. Quote diagnostics: render_market chain-None now says WHY (quote_chain_status) —
   concise in Simple, raw provider state under an Advanced expander.
3. Advanced structure / raw diagnostics (incl. DDOI) gated behind `if not simple_mode`
   — gone from the normal flow; DDOI never in prime.
4. Profile dropdown: Simple Mode shows ONLY Main Strategies + a "Show comparison and
   legacy profiles" checkbox (Tester + Builder); Advanced = all.
5. Terminal commands: `python -m scripts…` blocks gated to Advanced (Tester expander +
   Portfolio else-branch); Simple Mode gets buttons (Refresh portfolio / Reconcile /
   plus Generate EOD + Refresh stats on the Stats page).
6. Manual Paper Desk hidden in Simple Mode (Advanced only; renamed "Manual local paper
   entry" + "Manual entries are local records only").
7. Stats charts (Streamlit-native): equity curve (line), drawdown (area), daily P&L
   (bar), P&L-by-profile + exit-reason tables, selected-signals-over-runs (bar), and
   metrics (closed trades / win rate / realized P&L / **max drawdown** + %). Graceful
   "More stats will appear…" empty state.
8. EOD: prominent "Generate / Refresh EOD summary" button + last-generated timestamp +
   ⚠stale/✅up-to-date badge + a SAFE one-shot auto-generate (guarded by
   `_eod_autogen_done`; only when stale + has run data + runner not live — no background
   loop, no broker, local outputs only).
9. Latest-run clarity: Stats "Latest run" shows the friendly label (friendly_run_label);
   full run id only in Advanced.

Backtest research (item 10): scripts/discover_backtest_sources.py — HOME/env-derived
roots (ZSA_TRADING_ROOT / --root / ~/Dropbox/Trading; ZSA_BACKTEST_DIRS), NO hardcoded
username. Live run found: **145 SPX_RAW_*.csv** (Strike + CALL/PUT Volume present →
usable), 78 SPX_1DTE, WINGONOMICS outputs (daily_stats + latest.json), the wingonomics
script (reference, do-not-modify), Greek_Data_MASTER.xlsm + DeltaDrift PDFs (not
usable). Key finding: **wingonomics.py detects 10K wings by the SAME volume-threshold
logic our mapper uses** (call_floor = min strike where CALL Volume ≥ 10000) → replaying
the raw CSVs should reproduce its wing levels; wingonomics_daily_stats.csv is the
validation ground-truth. docs/phase10_backtest_plan.md §13 documents the sources, the
CSV→exposure_series ETL, the quote-from-CSV-bid/ask path, env path approach, and the
concrete build order. We CONSUME wingonomics; never run/modify it.

Tests: test_phase9i_helpers.py (16) + test_phase9i_ui.py (10) + test_phase9i_discovery.py
(6). Updated test_phase9h_helpers (category relabel) + test_phase9h_ui (grouping wiring
→ simple_mode_profile_ids + checkbox). Full suite 654 passed, ruff clean,
manage_profiles 14/14, streamlit import OK, discovery script runs read-only.

Next: Phase 10 implementation (capture_exposures ETL over SPX_RAW CSVs →
ReplayStructure/QuoteProvider → run_backtest per preset → review_backtest comparison vs
wingonomics). Phase 11 = per-profile TP/SL + dynamic-exit lifecycle wiring + Tastytrade
execution readiness, still deferred.

## 2026-06-03 — Phase 9J: true Wing Dominance Score (WDS) + Phase 10A SPX_RAW loader

9H/9I cockpit was already committed (69e1662, "Phase 9H-9I: refine trader cockpit and
prepare backtesting"; clean tree, in sync with origin) — Step A satisfied. Built 9J on
top. NO scanner/selector/risk/paper-P&L MATH change; no broker execution.

WHY: the operator read over-emphasised the nearest 2K wing. Dan's wing logic is true
**Wing Dominance Score** — a 10K wing (W1) is only strong if it DOMINATES the adjacent
strike (W2):  WSR = W2_vol / W1_vol ;  WDS = 1 - WSR  (higher = cleaner). This is NOT a
generic tier-strength (10K=1.0/5K=0.7) — that was explicitly rejected.

Source-of-truth review (Step B): read the real
`…/0 - Strategies_Backtesting/wingonomics/scripts/wingonomics.py`. Its `compute_wing`
selects CW1 = min strike where CALL Volume ≥ 10000 and PW1 = max strike where PUT Volume
≥ 10000 — EXACTLY our mapper. But wingonomics does NOT compute WDS/WSR/adjacent-strike at
all → WDS is a NEW concept. Implemented per Dan's spec with documented assumptions:
  • W2 = the next AVAILABLE strike in the series (CALL → one LOWER than CW1; PUT → one
    HIGHER than PW1); no fixed 5/10-pt assumption.
  • WSR uses SIDE-SPECIFIC volume (CALL vol for calls; PUT vol for puts).
  • No clipping: WSR may exceed 1 → WDS < 0 → Tier 4 (very weak).
  • Tiers: ≥0.75 T1, 0.50–0.75 T2, 0.30–0.50 T3, <0.30 T4.
  • Missing W1 or W2 volume → true WDS UNAVAILABLE (never invented; no proxy used).
  • Dominant side = higher WDS, tie-broken by larger W1 volume (NOT nearest distance).

Structure model (Step D): ExposureContext gained call_floor_10k_w2_strike/volume (one
LOWER) + put_ceiling_10k_w2_strike/volume (one HIGHER) — optional/defaulted (backward
compatible). ZS mapper derives W2 from the SAME call/put volume series via a new
`_adjacent_strike(strikes, w1, direction)` (next available neighbour). Stub stays 10K=None
(mock peaks ~5.5K) → WDS unavailable in sandbox (honest).

WDS helper (Step C, cockpit_helpers): `wds_tier`, `wds_pct`, `compute_wds(w1s,w1v,w2s,w2v)`
(→ wsr/wds/wds_pct/tier/source/reason; source 'true'|'unavailable'), `wing_dominance(ex,
spot)` → all required fields (call_*, put_*, dominant_wing_*, nearest_wing_*, wds_source,
wds_reason). Example reasons match the spec: dominant → "…adjacent strike volume is only
30% of W1"; weak → "…weak because adjacent strike volume is 82% of W1".

Operator read (Step E) + Wing Stack (Step F): `operator_decision_layer` now takes `wds`
and leads Structure Read with the dominant 10K WDS wing AS THE PRIMARY STRUCTURE, then
frames the nearest 2K/5K wing as "immediate breach risk but not the primary structure";
Candidate Risk names the dominant 10K (not the tier-based primary_wing) as primary
structure. render_market Wing Stack gained a "Dominant wing (WDS)" block (W1 vol, W2
strike+vol, WSR, WDS %, Tier) + the dominant-vs-nearest caption. When W2 missing: "10K
wing exists, but true WDS is unavailable because adjacent W2 volume is missing."

Selector (Step G): DISPLAY-ONLY this pass. Candidate rows don't carry W2 volume yet, so
WDS is NOT fed into the selector — selector weighting by WDS is DEFERRED to Phase 10/11
(documented). `balanced_structure_premium_valid` math untouched.

Phase 10A (Step J): src/replay/spx_raw_loader.py reads `SPX_RAW_*.csv` (RTH filter,
group by timestamp, build {strikes,calls,puts,spot}) and maps ONE timestamp →
StructureSnapshot via the SHARED `map_payload_to_snapshot` (no fork) → 2K/5K/10K wings +
W2 derive identically. `scripts/backtest_spx_raw.py` (HOME/env paths, no hardcoded user)
prints available dates + a sample mapped structure incl. WDS. VALIDATED on REAL data:
145 dates (2025-10-31 → 2026-06-03); midday 2026-06-03 12:45 → call_floor_10k 7560 / put_
ceiling_10k 7600, CALL W1=15734 vs W2=8264, dominant = PUT_CEILING 10K WDS 60% Tier 2.
Loader-only — no runner, no execution.

Tests: test_phase9j_wds.py (17) + test_phase9j_ui.py (3) + test_phase9j_backtest.py (6).
Updated test_phase9h_ui (Wing Stack "Primary wing"→"Dominant wing (WDS)"). Full suite 678
passed, ruff clean, manage_profiles 14/14, streamlit import OK.

Next: Phase 10B — ReplayStructureProvider/ReplayQuoteProvider + run_backtest per preset
(reusing run_scanner.main + paper lifecycle) + per-preset comparison vs
wingonomics_daily_stats.csv. WDS→selector weighting still deferred (Phase 10/11).

---

## 2026-06-03 (Phase 10A) — local historical backtester: data mapping + multi-symbol + wing corridor

DATA MAPPING + LOADER SCAFFOLD only. No strategy/selector fork, no broker, no order
preview, no Tastytrade / ZS-live calls for history. Backtesting reuses the SAME live
path: saved `SPX/SPY/QQQ_RAW_*.csv` → StructureSnapshot/OptionChainSnapshot (shared
`map_payload_to_snapshot` + Phase 9J WDS) → same profile → same selector shapes →
repo-local outputs.

New pure package `src/backtesting/`:
- `schemas.py` — `SymbolConfig` (spot col + 2K/5K/10K thresholds + note); `symbol_config`
  for SPX/SPY/QQQ (SPY/QQQ thresholds flagged PROVISIONAL — calibration is 10B); required
  structure/pricing/optional cols; `ENTRY_WINDOWS` (Morning 11:00 ±5; EOD 15:00/15:15/15:30
  ±15/±30); RTH bounds; DTE_0/DTE_1; `<SYM>[/_1DTE]` folder + `<SYM>_RAW[_1DTE]_*.csv` glob.
- `raw_snapshot_loader.py` — `trading_root` = `--trading-root` → `ZSA_TRADING_ROOT` →
  `~/Dropbox/Trading` (NO hardcoded username); `parse_timestamp` handles ISO-offset /
  spaced / compact and normalises tz-aware → America/New_York wall time; RTH filter;
  symbol-aware `<SYM>_Spot`; 0DTE glob excludes the 1DTE files. Pure helpers
  `available_dates`/`file_for_date`/`available_timestamps`.
- `mappers.py` — `select_snapshot` (closest |delta| in window; ties prefer at-or-after via
  `abs(delta)*2 + (delta<0)`), `map_structure`/`map_option_chain` (shared mapper,
  `source="backtest_raw"`), `vertical_credit` (mid-to-mid), `corridor_wds`
  (= ch.wing_dominance), repo-local `output_base/latest_dir/run_dir` (honor
  OUTPUT_DIR/DATA_DIR else `<repo>/outputs`, ALWAYS under `…/backtests/`).

MANDATORY wing-corridor rule (Dan's structure logic): a wing structure is ACTIVE only
when `CW1 (call_floor_10k) < spot < PW1 (put_ceiling_10k)`. A call floor ABOVE spot is
NOT an active floor. Encoded once in pure code, reused live + backtest:
- `cockpit_helpers.wing_corridor_status(spot,cw1,pw1)` → `{corridor_valid,cw1,pw1,spot,
  reason,side_read}` (missing CW1/PW1 → invalid; CW1≥spot → "CW1 is not below spot.";
  PW1≤spot → "PW1 is not above spot."; CW1<spot<PW1 → valid).
- `wing_dominance` now GATES the dominant wing on the corridor (`wds_active =
  corridor_valid and raw_dom`). Corridor not formed → raw WDS is CONTEXT-ONLY
  (`raw_wds_source="true"`, `dominant_wing_side="unavailable"`, `wds_source="unavailable"`),
  never active structure.
- Operator read leads with "Structure status: Active corridor" / "Inactive — corridor not
  formed"; the nearest 2K/5K wing is immediate breach risk, NOT primary structure, when
  invalid. Wing Stack UI shows CW1/Spot/PW1 + ✅/⛔ corridor; active-dominant only when
  valid. Selector gets `corridor_valid`+`wds_active`; NO positive structure credit when
  invalid (display-only/deferred). Scan CSV records corridor_valid/cw1/pw1/reason/raw_wds/
  active_wds per snapshot.

3 read-only CLIs (HOME/env paths, no hardcoded user):
- `discover_backtest_sources.py --symbols SPX SPY QQQ --include-1dte` — per symbol×DTE:
  folder/count/date-range/sample/spot-col/structure+pricing+optional cols/usability. 1DTE
  is DISCOVERY-ONLY (full 1DTE logic is future).
- `backtest_dry_run.py` — one entry snapshot → spot/2K-5K-10K wings/corridor/WDS/gamma/
  candidate spreads/priceable.
- `backtest_scan_dates.py` — one row per entry snapshot over a date range →
  `outputs/backtests/latest/scan_<SYM>_<DTE>_<HHMM>.csv` + a timestamped run dir.

Outputs ONLY under `outputs/backtests/{latest,runs/<stamp>_<label>}` — NEVER into the raw
`TOS Data` folders. VALIDATED on real data: SPX 145×0DTE (2025-10-31 → 2026-06-03) +
78×1DTE; SPY/QQQ 66 each. SPX 2026-06-03 @ 11:00:15 → corridor ACTIVE (7575 < 7578.55 <
7600), dominant PUT_CEILING 10K WDS 58% T2, both spreads priceable (162 quotes). The
corridor bug is real in the data: an SPX midday tick with call_floor_10k 7560 > spot
7557.74 now correctly reads "Inactive — corridor not formed" instead of "dominant
CALL_FLOOR 10K".

Tests: test_phase10a_corridor.py (12) + test_phase10a_backtest.py (15). Updated
test_phase9j_wds/ui/backtest + test_phase9h_ui to corridor-aware copy + valid-corridor
fixtures (the old call-floor-above-spot fixtures were structurally INVALID and now read
inactive — the correct behavior). One scan test made hermetic (clears OUTPUT_DIR/DATA_DIR
so the repo-local default is deterministic; output_base honors those envs by design).
Ruff: added N812 to ignore (deliberate `mappers as M` / `raw_snapshot_loader as L`
aliases). Full suite 700 passed, ruff clean, manage_profiles 14/14, discovery + dry-run
CLIs verified on REAL data, streamlit import OK.

Next: Phase 10B — ReplayStructureProvider/ReplayQuoteProvider over these mapped snapshots
+ run_backtest per preset (reuse run_scanner.main + paper lifecycle) + per-preset P&L /
drawdown / win-rate vs wingonomics_daily_stats.csv; SPY/QQQ wing calibration; full 1DTE.

---

## 2026-06-04 (Phase 10B) — historical replay runner: run profiles across snapshot dates

REPLAY + SIMULATION only. No strategy/selector fork, no broker, no order preview, no
Tastytrade, no ZerσSigma live API. Drives each mapped (structure, chain) through the SAME
live path, then simulates the exit historically.

Reused live path (NO fork): map_structure/map_option_chain (10A) → VerticalWingV1.
generate_candidates → risk.filters.apply_filters → score → selector.readiness.
compute_readiness → selector.daily_selector.select_daily_trade → lifecycle_sim.simulate_exit.
Candidate construction is NOT re-implemented — generate_candidates already builds CALL_CREDIT
(short at PUT_CEILING, long +1 strike) + PUT_CREDIT (short at CALL_FLOOR, long −1 strike) at
the 2K/5K tier; the backtest just feeds it the profile-derived volume_threshold/spread_width.
Side filtering + structure/premium balancing are the live selector's job.

New pure modules (src/backtesting/):
- profile_runtime.py — derive_run_settings(profile) from FIELDS (target_time, threshold_label
  /wing_threshold, allow_*_credit, daily_selector, take_profit_pct, stop_loss_pct, target_dte),
  never by name. TP/SL: take_profit_pct = CAPTURE fraction (TP75→debit≤0.25×credit; TP50≤0.50×);
  stop_loss_pct = LOSS fraction (SL150→debit≥2.5×credit; SL200≥3.0×) — matches the reference
  vertical_wing_backtest. selector_config_from_profile mirrors run_scanner's SelectorConfig.
  threshold_scheme(symbol): SPX standard; SPY/QQQ provisional + warning.
- replay_providers.py — ReplayStructureProvider/ReplayQuoteProvider wrap the 10A mapped
  snapshots (provider-shaped get_snapshot/get_option_chain/get_spot/status; no network).
- lifecycle_sim.py — build_day_index (ts→strike→(call_mid,put_mid)+spot, once/day);
  simulate_exit walks (entry_ts, settlement_ts], debit = short_mid − long_mid, first-event-wins
  (SL wins ties → event_conflict), EOD = first snapshot in [16:00,16:20] settled to cash-settle
  INTRINSIC. Exit fields: exit_reason TP/SL/EOD/SKIPPED, exit_debit/pnl points+dollars,
  credit_kept_pct, hold_minutes, max/min spot, short/long touch, snapshots_checked,
  missing_price_count. dollars = points×100 (1 contract).
- replay_runner.py — run_backtest iterates dates (rows loaded once/day, shared across profiles),
  selects entry snapshot, maps, runs the reused pipeline, records corridor/WDS/gamma per snapshot,
  simulates the selected trade. resolve_profiles: all-main (4 primary) / all (+6 controls).
- reports.py — daily_pnl, equity_curve+drawdown, summary_by_{profile,symbol,corridor,wds_tier},
  no_trade_reasons, run_config.json. Metrics: win rate, total/avg/expectancy P&L, gross w/l,
  profit factor, max DD + duration, avg credit/risk/distance, TP/SL/EOD, CALL vs PUT, active vs
  inactive corridor, WDS-tier breakdown.

CLI scripts/backtest_run.py: --symbol --profile [id|all-main|all] --start --end --dte --run-label
--limit --latest-days --entry --include-controls --trading-root --output-root. Prints files/dates/
valid-entries/trades/skips/P&L/win-rate/max-DD/output. Outputs ONLY under
outputs/backtests/{latest,runs/<stamp>_<label>}.

Risk-cap learning: aggressive_paper_10k trades 5 contracts with a 10%-of-$10k planned-loss cap
($1000); planned = credit × (stop_mult−1) × 100 × 5 under SL_150 → credit ≲ 1.33 to pass the cap.
The live filters apply unchanged in the backtest (no fork), so big-credit synthetic candidates get
risk-rejected exactly as live.

VALIDATED on real data: SPX morning_5k_dynamic_tp75 5-day → 3 trades, +$45, 67% win, TP/SL/EOD
2/1/0. SPX all-main 20-day → 17 trades, TP/SL/EOD 7/6/4, win 0.59, DD $660. SPY all-main 8-day →
52 candidates MAPPED but 0 selected (SPX thresholds wrong for SPY → flagged provisional, not
over-interpreted). The SPX SL/TP math checks out: credit 0.55 → SL at debit 1.40 (≥2.5×0.55) →
−$85; credit 0.50 → TP at debit 0.10 (≤0.25×0.50) → +$40.

Tests: test_phase10b_backtest.py (24). Full suite 724 passed, ruff clean, manage_profiles 14/14,
smokes verified on REAL data.

Next: Phase 10C — SPY/QQQ threshold calibration + cross-check vs wingonomics_daily_stats.csv;
contracts sizing + comparison dashboards; corridor/WDS → selector weighting; full 1DTE (SPX 1DTE
exists; QQQ_1DTE empty; SPY_1DTE absent).

---

## 2026-06-04 (hotfix) — precise Tasty quote diagnostics (read-only, no orders)

Symptom: during RTH, ZerσSigma structure/spot/wings/gamma render but Tasty market data shows
"unavailable" for SPX — cockpit reads structure but can't price spreads. Expected architecture:
ZS = exposures/structure/spot; Tasty = quote chain / bid-ask. Goal: surface the EXACT stage that
breaks. No strategy/selector change, no broker execution, no order preview, no secrets.

New pure module src/providers/quotes/tasty_diagnostics.py — diagnose_quote_path(cfg, symbol,
target_dte, validation, client_factory, spot_hint, now) walks: (1) configured? (is_configured +
missing_fields NAMES only) → (2) auth/session (probe.login; fail → "Tasty auth failed / session
invalid.") → (3) chain summary/roots (fail → "SPX root/expiry unresolved.") → (4) expiry/DTE
(0DTE = today in chain expirations; has_0dte_today; else exact reason) → (5) root resolution for
that expiry → (6) chain/quote pull (small ATM ladder around spot_hint or chain midpoint; "Tasty
returned no chain" on fetch fail) → (7) per-quote QuoteValidation (stale/zero-bid/crossed/wide
counts + missing strikes + top blocker). Every stage is non-fatal (network errors caught →
sanitized exception TYPE only). Result dict is SAFE TO PRINT — only present/missing booleans +
env/base_url, never token/password/client_secret/refresh_token/account. `summary_rows(diag)` is
the single formatter for CLI + UI.

CLI: scripts/diagnose_tasty_quotes.py --symbol SPX --dte 0 [--spot-hint N --json] → prints
configured / auth / resolved root / resolved expiration / chain returned / quote count / strike
min-max / sample strikes / last_error / FINAL. Live Cockpit: render_market gained a "Why are
quotes unavailable?" expander (button-gated → one read-only round-trip; same fields + sanitized
JSON). Reuses the existing read-only TastyProbeClient + QuoteValidation — no new network surface.

Found on Dan's env: Tasty is NOT configured (no TASTY_* vars; defaults to certification) — the
diagnostic reports exactly that with the missing var names. That IS the current root cause: add
TASTY_* creds (+ TASTY_ENV=production) to .env to enable live quotes.

Tests: test_phase10b_tasty_diagnostics.py (12 — missing config / auth fail / auth network-error /
root unresolved / expiry unavailable / no chain / invalid quotes / stale quotes / happy path /
NO secrets echoed / summary_rows / CLI). Full suite 736 passed, ruff clean, manage_profiles 14/14,
diagnose CLI verified.

---

## 2026-06-04 (hotfix) — Tasty OAuth config detection (the diagnostic CLI wasn't loading .env)

Symptom: Dan's .env HAS OAuth creds (TASTY_CLIENT_ID/SECRET/REFRESH_TOKEN + ENV/BASE_URL/SCOPES +
QUOTE_PROVIDER=tastytrade), but the diagnostic reported "not configured" and mentioned missing
USERNAME/PASSWORD. TWO root causes:
1. scripts/diagnose_tasty_quotes.py never loaded .env — it called config_from_env() (reads
   os.environ) without load_config/load_dotenv (probe_tastytrade.py does load_config first). So the
   OAuth vars were invisible → config_from_env saw defaults → not configured.
2. The "missing config" presentation picked the SHORTER missing list (legacy = 2 < oauth = 3),
   misleadingly pointing at USERNAME/PASSWORD when nothing was set.

Fixes (no strategy/selector/backtest change; no execution; no order preview; never enables order
submission; no secrets):
- CLI loads .env via load_dotenv(repo/.env, override=False) before reading env.
- tasty_diagnostics.diagnose_quote_path now reports OAuth and legacy SEPARATELY: oauth_configured,
  legacy_configured, auth_mode (oauth/legacy_session/none), oauth_missing_fields, legacy_missing_
  fields, auth_summary ("OAuth credentials found. Using OAuth refresh-token auth." / "Tasty OAuth
  credentials missing: TASTY_CLIENT_ID, ...; optional legacy fallback ..."). OAuth-led — never
  claims unconfigured merely because USERNAME/PASSWORD are absent. Provider auth preference was
  ALREADY correct (probe.login() does OAuth when has_oauth(), legacy only as fallback) — unchanged.
- QUOTE_PROVIDER surfaced + warning when != tastytrade ("...will NOT use live Tasty quotes unless
  Live data-source override is selected or QUOTE_PROVIDER=tastytrade").
- Trade-scope SAFETY read: warns "Trade scope is present, but TASTY_ENABLE_ORDER_SUBMISSION=false.
  Quote fetching remains read-only." — informational only; no submit path; never blocks.
- .env.example: TASTY_ENV=production, TASTY_BASE_URL=https://api.tastyworks.com, TASTY_SCOPES=read
  openid, TASTY_ALLOW_TRADE_SCOPE=false, TASTY_ENABLE_ORDER_SUBMISSION=false, QUOTE_PROVIDER=
  tastytrade + safety notes (order submission stays disabled).
- tests/conftest.py (NEW, test-infra only): forces QUOTE_PROVIDER=mock + clears TASTY_* for the
  session so the suite is hermetic against the personal .env. Dan's .env (now QUOTE_PROVIDER=
  tastytrade) had been leaking into 8 scanner tests via run_scanner's load_config → live-Tasty
  attempts → NO_TRADE. load_config uses load_dotenv(override=False), so pre-set test values win.

VERIFIED on Dan's real env: configured True, OAuth/API configured True, auth mode oauth, missing
OAuth vars none, TASTY_ENV production, QUOTE_PROVIDER tastytrade, auth SUCCESS → resolved root SPXW,
today's 0DTE, chain returned. (It even surfaced a real downstream finding: the ATM quote fails the
conservative spread_abs $5 validation cap.) Tests: test_phase10b_tasty_diagnostics.py now 20 (+8:
OAuth-without-legacy valid, OAuth-present-not-unconfigured-on-auth-fail, legacy optional fallback,
missing-names-not-values, QUOTE_PROVIDER mock warning + tastytrade no-warning, trade-scope warning
without execution, no-execution-paths). Full suite 744 passed, ruff clean, manage_profiles 14/14.

---

## 2026-06-04 (hotfix) — Live Cockpit Tasty status reconciliation (chain returns but UI said "unavailable")

Symptom: the Tasty diagnostic proved OAuth + auth + SPXW root + 0DTE expiry + chain all work, yet
the Live Cockpit still showed "Tasty market data: unavailable / Strategy eligible: no / generic
banner." ROOT CAUSE: streamlit_main fetched the chain with `get_option_chain(SYMBOL,
expiry=structure.expiry)` — NO `request`/`required_strikes`. The Tasty REST provider returns None
without required_strikes ("production provider does not pull whole chains"), so the cockpit's
`chain` was always None under tastytrade → `market_data_available = chain is not None` False →
everything collapsed to the generic "unavailable" banner. The diagnostic worked because it probes
explicit ATM strikes. (The scanner already builds a QuoteRequest; the cockpit didn't.)

Fixes (NO strategy/selector/risk change; no execution; no order preview; no secrets):
1. Root cause — streamlit builds the SAME structure-anchored QuoteRequest the scanner uses
   (`ch.build_quote_request(SYMBOL, structure, STRATEGIES)`) and passes it to get_option_chain. The
   Tasty chain now returns in the cockpit, matching the diagnostic.
2. Quote STATE model — new pure `cockpit_helpers.cockpit_quote_status(...)` classifies the
   ALREADY-FETCHED chain into nine distinct states (not one "unavailable"): not_configured /
   auth_failed / root_unresolved / expiration_unavailable / chain_unavailable /
   chain_returned_validation_failed / chain_returned_usable / mock / unknown_error, each with a UI
   label, an `available` flag (usable quotes or mock), an eligible hint, a precise banner, and
   details (quote_count, validation passed/failed, top blocker, root, expiration, strike range,
   max_spread_abs, max_age, observed worst spread, missing strikes, last_error). The generic
   "market may be closed…" banner now shows ONLY for true chain_unavailable.
3. render_symbol_health + render_market use the status: market_data card shows the precise label;
   "Strategy eligible" shows "blocked" on validation-fail; the banner explains the blocker. The
   "Why are quotes unavailable?" expander → "Quote status & diagnostics" shows the cockpit's OWN
   state (no network) + the validation config, then the existing button-gated network probe.
4. CLI parity — scripts/diagnose_cockpit_quote_status.py runs the SAME cockpit path (load_config →
   build providers → build_quote_request → fetch chain → cockpit_quote_status) and prints provider /
   data source / chain / validation / STATE / label / banner. Lets us verify the cockpit status
   without the app.

REAL FINDING on Dan's env (cockpit parity CLI): chain RETURNED (SPXW @ 2026-06-05, 8 quotes for the
wing strikes 7545/7550/7600/7605), but all 8 failed validation with top blocker **stale** (quotes
>10s old → market closed / REST delayed), NOT spread_abs. So the cockpit now reads
`chain_returned_validation_failed` → "chain returned / validation blocked (stale)" instead of
"unavailable". spread_abs investigation: max_spread_abs=`TASTY_QUOTE_MAX_SPREAD_ABS` (default 5.0)
and max_age=`TASTY_QUOTE_MAX_AGE_SECONDS` (default 10.0) are BOTH per-quote, env-configurable, and
applied to the SELECTED candidate legs (the wing strikes), not just one ATM probe. Validation flags
individual quotes (chain still returns); it does not drop strikes. The diagnostic's earlier
spread_abs finding was on its ATM ladder. Left validation UNCHANGED (risk-adjacent; do not loosen
blindly) — Dan can raise TASTY_QUOTE_MAX_AGE_SECONDS / TASTY_QUOTE_MAX_SPREAD_ABS in .env if needed;
during live RTH with fresh quotes the stale block clears on its own.

Tests: test_phase10b_cockpit_quote_status.py (14 — each state, generic-banner-only-for-no-chain,
validation-blocked != unavailable, mock available, missing strikes, build_quote_request, CLI parity
with mocked providers, no-secrets). Updated test_phase9i_ui (quote_chain_status → cockpit_quote_status).
Full suite 758 passed, ruff clean, manage_profiles 14/14, cockpit parity CLI verified on REAL data.

---

## 2026-06-04 — Phase 10B UI hotfix: trader-first labels + Run Strategy workflow + stale clarity

Backend was correct (chain returns, states classified) but the cockpit READ confusingly: clipped
raw enums in cards (`TRADE_CALL_CREDIT`, `chain_returned_validation_failed`, `vertical_wing_v1`,
`zerosigma_api`), a "Best Eligible Setup" header that fired even when nothing was actually priceable,
and no obvious "how do I run a strategy" path. This pass is UI-only — NO strategy / selector / risk /
backtest logic touched, NO execution surface added.

1. Friendly label helpers (pure, stdlib-only, in `operator_mode.py` — fully unit-tested, zero project
   imports): `provider_short` (zerosigma_api→"Zσ API", tastytrade→"Tasty", null→"Manual"),
   `decision_label`/`side_label` (TRADE_CALL_CREDIT→"Call Credit", NO_TRADE→"No Trade"),
   `runner_state_label` (stopped→"Stopped"), `quote_state_label(state, top_blocker)` — the key one:
   `chain_returned_validation_failed` splits on the blocker → **"Stale"** when top_blocker=="stale"
   else "Validation Blocked"; usable→"Available", chain_unavailable→"No Chain", mock→"Sandbox".
   `candidate_label` ("Put Credit 7550/7545", drops .0), `candidate_status_label` (pills:
   Eligible / Blocked: stale quotes / Blocked: quote validation / Blocked: risk cap / Blocked:
   filters / Observe only), `header_status_cells` (7 short read-only cells: Strategy / Structure /
   Quotes / Runner / Last Signal / Paper P&L / Safety). The long pinned
   `cockpit_quote_status['label']` is UNCHANGED — these are separate short card labels.

2. Stale-quote clarity (the real after-hours case): `quote_state_banner` for stale →
   "Tasty chain returned, but quotes are stale. Structure preview only — live eligibility will
   re-check during RTH."; `stale_quote_mode_banner` adds the 🌙 after-hours sub-text. Wired into
   render_symbol_health (Quotes card now shows "Stale" not a clipped enum) and render_market.

3. "Best Eligible Setup" is now honest — the header says **"Best Eligible Setup"** only when
   `QUOTE_STATUS["available"]`; otherwise **"Best Candidate Preview — Stale Quotes"** (stale) or
   **"Best Candidate Preview — Blocked"**. Setup line uses candidate_label + score (2dp) + credit $.

4. Run Strategy workflow — Tester tab relabeled **"🧪 Run Strategy"** (tab_labels), page title
   "🧪 Run Strategy — local paper test", and a prominent 5-step panel: Choose strategy → Confirm data
   source → Preview Strategy → Start Paper Test → Stop Test / Review Latest. Buttons relabeled
   (👁 Preview Strategy / ▶ Start Paper Test / ■ Stop Test / 📄 Review Latest). Header strip is now a
   clearly READ-ONLY "Status Summary" with a CTA: "▶ To run a strategy, open the 🧪 Run Strategy tab".

5. Candidate cards use the friendly Setup + Status pill; raw JSON score breakdown stays Advanced-only
   (Simple Mode shows a caption). No raw IDs surface in Simple Mode.

REAL-ENV check (cockpit parity CLI, after-hours): STATE=`chain_returned_validation_failed`, top
blocker=**stale** (8/8 quotes >10s old, SPXW @ 2026-06-05, wing strikes 7545/7550/7600/7605) → the
cockpit now renders Quotes="Stale" + the stale banner + "Best Candidate Preview — Stale Quotes",
exactly the intended UX. During live RTH the stale block clears on its own.

Tests: NEW test_phase10b_ui_labels.py (20 — all label helpers incl. stale split, no-raw-enum-leak,
banners, candidate labels/pills, 7-cell header, Run-Strategy tab/buttons, and source-wiring for
Best-Eligible gating + Run-a-Strategy panel + read-only header + Advanced-only raw JSON + no-exec).
Updated test_phase9e (tab "Run Strategy") + test_phase9f (button labels). Full suite **778 passed**,
ruff clean, manage_profiles 14/14, both diagnose CLIs read-only (no secrets, no orders).

---

## 2026-06-04 — Phase 10C: full trader UX audit + Simple-Mode cleanup + after-hours DTE + backtests

Dan reviewed screenshots and flagged 9 issues: stale 0DTE confusion after close, legacy dev language
("Phase 4.1", "score_edge", "quote_quality_bucket"), confusing fields (threshold/gap/skew/edge),
unclear "Validate strategy", a dead "enabled" checkbox, Strategy Builder showing Sandbox while the
app runs Live, "Runner" wording, undiscoverable backtesting. UI-ONLY pass — NO strategy/selector/risk/
backtest logic changed, NO execution surface added.

A. **Simple-Mode jargon purge (candidate cards).** The whole candidate detail block (Threshold / Gap /
   Rejection type / score_edge / quote bucket / b/a quality / Clock skew / Skew(s) / Phase 4.1/4.2 /
   selector_blockers / strict_target_dte / raw st.json) rendered in Simple Mode — only the final json
   was gated. Split into two pure render fns: `_render_candidate_simple` (Setup / Short-Long / Score /
   Credit / Quote Status / Risk Status / Blocker / Anchor / Anchor volume / distance) and
   `_render_candidate_advanced` (all raw fields, Advanced-only). The ranked table now shows clean
   trader columns in Simple Mode; raw columns (b/a quality, gap, edge, bucket, risk_type, …) move under
   `if not simple_mode: row.update(...)`. New pure helpers in operator_mode: `anchor_label`
   ("put_ceiling_2k"→"Put Ceiling 2K"), `candidate_quote_status_label`, `candidate_risk_status_label`,
   `candidate_blocker_label`.

B. **"Runner" → "Test Status".** Header cell + tester card now read "Test Status" (friendly state via
   `test_status_label`); "Running" → "Active paper test"; "Clear stale runner" → "🧹 Clear stale test";
   the force-stop checkbox is Advanced-only and renamed `BTN_FORCE_STOP` = "⏹ Force stop local test
   process"; PID stays Advanced-only; control messages humanized via `humanize_runner_message`.

D. **Corridor explainer.** Plain-English caption: "Corridor is active only when the **10K call floor**
   is below spot AND the **10K put ceiling** is above spot — i.e. CW1 (10K call floor) < Spot < PW1 (10K
   put ceiling)." Metric labels relabeled "10K call floor (CW1)" / "10K put ceiling (PW1)".

E. **After-hours DTE preview (display-only; never mutates the profile).** New pure
   `resolve_preview_dte(now_et, profile_dte, mode)`: a 0DTE profile previews 1DTE after 17:00 ET (pre-
   midnight), back to 0DTE next session; 1DTE profiles and non-live-preview modes never roll. Module
   globals `PREVIEW_DTE` / `AFTER_HOURS_PREVIEW`; banner via `after_hours_preview_banner`. Wired into
   Live Cockpit (banner + "Profile DTE / Preview chain" caption) and Run Strategy (per selected
   profile). The on-demand Tasty quote diagnostic now defaults its Target DTE to the rolled preview DTE
   (so after close it probes the fresh 1DTE chain, not dead 0DTE) — a real, safe roll. The HEAVY main-
   chain re-fetch (fetch a 1DTE chain for the whole cockpit) is DEFERRED: the ZS structure is 0DTE and
   pairing it with a 1DTE chain would mismatch candidate construction. Profile / paper-test / backtest
   DTE are never changed.

F. **Strategy Builder.** (1) "Validate strategy" → **"Check Strategy Setup"** (`BTN_VALIDATE`) + an
   explainer ("validates fields, side rules, DTE, TP/SL, data-source compatibility… does not run or
   trade"). (2) **Enabled** was a dead checkbox (all 14 profiles `enabled: false`, filtering is by
   preset_kind). Relabeled "Show in main strategy list"; `simple_mode_profile_ids` is now enabled-aware
   with an all-disabled FALLBACK (curates to enabled Main profiles if any are checked, else shows all
   Main — never empty); Simple-save stops force-setting `enabled: True`. (3) **Data source** radio
   relabeled "Profile default data source" + a "Current run source: Live · Structure: Zσ API · Quotes:
   Tasty" caption + a mismatch warning when the profile default differs from the live app source (app
   source wins).

G. **Discoverable backtests.** New **📈 Backtests** tab (7th) — `render_backtests()`: symbol/profile/
   latest-N-days/DTE/run-label inputs build the exact read-only CLI (`om.backtest_command` →
   `python -m scripts.backtest_run …`), shown in a code block (NOT launched from the UI — runs can take
   minutes). "🔄 Refresh Latest Results" reads `outputs/backtests/latest` via new
   `cockpit_helpers.read_backtest_results` (reuses the pure `reports.metrics`; handles missing dir/files
   gracefully) → cards: Trades / Win Rate / Total P&L / Max Drawdown / TP-SL-EOD + a by-profile table.
   Note: "Uses local saved snapshots only. No live API calls. No broker execution. No order preview."

REAL-ENV check (cockpit parity CLI, after-hours): STATE=`chain_returned_validation_failed`, top
blocker=**stale** — exactly the after-hours case Task E surfaces. Both diagnose CLIs read-only (no
secrets, no orders); headless streamlit import (mock providers) OK — 7 tabs, PREVIEW_DTE + render_backtests
present.

Tests: NEW test_phase10c_ui.py (24 — preview-DTE roll, after-hours banner, anchor/quote/risk/blocker
labels, test-status + humanize, Check-Setup + enabled-curation, backtest_command + read_backtest_results
(missing/empty/populated tmp dirs), and source-wiring for Simple-no-jargon / Test-Status / corridor /
after-hours / data-source-mismatch / Backtests-local-only / friendly-tabs / no-exec). Updated pinned
tests for the relabels (tab len 7, Test Status, Check Strategy Setup, Clear stale test, corridor
labels). Full suite **802 passed**, ruff clean, manage_profiles 14/14.

Deferred (noted in the audit): heavy main-chain 1DTE re-fetch (E); launching backtests from the UI (G —
command-only this pass); SPY/QQQ wing calibration; per-profile TP/SL lifecycle wiring (still PAPER_* env).

---

## 2026-06-04 — Phase 10C follow-up: after-hours 1DTE labeling · stale=preview-only · backtest runner · custom profiles

A read-only discovery WORKFLOW (5 parallel Explore agents → structured JSON) mapped every change site
first (after-hours, stale-decision, backtest UI/API, profile visibility, jargon); implementation then
done in the main loop. UI-only — NO strategy/selector/risk math changed, stale validation NOT loosened,
no execution surface added.

A. **Explicit 1DTE after-hours quote labeling.** `after_hours_preview_banner` rewritten to lead with
   "Quote chain: 1DTE after-hours preview" + "Profile DTE: 0DTE" + "Strategy DTE unchanged". New pure
   `after_hours_quote_detail(active, dte)` → "1DTE quote chain · after-hours preview", shown as the
   Quotes-card sub-label (st.metric delta, color off). Symbol-health + Run-Strategy captions now say
   "Quote chain: 1DTE after-hours preview · Strategy DTE unchanged · Structure: still Zσ context".
   Never mutates profile / paper-test / backtest DTE (preview/diagnostic only).

B. **Stale quotes are PREVIEW-ONLY (never a fake live decision).** New module flags `QUOTE_STALE`
   (chain returned but validation-blocked by staleness) and `LIVE_QUOTES_STALE` (= stale AND not
   sandbox). New pure `decision_headline(available, quote_state, top_blocker)` → {live, title, note}:
   usable → "Decision" + "Why: cleared selector/quote/risk gates"; stale → "No Live Decision — Quotes
   Stale" + "Why not: quote validation failed because quotes are stale… preview-only until fresh RTH
   quotes"; non-stale block → "…Quotes Blocked: <reason>"; no chain → "…Quotes Unavailable". Wired into
   render_candidates (Decision subheader + the daily-selector success line, which now shows "Preview
   Candidate" instead of a green selection when not usable). **Start Paper Test** is disabled when
   `LIVE_QUOTES_STALE` (`_can_start = can and not LIVE_QUOTES_STALE`) with reason
   `START_TEST_STALE_REASON` = "Cannot start live paper test: quotes are stale. Try again during RTH
   or use Sandbox." Preview Strategy stays enabled but its launch message is marked preview-only.

C/D. **Backtests is now a real UI runner, not a command-copy page.** `render_backtests` rebuilt:
   Symbol · Strategy-profile (incl. custom, with "Show all saved profiles") · DTE (1DTE shown only when
   local data exists, else 0DTE-only) · Date mode radio (Latest N days / Date range / All data) with
   `st.date_input` calendars validated against `available_dates` · a per-symbol×DTE availability line
   ("SPX 0DTE: 146 files · 2025-10-31 → 2026-06-04") · auto-filled Run label · **▶ Run Backtest**
   (in-process `run_backtest` under `st.spinner`, writes reports, reruns → result cards) · **Refresh
   Latest Results** · result cards (Trades/Win/PnL/Drawdown/TP-SL-EOD) + by-profile table. The CLI
   moved to a secondary **"Advanced — CLI command"** expander (no longer the page focus; "Run this in a
   terminal" removed). New pure readers in cockpit_helpers: `backtest_data_range` / `backtest_data_
   availability` / `backtest_range_caption` (reuse `raw_snapshot_loader.available_dates`, read-only,
   graceful on empty). `om.backtest_default_label`. Verified in-process on real data: SPX all-main
   latest-3 → 2 trades, +$87.5, TP/SL/EOD 2/0/0; reads back via the same `read_backtest_results` path.

E. **Saved/custom profiles are visible + usable.** `profile_category(None)` now returns **"Custom"**
   (was "Legacy / Archived"); `PROFILE_CATEGORIES` ends with "Custom". The Builder/Run-Strategy/Backtests
   "Show comparison and legacy profiles" checkbox renamed **"Show all saved profiles"** (Simple Mode
   defaults to Main; ticking it surfaces comparison · research · custom). Backtests defaults to show-all
   so custom profiles appear immediately. INVALID profiles are no longer silent: Run-Strategy +
   Backtests show "⚠ N saved profile(s) have validation errors and are hidden … fix in Zσ Strat Builder".

F. **Simple-Mode audit:** the discovery agent confirmed all score_edge / quote_quality_bucket / clock-
   skew / Phase-4.x jargon stays Advanced-gated; the two leaks it found — "Run this in a terminal" and a
   live "Decision" in stale state — are both fixed by C and B. `status_strip_cells`' "Runner" is an
   unused helper (never rendered).

REAL-ENV (RTH at run time): cockpit STATE=`chain_returned_usable`, Strategy eligible=yes → the cockpit
correctly shows a LIVE Decision and Start enabled. The stale path (preview-only + Start disabled)
activates after 17:00 ET. Both diagnose CLIs read-only (no secrets, no orders); headless import OK.

Tests: NEW test_phase10c_followup_ui.py (18 — after-hours detail/banner, decision_headline live/stale/
blocked/unavailable, START_TEST_STALE_REASON, stale-gating source wiring, backtest data range/caption/
availability + default-label + UI-runner wiring (date modes / All data / Run button / spinner /
in-process run_backtest / CLI-in-Advanced), Custom category + show-all + invalid surfacing, no-jargon,
no-exec). Updated pins: profile_category(None)→Custom, cats→…Custom, "Show all saved profiles",
"Quote chain", backtests CLI-secondary. Full suite **820 passed**, ruff clean, manage_profiles 14/14.

Deferred: heavy main-cockpit 1DTE chain re-fetch (label/diagnostic roll only); in-process backtest has
no hard timeout (spinner + soft "All data" size note — large all-data runs block the tab while running);
results charts (cards + by-profile table only); SPY/QQQ wing calibration; per-profile TP/SL lifecycle.

---

## 2026-06-05 — Phase 10D-B implementation plan: fixed sizing + output isolation + label cleanup

Branch: `codex/phase-10d-backtest-sizing-ux-cleanup`.

Scope is intentionally narrow: do not change strategy logic, selector math, risk math, quote validation,
broker execution, or order preview behavior.

Plan:

1. Isolate backtest test outputs so pytest never refreshes app-visible `outputs/backtests/latest`.
2. Add fixed sizing only: `--starting-balance` (default 10000) and `--contracts` (default 1).
3. Thread contracts through replay exit simulation and reports; equity starts at starting balance;
   report ending balance, return %, max drawdown $, and drawdown % from prior equity peak.
4. Add Backtests tab sizing controls, presets, account-adjusted cards, and result sizing context.
5. Clean Simple Mode raw labels / enum display, hide raw JSON in Simple Mode, and round long floats.
6. Suppress empty chart renders that produce Vega extent warnings.
7. Apply a small Windows-safe diagnostic encoding fix if it stays local to CLI output.
8. Validate with pytest, ruff, profile validation, diagnostics, and 1-lot vs 5-lot smoke backtests.

Completion notes: pytest backtest/scaffold tests now write under temp `OUTPUT_DIR`/`--output-root`
and fingerprint repo-local `outputs/backtests/latest`; fixed sizing flows through replay, reports,
Backtests UI, and run_config; Simple Mode hides raw JSON/profile IDs and maps remaining raw labels
to trader-facing copy. Validation passed with `.venv`: `pytest -q` (827), `ruff check .`,
`manage_profiles --validate-all`, diagnostics, and 1-lot vs 5-lot smoke backtests.

---

## 2026-06-05 — Phase 10D-C: backtest explainability + quote diagnostics

- Added backtest explainability artifacts without changing strategy, selector,
  risk, or quote-validation math.
- Candidate/trade CSVs now carry readiness, risk, quote, selector blocker, and
  side-allowed fields.
- `no_trade_reasons.csv` now records structured skip context: entry target,
  status, first blocker, candidate/eligible counts, filter counts, top selector,
  risk, and quote reasons.
- Reports now include avg win/loss, largest win/loss, avg hold, max consecutive
  losses, best/worst day, side/corridor/WDS P&L, and new summary CSVs by side,
  exit reason, and day.
- Backtests tab now shows account cards, a generated low-trade explanation,
  guarded charts, filtered trade log, skipped/no-trade tables, and breakdown
  tabs.
- Tasty diagnostics now distinguish no config/auth/root/expiry, no required
  strikes, no chain, empty quote payloads, missing required strikes, stale
  quotes, validation-blocked quotes, and usable quotes. No validation caps were
  loosened.
- Deferred: risk-based backtest sizing, selector weighting changes, broker
  execution, order preview, and any live order path.

---

## 2026-06-05 — Phase 10D-D: global strategy synopsis

- Added deterministic Strategy Synopsis helpers in `operator_mode.py`; no AI/API
  calls, no strategy/selector/risk changes.
- Synopsis now appears in Live Cockpit, Zσ Strat Builder, Run Strategy,
  Backtests, Paper Portfolio, Stats / Review, Settings, and selected-profile
  detail surfaces.
- Added a separate deterministic Backtest Run Summary narrative for loaded
  backtest results, including P&L, return, drawdown, trades, exits, and top
  blocker.
- Added tests for dynamic/control/put-only/observe/custom profiles, raw-enum
  hiding, page placement, run narrative, and no execution surface.

---

## 2026-06-05 — Phase 10E: backtest comparison dashboard

- Added a dedicated local `backtest_compare` workflow that reuses the existing
  Phase 10D multi-profile replay over one shared symbol/date/DTE/sizing set.
- Added comparison outputs under `outputs/backtests/comparisons/`: profile
  rankings, dynamic-vs-control, profile/side/exit/corridor/WDS/entry-window
  breakdowns, full trade logs, deterministic narrative, and run configuration.
- Added a transparent research ranking score with visible components and
  deterministic promotion labels. Rankings and labels never feed live selection
  or profile execution.
- Added a Compare Strategies section in Backtests with group/profile/date/sizing
  controls, ranked table, best-metric cards, narrative, dynamic/control and
  corridor/WDS tables, and expandable trade logs by profile.
- Deferred: SPY/QQQ threshold calibration, risk-based sizing, selector-weight
  changes, broker execution, order preview, and order placement.

---

## 2026-06-07 — Phase 10F: dynamic selector attribution + control edge audit

- Added research-only dynamic selected-side attribution. Each selected dynamic
  trade records its best opposite candidate, both selector component sets,
  choice reason, and a mechanically simulated opposite outcome using the same
  historical TP/SL lifecycle. The selected trade itself is unchanged.
- Added selected-side split, selected-vs-opposite opportunity cost, call-control
  edge dimensions, deterministic dynamic failure taxonomy, attribution narrative,
  and research-only recommendation outputs.
- Added Backtests → Compare Strategies → “Why did dynamic underperform?” with
  side metrics, dynamic-vs-control P&L, opposite availability, failure buckets,
  call-control edge audit, and recommendation notes.
- Updated research labels: positive controls are benchmark/comparison only;
  underperforming dynamics remain watchlist/needs-tuning. Positive control
  results are not production approval.
- No strategy logic, selector math, risk math, quote validation, broker path,
  order preview, or execution behavior changed.
