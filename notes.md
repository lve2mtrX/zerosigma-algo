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
