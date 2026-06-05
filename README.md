# ZerσSigma Algo Cockpit

Portable, local, multi-strategy algorithmic trading cockpit. Designed to scan
intraday options structure, propose ranked candidates, log decisions, and track
manual / paper trades — without auto-executing anything until a broker is wired
in deliberately and explicitly.

> **Status:** Phase 1 scaffold. No live execution. No broker connected.
> Structure data is sourced read-only from the ZerσSigma API (sibling repo).

---

## What this is

A general-purpose strategy cockpit, **not a single-strategy app**.

- **Multi-strategy by design** — strategies are registered via
  [`config/strategies.yaml`](config/strategies.yaml). Each strategy is a
  self-contained module under `src/strategies/<strategy_name>/`
  (candidates → scoring → decision). The framework code (app, providers,
  risk, reporting, storage) is strategy-agnostic and never imports a
  specific strategy directly.
- **Vertical Wingy is the first strategy module only.** It lives in
  `src/strategies/vertical_wing/`. The cockpit does not depend on it; if
  you disable it in `strategies.yaml`, the cockpit still launches and
  registers whatever else is enabled.
- **Portable** — no hardcoded usernames, drive letters, or Dropbox paths.
  Everything is repo-relative or env-driven (`.env` / `config/*.yaml`).
- **Read-only against the live ZerσSigma stack** — the cockpit consumes the
  public ZerσSigma API (`/api/v1/market/*`, `/api/v1/exposure/*`) for
  pre-computed structure, Greeks, and exposures. It does not modify
  Dashboard, API, worker, Redis, or production execution code.
- **Broker undecided.** A future broker API will supply spot, bid/ask,
  spread marks, and (eventually) execution. Phase 1 has no broker wired.
- **Phase 1 = scanner + decision log + manual & paper P&L only.**
  **No live execution. Ever, until explicitly opted in.**

### Phase 1 scope (this scaffold)

- Strategy registry with one registered strategy: `vertical_wing_v1`
- Scanner framework (planning only — no live scan loop yet)
- Decision log (`outputs/runs/decision_log.jsonl`)
- Manual trade tracker (`outputs/runs/manual_trades.csv`)
- Local / paper P&L tracker (`outputs/runs/paper_*.csv`)
- EOD summary generator (`outputs/daily/eod_summary.{md,json}`)
- Local Streamlit dashboard skeleton

### Explicitly out of scope (Phase 1)

- Live broker auth / quotes / execution
- Auto-trading of any kind
- Modifying Dashboard, ZS API, workers, or any production component

---

## Install

Requires Python 3.11+.

```powershell
# clone or open this folder anywhere on disk
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # PowerShell on Windows
# or: source .venv/bin/activate  # bash/zsh

pip install -e .
copy .env.example .env           # then edit .env locally
```

`.env` is gitignored. Only `.env.example` is committed.

---

## Run

All three entry points work locally against the stub structure + mock quote providers. No broker. No live ZS API call.

```powershell
# 1) Streamlit cockpit (preferred — full UI)
python -m scripts.run_streamlit
# (equivalent: streamlit run src/app/streamlit_main.py)

# 2) One-shot scanner tick — writes outputs/latest/ and outputs/runs/{date}/
python -m scripts.run_scanner
#    --profile aggressive_paper_10k    (default; see config/risk_profiles.yaml)
#    --strategy vertical_wing_v1
#    --symbol SPX
#    --dry-run                          (don't persist)

# 3) Generate EOD summary — writes outputs/daily/{date}/ AND outputs/latest/
python -m scripts.run_eod_summary
```

What the scanner does in one tick:
1. Loads `config/*` and the active risk profile (or `--profile NAME`).
2. Acquires a **StructureSnapshot** from the StructureProvider — context only
   (MaxVol, DA-GEX, gamma regime, DDOI pin, PUT_CEILING and CALL_FLOOR levels
   at 2K and 5K thresholds). No bid/ask/mid here.
3. Acquires an **OptionChainSnapshot** from the QuoteProvider — full chain
   with bid/ask/mid, volume, OI, optional Greeks per strike, plus spot.
4. Passes **both** to every enabled strategy: `generate_candidates(structure, chain, params)`
   → applies risk filters → scores → selects.
5. Writes ranked candidates (CSV with leg bid/ask/mid + planned/theoretical $)
   and the decision log (JSONL, with both provider names + timestamps +
   spot from QuoteProvider) to:
   - `outputs/runs/{YYYY-MM-DD}/` (append, per-day history)
   - `outputs/latest/` (overwrite for the cockpit's "current view")

Under the default `aggressive_paper_10k` profile the demo data emits a
`TRADE_CALL_CREDIT` decision for SPX 5815/5820 at $0.60 credit (planned stop
risk $450; theoretical max loss $2,100; score ~0.61).

### Session controls in the cockpit

`config/risk_profiles.yaml` ships two templates: `aggressive_paper_10k` (active default) and `conservative_paper_10k`. In the Streamlit sidebar you can:

- pick a strategy
- pick a risk profile (resets the session)
- click **Reset to profile defaults**

In the **Session controls** expander you can edit every field below for the running session — every change is appended to `outputs/runs/{date}/config_change_log.jsonl`:

```
starting balance · contracts/trade · max open positions
max daily loss (dollars / percent)
max planned trade loss (dollars / percent)
max theoretical trade loss (dollars / percent)
spread width · stop variant · profit targets
no-trade score threshold · minimum credit
max bid/ask width · minimum distance from spot
```

### Where outputs live

```
outputs/
├── latest/                       # always-current view (overwrites)
│   ├── ranked_candidates.csv
│   ├── decision_log.jsonl
│   ├── manual_trades.csv
│   ├── paper_trades.csv
│   ├── paper_positions.csv
│   ├── paper_equity_curve.csv
│   ├── eod_summary.md
│   └── eod_summary.json
├── runs/{YYYY-MM-DD}/            # per-day append-only history
│   ├── ranked_candidates.csv
│   ├── decision_log.jsonl
│   ├── manual_trades.csv
│   ├── paper_trades.csv
│   ├── paper_positions.csv
│   ├── paper_equity_curve.csv
│   └── config_change_log.jsonl
└── daily/{YYYY-MM-DD}/
    ├── eod_summary.md
    └── eod_summary.json
```

### Provider modes

Two structure-provider modes are available; the default is **safe (stub)**.

```powershell
# (1) Safe default — stub + mock (no network, no credentials)
python -m scripts.run_scanner
python -m scripts.run_scanner --structure-provider stub

# (2) Read-only ZS API — requires ZS_API_AUTH_MODE + credentials in .env
python -m scripts.run_scanner --structure-provider zerosigma_api
```

The Streamlit cockpit also exposes a structure-provider dropdown in the sidebar.

#### Read paths and `.env` for ZS API mode

The cockpit supports **five** auth modes. Picking one decides which ZS
endpoints get called:

| Mode | Sends Authorization header? | `/market/snapshot` | `/exposure/series` | Required `.env` vars |
|---|---|---|---|---|
| `none`          | n/a   | ❌ | ❌ | n/a (stub provider used) |
| `public_only`   | **no** | ✅ | **skipped** | `ZS_API_BASE_URL` only |
| `bearer`        | yes   | ✅ | ✅ if subscribed | `ZS_API_BASE_URL`, `ZS_API_TOKEN` |
| `login`         | yes   | ✅ | ✅ if subscribed | `ZS_API_BASE_URL`, `ZS_API_USERNAME`, `ZS_API_PASSWORD` |
| `service_token` | yes   | ✅ | ✅ if subscribed | `ZS_API_BASE_URL`, `ZS_API_USERNAME`, `ZS_API_SERVICE_KEY` |

`public_only` is the **recommended smoke-test mode**: no credentials, no
header attached, but you still validate connectivity, response shape, and
the public exposures payload. VW levels (`PUT_CEILING_{2K,5K}`,
`CALL_FLOOR_{2K,5K}`, `MaxVol`) will be `None` because they're derived
from the subscription-gated `/exposure/series` — that's expected.

##### Smoke test the live ZS API without secrets

```powershell
# .env:
#   ZS_API_BASE_URL=https://api.your-zerosigma-host.example
#   ZS_API_AUTH_MODE=public_only

python -m scripts.smoke_zs_api                  # SPX
python -m scripts.smoke_zs_api --symbol SPY
python -m scripts.smoke_zs_api --json           # machine-readable
```

The script prints a SANITIZED summary (provider, auth_mode, configured,
spot, exposures, missing_fields, http_status) and exits **0** on success,
**0** with a warning when the provider isn't configured (so CI can run it
defensively), and **1** with a clean message (no traceback) when a
configured call fails.

It **never** prints tokens, passwords, service keys, headers, or raw env
values.

##### Run the scanner against live ZS API + mock quotes

```powershell
python -m scripts.run_scanner --structure-provider zerosigma_api
```

Phase 2.6 changed this in two ways:

1. **The mock quote provider now re-centers on live ZS structure**. If
   ZS says `put_ceiling=7600` and your default `MockQuoteProvider`
   centered on 5800, VW used to find no chain quotes at 7600 and emit
   "all candidates rejected by filters" (misleadingly). The scanner now
   derives a `QuoteRequest` with `spot_hint` (precedence:
   `structure.spot if > 0` → `maxvol` → median of required strikes →
   mock default) and `required_strikes` (the active ceiling/floor +
   long-leg partners), and the mock provider builds an aligned chain
   that includes every required strike.

2. **Public_only mode populates anchors via the public payload**.
   `wings.put_ceiling` / `wings.call_floor` ride along on
   `/market/exposures` (subscription-FREE), so VW gets one ceiling and
   one floor under `public_only`. The 5K-tier variants still require
   `/exposure/series` (subscription-gated) — those stay None.

The decision log's `snapshot_summary` now carries:

- `required_strikes` — strikes VW asked the chain for
- `quote_chain_min_strike` / `quote_chain_max_strike`
- `missing_required_quote_strikes` — empty when everything aligned
- `quote_spot_source` — one of `structure_spot | maxvol | structure_midpoint | mock_default`
- `quote_spot_hint` — the numeric value passed to the provider

If `NO_TRADE` shows up, the refined `explanation` distinguishes:

- *"no structure anchors"* — `put_ceiling_*` and `call_floor_*` all None upstream
- *"quote chain missing required structure strikes [...]"* — chain didn't cover the anchors
- *"Best score X below no_trade_score_threshold Y"* — legit gating; check filters

##### Diagnose snapshot/exposures shape

If your smoke output shows `spot=0.0` or `total_*_bn=None`, the response
shape doesn't match the cockpit's mapper. Run:

```powershell
python -m scripts.smoke_zs_api --endpoint snapshot  --debug-shape
python -m scripts.smoke_zs_api --endpoint spot      --debug-shape
python -m scripts.smoke_zs_api --endpoint exposures --debug-shape
```

`--debug-shape` prints the SHAPE (keys + types) of the response —
scalar numbers pass through, every string is reduced to `<str len=N>`,
every list to `<list len=N, first_item_shape=...>`, and any
secret-looking key (`token`, `password`, `service_key`, `authorization`,
`bearer`, `api_key`, `apikey`, `private`, `jwt`) is replaced with
`<REDACTED>`. Safe to share in a bug report.

##### Score-breakdown debugging (Phase 2.7)

Every candidate now carries its full score breakdown plus three meta
fields, plus a `rejection_type` enum and a `weak_components` list. The
ranked-candidates CSV exposes one column per scoring component AND the
breakdown as JSON, so PowerShell can slice it directly:

```powershell
# Quick high-level look at why each candidate landed where it did
Import-Csv .\outputs\latest\ranked_candidates.csv |
  Select-Object side,short_strike,long_strike,credit,score,
                score_gap_to_threshold,weak_components,rejection_type,
                rejection_reasons |
  Format-Table -AutoSize

# All score columns + the JSON breakdown for one candidate
Import-Csv .\outputs\latest\ranked_candidates.csv |
  Select-Object -First 1 | Format-List

# Discover every column the scanner emits today
(Import-Csv .\outputs\latest\ranked_candidates.csv | Select-Object -First 1).PSObject.Properties.Name
```

**`rejection_type` taxonomy** (per-candidate AND per-decision):

| Value | Meaning |
|---|---|
| `selected` | This candidate is the winner — `TRADE_*` decision. |
| `score_below_threshold` | Cleared hard filters; score < `no_trade_score_threshold`. |
| `filter_rejected` | Removed by hard filters before scoring (`rejection_reasons` populated). |
| `no_candidates` | Strategy returned zero candidates (decision-level only). |
| `missing_quotes` | Quote chain didn't cover required structure strikes (decision-level). |
| `missing_structure` | StructureProvider supplied no anchors (decision-level). |

**`weak_components`**: top-2 lowest-scoring sub-components per candidate,
formatted `"name=0.42"`. The helper `weak_components_of()` in
`src/strategies/base.py` filters out the meta keys (`final_score`,
`no_trade_threshold`, `score_gap_to_threshold`) so they never surface as
"weak."

**Anchor-volume observability (Phase 2.8)**: every candidate also carries
`anchor_source` (which VW level was chosen — `put_ceiling_2k` /
`put_ceiling_5k` / `call_floor_2k` / `call_floor_5k`), `anchor_volume`
(the actual volume at that strike), `anchor_volume_source` (one of
`zs_exposure_series` / `quote_provider_fallback`), and
`structure_strength_source` (`zs_volume_series` /
`missing_anchor_volume_neutral` / `no_anchor`).

VW prefers structure-supplied volume over chain-supplied volume — that
fixes the original Phase 2.7 finding where live ZS levels at e.g. 7600
were paired with the QuoteProvider's token volume (100) at synthesized
strikes, producing `structure_strength=0.00` regardless of how strong the
real level was. Under `public_only` (where ZS gives a level via
`wings.*` but no volume), the scanner falls back to chain volume AND
labels the row `quote_provider_fallback` so the audit trail makes the
substitution visible.

When structure has the level but volume is missing entirely, scoring
uses a **neutral 0.5** instead of 0.0 — rationale: the existence of a
ceiling/floor already implies SOME structure, so silently scoring it
zero was a bug, not a policy. That neutral case is tagged
`structure_strength_source = missing_anchor_volume_neutral`.

**`planned_loss_dollars`** = **planned stop risk dollars** under the
session's `default_stop_variant`. Computed as
`credit × (stop_multiple − 1) × 100 × contracts`, capped at the
theoretical max loss. Column name preserved for back-compat with earlier
runs; semantic clarification only.

**`time_decay_headroom` is a documented placeholder** (returns a neutral
0.5 regardless of intraday time-of-day). Its column will become
informative when minutes-to-close is plumbed into the scorer. Today it
counts as 5% of the weighted total, which is small enough that the
placeholder doesn't materially distort decisions — but the breakdown
makes it visible.

> This phase is **observability, not tuning**. We haven't changed any
> weights, haven't moved any thresholds, haven't added or removed any
> components. The next phase will compare scoring output against
> discretionary expectations (Dan's intuition for each candidate) and
> THEN parameterize weights via the session config.

##### Example below-threshold explanation

```
NO_TRADE — best candidate PUT_CREDIT 7550.0/7545.0 @ 0.50 scored 0.4639,
below threshold 0.60 by 0.1361. Weakest components:
credit_to_risk=0.14, maxvol_alignment=0.00.
```

##### Phase 3 — Tastytrade capability probe (read-only)

A separate, read-only scaffold lives at
`scripts/probe_tastytrade.py` + `src/providers/quotes/tasty_probe.py`.
It is **not wired into the scanner** and never submits orders. Its job
is to answer one question per real Tasty account: *"does Tasty give us
everything VW needs for live quotes — auth, accounts, SPX/SPXW chain,
per-strike quotes, DXLink streamer token, multi-leg dry-run?"*

The probe supports **two auth paths** — pick the one your account is set up for:

**Path A — OAuth2 refresh (recommended; long-lived; the path forward)**:

```bash
TASTY_ENV=certification                # production | certification
TASTY_CLIENT_ID=your-oauth-client-id
TASTY_CLIENT_SECRET=your-oauth-client-secret
TASTY_REDIRECT_URI=https://localhost:8000   # informational; only used for one-time bootstrap
TASTY_REFRESH_TOKEN=your-refresh-token       # capture once via the interactive code flow
TASTY_SCOPES=read trade openid               # space- OR comma-separated; both work

# Safety gates (defaults shown — leave them like this)
TASTY_ALLOW_TRADE_SCOPE=true             # allow the `trade` scope to be in the list
TASTY_ENABLE_ORDER_SUBMISSION=false      # HARD gate; Phase 3 never reads this for anything but reporting
```

**Path B — legacy `/sessions` (fallback; Tasty announced sunset)**:

```bash
TASTY_ENV=certification
TASTY_USERNAME=your-tasty-email-or-login
TASTY_PASSWORD=your-tasty-password
```

Both paths share these optional knobs:

```bash
TASTY_ACCOUNT_NUMBER=                 # optional; first account picked if empty
TASTY_USE_DXLINK=false                # true → also fetch /api-quote-tokens (token only, NO websocket)
TASTY_TIMEOUT_SECONDS=10
TASTY_VERIFY_SSL=true
```

(See `.env.example > Phase 3` for the full block + safety notes.)

**Trade scope ≠ execution.** You can have `trade` in `TASTY_SCOPES` because
the OAuth app was registered for future execution — that's fine and
expected. The probe surfaces `trade_scope_present=True` for visibility but
**will not submit orders**. The HARD execution gate is
`TASTY_ENABLE_ORDER_SUBMISSION`, default `false`, and Phase 3 doesn't
expose a submit path at all — even if you flip it to `true`, the probe
class raises `SafetyGateError` from any future `submit_*` call.

**Why not ZS for quotes?** The ZS API has chain endpoints but Phase 3
intentionally treats ZS as **structure-only** (MaxVol, DA-GEX, gamma
regime, PUT_CEILING / CALL_FLOOR / DDOI). Real per-strike bid/ask
quotes belong on a broker provider — that's what Tastytrade is for.
ZS chain quotes are out of scope for this repo.

Safe commands:

```powershell
# Sanitized config dump — confirms your .env wiring. NO HTTP CALL.
# Works without any credentials set.
python -m scripts.probe_tastytrade --config

# Auth check only (OAuth refresh OR /sessions, per env). Sanitized.
python -m scripts.probe_tastytrade --auth-only

# Account list — full account numbers are redacted to ****1234.
python -m scripts.probe_tastytrade --accounts

# Option chain summary — counts + small strike sample, no raw payload.
python -m scripts.probe_tastytrade --chain --symbol SPX

# Quotes for specific strikes (probe synthesizes OCC symbols, then
# GET /market-data/by-type up to 100 symbols).
python -m scripts.probe_tastytrade --quotes --symbol SPX `
    --expiry 2026-06-30 --strikes 5790,5800,5810,5820 --right C

# Full capability matrix — runs all the above + checks DXLink token,
# sandbox detection, and reports yes/no/unknown per capability.
python -m scripts.probe_tastytrade --capabilities --symbol SPX

# Machine-readable variant of any subcommand
python -m scripts.probe_tastytrade --capabilities --symbol SPX --json
```

**What each command proves**:

| Command | Confirms |
|---|---|
| `--config` | `.env` is wired correctly. Lists which credential fields are present (without values), the chosen auth_mode (`oauth` / `legacy_session` / `none`), and the safety-gate state. NO HTTP call — safe to run anytime, even with empty `.env`. |
| `--auth-only` | Tasty creds are valid. Uses OAuth refresh flow when all 3 OAuth fields are set; falls back to legacy `/sessions` otherwise. |
| `--accounts` | The authenticated user owns at least one account; lists redacted ids. |
| `--chain` | `/option-chains/SPX/nested` returns expirations + strikes; reports whether SPX and/or SPXW roots are present and whether today is a 0DTE expiry. |
| `--quotes` | `/market-data/by-type` returns bid/ask/mid/mark for OCC symbols built from `--strikes`. **Phase 3.1**: auto-resolves SPX vs SPXW root from the chain so `--symbol SPX --expiry <0DTE>` works without manually picking SPXW. Pass `--root-symbol SPXW` to override. |
| `--capabilities` | All of the above + a `has_streaming_token` check via `/api-quote-tokens` (when `TASTY_USE_DXLINK=true`). Outputs a capability matrix that includes `trade_scope_present`, `order_submission_enabled`, `execution_blocked_by_safety_gate`, `has_dxlink`, `has_certification_or_sandbox`. **Phase 3.1**: optionally runs a real quote probe when `--capability-expiry` + `--capability-strikes` (+ `--capability-right`) are supplied — `has_quotes` becomes True/False with `quote_probe_count`, `quote_probe_resolved_root_symbol`, `quote_probe_http_status`. |

##### SPX vs SPXW (root auto-resolution)

Tastytrade's chain payload for `SPX` includes **two** roots:

- `SPX` — AM-settled, 3rd-Friday monthlies only.
- `SPXW` — PM-settled, **all** weeklies + 0DTE.

When the probe gets `--symbol SPX --expiry <date>`, it walks the chain
and picks the right root automatically:

| Expiry exists under | Resolved root | `root_resolution_source` |
|---|---|---|
| `SPXW` only (most weekdays, including 0DTE) | `SPXW` | `auto_chain` |
| `SPX` only (3rd Friday monthlies) | `SPX` | `auto_chain` |
| Both | `SPXW` (preferred — daily/PM-settled) | `auto_chain` |
| Caller passes `--root-symbol SPXW` | `SPXW` (no chain lookup) | `explicit` |
| Caller passes `--symbol SPXW` directly and chain confirms | `SPXW` | `direct_match` |
| Neither root lists the expiry | `None` | `unresolved` — error includes `sample_expirations_by_root` so the user can see what's available |

The preferred command on 0DTE:

```powershell
python -m scripts.probe_tastytrade --quotes --symbol SPX --expiry 2026-06-01 `
    --strikes 7550,7560,7570,7580,7590,7600 --right C
# resolved_root_symbol: SPXW
# root_resolution_source: auto_chain
# quote_count: 6
```

Explicit override (skips the chain lookup — useful when you already know the root):

```powershell
python -m scripts.probe_tastytrade --quotes --symbol SPX --root-symbol SPXW `
    --expiry 2026-06-01 --strikes 7550,7560,7570,7580,7590,7600 --right C
# root_resolution_source: explicit
```

##### A note on after-hours quote freshness

When the probe is run after RTH close, Tasty's REST quote
(`/market-data/by-type`) may report values that match the official
EOD close — the `updated-at` timestamp will reflect last trade, not
"now." Quote freshness should be re-validated during an RTH session
before any decision logic depends on it. The probe doesn't try to
distinguish stale-but-EOD-correct from genuinely-stale yet — that's a
Phase 4 concern when the production `TastytradeQuoteProvider` lands.

**What the probe explicitly does NOT do**:

- **Never** POSTs to `/orders` or `/complex-orders` — the live order
  submit paths.
- **Never** opens the DXLink WebSocket — only confirms the token
  endpoint is reachable.
- **Never** prints `session-token`, `remember-token`, `access_token`,
  `password`, `client_secret`, `refresh_token`, `Authorization` header
  values, or full account numbers. Internal `__repr__` / `status()` /
  `config_summary()` are all sanitized.
- **Never** submits a dry-run (no-routing preview) by default — even
  though the endpoint exists. Dry-runs of complex orders will be added
  behind an explicit `--dry-run-vertical` flag after the probe results
  are reviewed.
- `submit_order()` / `submit_complex_order()` raise `SafetyGateError`
  (not generic `NotImplementedError`) — the safety boundary is
  enforced even if a future caller imports the probe class directly.

If you forget the env, the probe exits 0 with a clean warning. No
traceback, no live HTTP attempt.

##### Phase 4 — `TastytradeQuoteProvider` (live REST quotes, no execution)

Phase 4 promotes the probe into a real `QuoteProvider` implementation
that plugs into the existing scanner / Streamlit cockpit:

```powershell
# Default — mock chain, unchanged
python -m scripts.run_scanner --structure-provider zerosigma_api

# Phase 4 — live Tasty REST quotes (REQUIRES TASTY_* OAuth env vars)
python -m scripts.run_scanner --structure-provider zerosigma_api `
                              --quote-provider     tastytrade
```

What it does:

- **Composes** the Phase 3 `TastyProbeClient` for OAuth refresh + REST
  fetch + SPX→SPXW root resolution. Same auth code; same safety gates;
  same redaction.
- Calls `/market-data/by-type` once per scan tick, fetching BOTH C+P
  sides of each strike the strategy asked for via `QuoteRequest`. VW's
  typical 4 strikes × 2 sides = 8 symbols sits well under Tasty's
  100-symbol cap.
- Wraps the result in the exact `OptionChainSnapshot` shape the mock
  provider returns, so strategies don't notice the swap. Resolved root
  + `root_resolution_source` ride on the snapshot for audit.
- Applies **broker-side `QuoteValidation`** per quote — crossed market,
  zero-bid, wide spread (abs + pct), stale age. Failed quotes stay in
  the chain so the cockpit can render them; they carry
  `validation_passed=False` + a short `validation_rejection_reason` so
  CSV/JSONL stays grep-friendly. Thresholds live in `.env`:

```bash
TASTY_QUOTE_MAX_AGE_SECONDS=10       # quote freshness during RTH
TASTY_QUOTE_MAX_SPREAD_PCT=0.50      # (ask-bid)/mid
TASTY_QUOTE_MAX_SPREAD_ABS=5.00      # absolute $ spread
TASTY_REJECT_ZERO_BID=true
TASTY_REJECT_CROSSED_MARKET=true
```

What it does **NOT** do (Phase 4 boundary, same as the probe):

- **No order submission.** Provider does not even define `submit_order`
  / `preview_order` / `place_order` (tests assert this).
- **No order preview / dry-run.**
- **No DXLink WebSocket.** REST polling only — `has_dxlink=false` in
  the live capability matrix.
- **No snapshot worker.** Provider fetches what the scanner asks for,
  nothing more.
- **No whole-chain pulls.** `get_option_chain()` requires
  `request.required_strikes` (the scanner has always passed this since
  Phase 2.6). Calling without it logs a warning and returns `None`.

`QUOTE_PROVIDER` precedence: `--quote-provider` CLI flag → `QUOTE_PROVIDER`
env var → `config/providers.yaml` → `"mock"`. The scanner **fails loudly**
when `tastytrade` is selected without OAuth creds; the Streamlit cockpit
**falls back to mock visibly** with a yellow warning panel so the UI
stays loadable.

Per-candidate observability columns added to `ranked_candidates.csv`:

| Column | Meaning |
|---|---|
| `quote_provider` | `mock` / `null` / `tastytrade` — which provider quoted this candidate |
| `quote_timestamp` | ISO timestamp from the chain |
| `quote_age_seconds` | Now − oldest leg `quote_time`. Surfaces stale fills. |
| `quote_chain_root` | `SPXW` for daily/0DTE; `SPX` for monthlies. None on mock. |
| `quote_root_resolution_source` | `explicit` / `auto_chain` / `direct_match` / `unresolved` |
| `short_validation_passed` / `long_validation_passed` | `True` / `False` / `None` (unvalidated) |
| `short_rejection_reason` / `long_rejection_reason` | short snake_case (`crossed_market`, `zero_bid`, `spread_pct`, `stale`, ...) |
| `quote_validation_passed` | `True` ONLY when BOTH legs passed. `None` when both legs unvalidated. |
| `quote_rejection_reason` | concat of per-leg reasons |

The Streamlit cockpit gains a Quote-Provider selector in the sidebar, a
`root=…` chip in the Provider status panel, a per-candidate `quote`
column (`✓ pass` / `✗ fail` / `—`), and per-leg pass/reason metrics inside
each candidate's expander.

##### Phase 4.1 — audit metadata + target-DTE plumbing

Phase 4.1 is purely additive: defaults are byte-identical to Phase 4. The
goal is to surface enough audit data for a future selector module (Phase 5)
without changing scoring weights or the TRADE / NO_TRADE decision branches.

**Quote validation vs `bid_ask_quality`.** The Phase 4 validator
(`QuoteValidation`) rejects quotes for crossed market / zero bid / wide
absolute spread / wide pct / staleness; results land on
`OptionQuote.validation_passed` + each candidate's `short/long/quote_validation_passed`
CSV columns. Separately, the strategy's existing scoring component
`bid_ask_quality` (computed by `_bid_ask_quality_score` in
`vertical_wing/candidates.py`) uses an absolute $0.20 cap and clips to 0.0
when the worst leg exceeds it. Phase 4.1 adds a `quote_quality_bucket`
column (`good` / `acceptable` / `poor` / `wide` / `invalid` / `unknown`)
that classifies the worst-leg width on $0.10 / $0.20 / $0.50 boundaries
AND respects validator failure. **A wide-but-valid quote can score 0.0 on
`bid_ask_quality` and still appear as `acceptable` or `poor` in the
bucket**, giving the operator a legible per-candidate quality label even
when the scorer's cap clips. (Phase 4.2 will switch the scorer to a
relative-cap; Phase 4.1 just makes today's behavior legible.)

**Score edge + marginal trades.** Set `MIN_SCORE_EDGE` env var (default
`0.02`) to define the score-above-threshold margin you consider non-
marginal. Three new columns:

- `score_edge` = `score - threshold` (signed)
- `score_edge_passed` = `score_edge >= MIN_SCORE_EDGE`
- `marginal_score` = `score >= threshold` AND NOT `score_edge_passed`

A live tick where score=0.6013 and threshold=0.60 stamps `score_edge=0.0013`,
`score_edge_passed=False`, `marginal_score=True`. Phase 4.1 does NOT
change the decision branch — the candidate still gets selected if it's
the best one. Phase 5 selector will decide whether to gate marginal
trades.

**Structured risk rejection.** Both risk-cap filters
(`_f_planned_trade_loss_within_cap`, `_f_theoretical_trade_loss_within_cap`)
now stamp structured fields on `Candidate.meta['risk_rejections']` keyed
by cap name. New CSV columns: `risk_rejection_type` (`planned_loss_cap` /
`theoretical_loss_cap` / `None`), `planned_stop_risk_dollars`,
`planned_stop_risk_cap_dollars`, `planned_stop_risk_pct`,
`planned_stop_risk_passed`, `theoretical_loss_cap_dollars`,
`theoretical_loss_passed`, `risk_rejection_reason`. The existing
human-readable `rejection_reasons` list is **untouched** — Phase 4.1 is
additive.

**Per-candidate audit print.** New scanner flag:

```powershell
python -m scripts.run_scanner --quote-provider mock --dry-run --print-candidates
```

Prints one block per candidate, grouped Identity / Risk / Score / Quote /
Selector, with one `key=value` per line. No truncation of
`score_breakdown_json` or `selector_blockers`. Designed for live-tick
audit when CSV columns are too wide for terminal review. NEVER prints
tokens, Authorization headers, or credentials (asserted in tests).

**Target-DTE expiry selection.** Three new knobs let the scanner request
a future expiry instead of always today's:

```powershell
# CLI
python -m scripts.run_scanner --target-dte 1 --dte-mode trading_days

# env (.env)
TARGET_DTE=0
DTE_MODE=trading_days                  # or calendar_days
ALLOW_AFTER_HOURS_EXPIRY_ROLL=false    # roll +1 day past 16:00 ET when target_dte=0

# YAML (config/scanner.yaml)
scanner:
  expiry:
    target_dte: 0
    dte_mode: trading_days
    allow_after_hours_roll: false
    after_hours_cutoff_et: "16:00"
```

Precedence: CLI > env > YAML > default. **Default `target_dte=0` keeps
behavior byte-identical to Phase 4**, so existing operators see no
change. SPX/SPXW root auto-resolution (Phase 3.1) still works for
`target_dte=1` and `target_dte=2` — the new `tasty_probe.validate_root_hint`
guard catches a stale explicit hint before it OCC-builds against a wrong
root. When the chain has no forward expiry beyond the target, the scanner
returns NO_TRADE cleanly — no traceback.

New decision-log fields (in `snapshot_summary`): `target_dte`, `dte_mode`,
`selected_expiry`, `expiry_selection_source`, `expiry_selection_reason`,
`expiry_root_symbol`, `expiry_days_out`, `available_expiries_count`, plus
an `expiry_override: {from, to, source, reason, root_hint,
structure_expiry_matches_quote_expiry}` block when the chosen expiry
differs from `structure.expiry`.

See `docs/reference_notes.md §11` for the full algorithm + holiday list
(hardcoded 2025-2027 — annual review needed).

##### Phase 4.2 — relative bid/ask quality + strict DTE + clock skew

Phase 4.2 makes **three surgical changes**. Everything else — all other
scoring components, the `bid_ask_quality` **weight** (0.05), the
`no_trade_score_threshold` (0.60), `hard_filters.max_bid_ask_width` (0.20),
and every risk cap — is **untouched**. No execution, no order paths.

**1. Relative-aware `bid_ask_quality` (quote VALIDATION vs quote QUALITY).**
These are two independent things and Phase 4.2 keeps them separate:

- **Quote VALIDATION** (`QuoteValidation`, Phase 4) is the broker's per-leg
  pass/fail: crossed market / zero bid / wide absolute spread / wide pct /
  staleness. It lands on `OptionQuote.validation_passed` and the
  `short/long/quote_validation_passed` CSV columns. **Unchanged in 4.2.**
- **Quote QUALITY** is the strategy's `bid_ask_quality` sub-score. In 4.1
  it used a blunt **absolute $0.20 cap** and clipped to 0.0 whenever the
  worst leg's spread exceeded $0.20 — so a Tasty quote that *passed*
  validation could still score `bid_ask_quality=0.00` while its
  `quote_quality_bucket` (then on absolute $ bins) read `poor`. The score
  and the bucket disagreed.

4.2 replaces the absolute cap with a **relative pct-of-mid** scorer in a new
pure module `src/utils/quote_quality.py`. The **same cutoffs drive BOTH the
score and the bucket**, so they can never contradict again:

| worst-leg pct-of-mid | score | bucket |
|---|---|---|
| ≤ 3% (`BID_ASK_GOOD_PCT`) | 1.0 | `good` |
| > 3% … ≤ 7% (`BID_ASK_ACCEPTABLE_PCT`) | linear 0.8 → 0.6 | `acceptable` |
| > 7% … ≤ 15% (`BID_ASK_POOR_PCT`) | linear 0.5 → 0.2 | `poor` |
| > 15% | 0.0 | `wide` |
| None / negative | 0.0 | `unknown` (no leg width) |
| crossed / missing leg | 0.0 | `invalid` |
| any leg failed validation | 0.0 | `invalid` (validator wins) |

The live case that motivated this: a worst leg **$0.20 wide on a ~$3.10 mid
= 6.45% of mid**. Under the old absolute cap → 0.0. Under relative mode →
**~0.63** (bucket `acceptable`). The `quote_quality_bucket` semantics
deliberately **migrated from absolute-$ bins to pct-of-mid bins**.

Legacy absolute scoring is still available as an opt-in:
`BID_ASK_QUALITY_MODE=absolute` (with `BID_ASK_MAX_ABS_CAP=0.20` for exact
4.1 parity — the default cap is **1.00**, NOT 0.20). When a leg has no usable
mid, the scorer auto-falls back to absolute so a missing mid never silently
zeroes an otherwise-valid quote.

New `Candidate.meta` keys + CSV columns: `bid_ask_quality_mode`
(`relative`|`absolute`), `bid_ask_quality_reason`. (The score reuses the
existing `bid_ask_quality` column; the bucket reuses `quote_quality_bucket`.)

**2. Strict target-DTE.** By default the scanner *falls back* to the nearest
forward expiry when the requested `target_dte` isn't in the broker chain.
`--strict-target-dte` (or `STRICT_TARGET_DTE=true`, or
`scanner.expiry.strict_target_dte: true`) instead forces **NO_TRADE** rather
than silently trading the fallback — the row gets a
`strict_target_dte_unavailable` selector blocker and the decision explanation
says so. No traceback. An **exact** target_dte match (e.g. today's expiry at
`--target-dte 0`) is *not* a fallback, so strict mode is a no-op there.
`pick_target_expiry` is unchanged.

**Phase 4.2.1 — pre-fetch quote-request guard.** The scanner now decides
whether it can even *ask* for quotes before calling the provider. Two
conditions short-circuit to a clean **NO_TRADE** *without* calling the quote
provider at all:

- **No required strikes** — the structure produced no anchor strikes to price
  (e.g. a premarket / public-only ZS read). The scanner logs
  `quote request skipped: no required strikes available` (a **warning**, not an
  error) and emits NO_TRADE with `quote_request_skipped_reason=no_required_strikes`,
  `required_strikes_available=false`, and `selector_blockers` ⊇
  `[no_required_strikes, insufficient_structure]`.
- **Strict target-DTE unavailable** — enforced here, *before* the fetch, with
  `quote_request_skipped_reason=strict_target_dte_unavailable`.

This is why `TastytradeQuoteProvider` is never handed an empty
`required_strikes` request: the REST provider correctly **refuses whole-chain
pulls**, and the scanner no longer mistakes that refusal for a provider
failure. The Tasty whole-chain safety boundary is unchanged — the scanner
simply doesn't send an unservable request. A `None` chain returned *after* a
real request (auth/transport/unresolved-root) is still a genuine error.
Both skip paths still write `decision_log.jsonl` (rich `snapshot_summary`) and a
header-only `ranked_candidates.csv`; `--print-candidates` prints a
`QUOTE REQUEST SKIPPED` audit block.

**3. Clock-skew clamp.** A negative oldest-leg `quote_age_seconds` means the
quote timestamp is *ahead* of the scanner clock (provider/scanner skew). 4.2
clamps the emitted age to **0.0** and surfaces `quote_clock_skew_detected` +
`quote_clock_skew_seconds`. `QUOTE_AGE_CLOCK_SKEW_TOLERANCE_SECONDS`
(default 2.0) only *labels* within- vs beyond-tolerance magnitude — both
clamp to 0.0. The broker validator's positive-age staleness rejection is
**byte-identical** (negative/tiny skews never trigger a stale rejection).

Six CSV columns are **APPENDED at the tail** (existing indices preserved):
`bid_ask_quality_mode`, `bid_ask_quality_reason`, `quote_clock_skew_detected`,
`quote_clock_skew_seconds`, `strict_target_dte`, `strict_target_dte_passed`.
The audit print and Streamlit per-candidate expander surface them; the
decision-log JSONL auto-rides them via `Candidate.meta`.

**Example commands:**

```powershell
# Mock chain (tight relative spreads → decision matches the 4.1 baseline)
python -m scripts.run_scanner --structure-provider zerosigma_api `
  --quote-provider mock --target-dte 1 --print-candidates

# Tasty, today's expiry (relative quality scoring on real quotes)
python -m scripts.run_scanner --quote-provider tastytrade `
  --target-dte 1 --print-candidates

# Tasty, demand DTE=2 strictly — NO_TRADE if only a fallback expiry exists
python -m scripts.run_scanner --quote-provider tastytrade `
  --target-dte 2 --strict-target-dte --print-candidates
```

**Mock-data note.** The four legs of the two default-selected mock spreads
(strikes 5780/5785/5815/5820) carry a tighter `bid_ask_width=0.02`. A flat
$0.10 spread on a sub-$1 OTM long leg (e.g. 5820 `c_mid=0.50` → 20% of mid)
is an unrealistically *wide* relative market that the recalibrated scorer
correctly scores 0.0/`wide`; tightening those legs keeps the mock smoke
invariant ("at least one CALL_CREDIT + one PUT_CREDIT is tradeable") intact.
All mids/volumes/OI and every other strike's width are unchanged.

See `docs/reference_notes.md §12` for the bid/ask config table, the
clock-skew clamp rule, and the strict-DTE reason string.

##### Phase 5 — daily trade selector (SELECTION ONLY — no execution)

The **daily selector** chooses **at most one** candidate (configurable via
`MAX_TRADES_PER_DAY`) from the candidates a strategy already generated, scored,
and filtered. It is a layer *after* the strategy's own `select()` — it marks
`selected_trade=true` on the chosen row and explains every decision. **It never
executes, submits, previews, or places orders.** The strategy's decision is
preserved as `pre_selector_decision`; the selector's outcome is
`post_selector_decision`.

Pure module: `src/selector/daily_selector.py` (operates on candidate row dicts;
imports no strategy package).

**Modes** (`DAILY_TRADE_SELECTOR` / `--daily-selector`):

| Mode | Picks | Tie-breakers |
|---|---|---|
| `score_best_valid` *(default)* | highest score | credit → farthest distance |
| `best_credit_valid` | highest credit | score → farthest distance |
| `closest_wing_valid` | shortest distance-from-spot (riskier — not default) | score → credit |
| `farthest_wing_valid` | greatest distance-from-spot | score → credit |
| `call_credit_only` | best eligible CALL_CREDIT (else NO_TRADE) | score → credit |
| `put_credit_only` | best eligible PUT_CREDIT (else NO_TRADE) | score → credit |
| `lowest_breach_risk_valid` | transparent composite: farther + lower `planned_stop_risk_pct` + acceptable credit (emits `selector_score_components`) | — |
| `regime_aligned_valid` | best eligible when `gamma_regime` ∈ {positive, neutral}; `negative` → blocked; missing → `insufficient_regime_data` | score → credit |
| `balanced_structure_premium_valid` *(Phase 9G — dynamic both sides)* | evaluates BOTH CALL_CREDIT + PUT_CREDIT and picks the better side on a transparent combined score (premium + distance safety + structure + MaxVol/gamma + quote + existing score − planned-risk penalty), min-max normalized WITHIN the eligible set — never highest-premium-only, never farthest-distance-only; emits `selector_score_components` + a human `selector_explanation` | combined total → score → distance |
| `no_trade` | nothing (always NO_TRADE) | — |

**Eligibility** (all `*_valid` + side-only modes): never selects a `rejected`
candidate; requires `selector_eligible_base` (default) and the
`candidate_passes_{trade,risk,quote,score_threshold}` buckets; `REQUIRE_QUOTE_VALIDATION`
excludes quote-invalid; `REQUIRE_SCORE_EDGE` excludes marginal/no-edge; the side
filters (`ALLOW_CALL_CREDIT` / `ALLOW_PUT_CREDIT`; both false → `no_sides_allowed`)
and the `MIN/MAX_SELECTOR_{SCORE,CREDIT,DISTANCE_FROM_SPOT}` thresholds apply.
When nothing qualifies the selector returns **NO_TRADE** with a
`selector_no_trade_reason` and keeps every candidate visible (it never hides
rejected rows).

**Config** (`config/scanner.yaml → scanner.selector`, env, or CLI; precedence
CLI > env > YAML > default):

```
DAILY_TRADE_SELECTOR=score_best_valid   MAX_TRADES_PER_DAY=1
ALLOW_CALL_CREDIT=true                  ALLOW_PUT_CREDIT=true
REQUIRE_SELECTOR_ELIGIBLE_BASE=true     REQUIRE_QUOTE_VALIDATION=true
REQUIRE_SCORE_EDGE=false                NO_TRADE_ON_SELECTOR_CONFLICT=true
MIN_SELECTOR_SCORE=  MIN_SELECTOR_CREDIT=  MIN_SELECTOR_DISTANCE_FROM_SPOT=  MAX_SELECTOR_DISTANCE_FROM_SPOT=
LOWEST_BREACH_RISK_{DISTANCE,CREDIT,RISK}_WEIGHT=1.0/0.25/1.0
```

CLI flags: `--daily-selector`, `--max-trades-per-day`,
`--allow-call-credit`/`--no-allow-call-credit`,
`--allow-put-credit`/`--no-allow-put-credit`, `--require-score-edge`,
`--min-selector-score`, `--min-selector-credit`.

New CSV columns (appended at the tail): `daily_selector_mode`, `selected_trade`,
`selector_rank`, `selector_reason`, `selector_rejection_reason`, `selector_score`,
`selector_score_components`, `selector_tiebreaker`, `side_allowed_by_config`,
`selector_config_summary`, `max_trades_per_day`, `selector_conflict_detected`,
`selector_no_trade_reason`. The decision log gains `daily_selector_mode`,
`pre_selector_decision`, `post_selector_decision`, `selected_trade`, and a full
`selector_result` (per-candidate metadata + after-selector pick).
`--print-candidates` adds a `--- daily selector ---` block per candidate plus a
tick-level `=== DAILY SELECTOR ===` summary. The Streamlit cockpit gains a
selector-mode dropdown, a `selected` column, and a selection caption.

**Example commands** (selection only — no orders):

```powershell
python -m scripts.run_scanner --structure-provider zerosigma_api `
  --quote-provider mock --target-dte 1 --daily-selector score_best_valid --print-candidates

python -m scripts.run_scanner --structure-provider zerosigma_api `
  --quote-provider tastytrade --target-dte 1 --daily-selector best_credit_valid --print-candidates

python -m scripts.run_scanner --structure-provider zerosigma_api `
  --quote-provider tastytrade --target-dte 1 --daily-selector call_credit_only --print-candidates
```

The default `score_best_valid` matches prior behavior (the highest-score
eligible candidate is chosen) while now stamping explicit selector fields.

##### Phase 6 — strategy run-profiles (CONFIG / PERSISTENCE ONLY — no execution)

Save a named, versioned bundle of scanner/selector settings as a YAML file and
run the scanner from it instead of a long flag string. **This is configuration
persistence only — no execution, no orders, no forward runner loop.** Profiles
reference providers by NAME; **secrets stay in `.env`, never in a profile.**

Profiles live in `profiles/` (committed example profiles, all `enabled: false`,
`stub` structure + `mock` quotes):

```
profiles/vertical_wing_score_best_1dte.yaml   # score_best_valid, 1DTE
profiles/vertical_wing_call_only_1dte.yaml    # call_credit_only, puts off
profiles/vertical_wing_best_credit_1dte.yaml  # best_credit_valid, min credit 0.50
profiles/vertical_wing_no_trade.yaml          # observe-only (no_trade)
```

**Schema** (`src/config/strategy_profiles.py`, versioned + validated): required —
`profile_id, profile_name, version, enabled, strategy_id, strategy_type, symbol,
structure_provider, quote_provider, target_dte, strict_target_dte, daily_selector,
max_trades_per_day, allow_call_credit, allow_put_credit,
require_selector_eligible_base, require_quote_validation, require_score_edge,
min_selector_score, min_selector_credit, min_selector_distance_from_spot,
max_selector_distance_from_spot, risk_profile, notes, created_at, updated_at`;
optional strategy params (loaded, not all wired this phase) — `wing_threshold,
spread_width, entry_window_start, entry_window_end, no_trade_score_threshold,
min_credit, max_planned_stop_risk_dollars, max_theoretical_loss_dollars`. Enums
(`structure_provider`, `quote_provider`, `daily_selector`) are validated;
`execution_mode`/secret keys are **rejected**.

**Manage profiles** (`scripts/manage_profiles.py` — config only, no execution):

```powershell
python -m scripts.manage_profiles --list
python -m scripts.manage_profiles --show vertical_wing_score_best_1dte
python -m scripts.manage_profiles --validate vertical_wing_score_best_1dte
python -m scripts.manage_profiles --validate-all
python -m scripts.manage_profiles --copy SRC_ID NEW_ID [--force]
python -m scripts.manage_profiles --create-template NEW_ID [--force]
```
`--copy` / `--create-template` refuse to overwrite an existing file without `--force`.

**Run the scanner from a profile** (`--profile <id|path>`):

```powershell
python -m scripts.run_scanner --profile vertical_wing_score_best_1dte --print-candidates
python -m scripts.run_scanner --profile profiles/vertical_wing_call_only_1dte.yaml --quote-provider mock --print-candidates
python -m scripts.run_scanner --profile vertical_wing_best_credit_1dte --quote-provider tastytrade --print-candidates
```

**Precedence: `CLI > profile > env > YAML/default`.** A `--profile` value supplies
defaults for every scanner knob it carries; any explicit CLI flag still wins (e.g.
the third command overrides the profile's `mock` with `tastytrade`).

> **Flag rename:** `--profile` now selects a *strategy run-profile* (Phase 6). The
> former risk-profile flag is **`--risk-profile`**. For back-compat, a `--profile`
> value that isn't a strategy profile but matches a known risk-profile name is
> still treated as the risk profile (with a log note). Risk-profile precedence:
> `--risk-profile` > the run-profile's `risk_profile` field > back-compat > YAML active.

**Run-profile hash.** Each profile has a deterministic `profile_hash` (sha256 of
the profile content, **excluding** `created_at`/`updated_at`/`profile_path` so
cosmetic re-saves don't churn it). The hash + `profile_id`/`profile_name`/
`profile_version`/`profile_path`/`profile_loaded`/`config_source_summary` are
stamped into `ranked_candidates.csv`, the `decision_log.jsonl` snapshot, and the
scan logs — so a later backtest/forward run can prove which exact profile produced
a signal. The Streamlit sidebar shows a read-only run-profile selector that
prefills the daily-selector default.

This run-profile layer **feeds the next phase: a forward (start/stop) local
paper-monitoring runner — still NOT execution.**

##### Phase 7 — forward runner (LOCAL PAPER MONITORING — no execution)

`scripts/run_forward.py` repeatedly runs the **existing scanner** (it calls
`scripts.run_scanner.main(...)` in-process — the *same* code path, no duplicated
logic) from a saved Phase 6 run-profile and records each tick into a local
ledger. **Monitoring + ledger only — it never places orders, submits paper
orders, calls order preview, or executes anything. No broker call exists in this
runner** (`no_execution: true`, `execution_mode: disabled_local_monitoring`).

```powershell
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --dry-run
python -m scripts.run_forward --profile vertical_wing_no_trade --once --interval-seconds 1
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --max-ticks 2 --interval-seconds 1
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --market-hours-only
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --output-dir outputs/forward
```

`--once` = exactly one tick; `--max-ticks N` stops after N; default interval 60s
(`--interval-seconds 0` = no sleep); **Ctrl+C stops cleanly** (manifest →
`stopped`, exit 0); `--dry-run` validates the profile + prints the planned config
and writes a `dry_run` manifest **without scanning** (no tick/signal logs).
Safe passthrough overrides: `--quote-provider`, `--structure-provider` (CLI beats
the profile, same as the scanner). An unknown profile exits cleanly (`2`); a
scanner failure is logged and sets manifest `status=error` (exit nonzero).

**Ledger** (under `--output-dir`, default `outputs/forward/`, gitignored):

```
outputs/forward/runs/{run_id}/
    run_manifest.json    # run_id, profile_{id,name,hash,path}, started/ended_at,
                         #   status (running|stopped|error|completed|dry_run),
                         #   interval_seconds, max_ticks, dry_run, providers,
                         #   daily_selector, target_dte, no_execution=true,
                         #   execution_mode=disabled_local_monitoring,
                         #   git_commit, python_version, platform
    tick_log.jsonl       # one line per tick: status, scanner_return_code,
                         #   scanner_decision, pre/post_selector_decision,
                         #   selected_trade, selected_candidate_summary,
                         #   selector_no_trade_reason, duplicate_selected_signal,
                         #   output_files, error
    signal_log.jsonl     # one line per NEW selected trade (provenance + the
                         #   ranked_candidates.csv trade/quote/risk fields)
    selected_trades.csv  # same, CSV
    no_trade_log.jsonl   # one line per no-selected-trade tick (reason + blockers)
    heartbeat.json       # latest status / tick / decision / selected_trade
    scanner/             # the scanner's own per-tick outputs (audit)
outputs/forward/latest/  # mirror of the most recent run's manifest + heartbeat
```

**Duplicate-signal protection (ledger, not execution):** within one run, signal
identity = `profile_hash + symbol + selected_expiry + side + short_strike +
long_strike + target_dte + trade_date`. A repeat of the same identity is **not**
appended again to `signal_log`/`selected_trades.csv`; the tick is still logged
with `duplicate_selected_signal=true`.

**Market-hours guard:** `--market-hours-only` skips scanning outside RTH
(09:30–16:00 ET, weekdays — simple rule, no holiday calendar yet) and logs the
tick with `status=skipped_market_closed`. Default off (deterministic tests).

The Streamlit cockpit shows a **read-only** "Forward runs (monitoring)" section
(enhanced in Phase 8). **The next phase is dashboard start/stop controls and/or a
backtest adapter — NOT live execution.**

##### Phase 8 — forward run review + control UX (READ-ONLY — no execution)

Phase 8 makes forward runs easy to inspect. **Review/control UX only — no
execution, no broker/paper orders, no order preview, and the UI never launches or
stops a process** (it only displays commands to copy).

Inspection lives in the pure module `src/forward/review.py` (discover runs, load
manifest/heartbeat/tick/signal/no-trade/selected-trades, summarize a run — all
tolerant of missing/empty files). The Phase 7 runner now also writes
`outputs/forward/latest/latest_run_pointer.json` so `latest` resolves robustly.

**Review CLI** (`scripts/review_forward.py` — read-only; `RUN_ID` may be a run id
or the alias `latest`):

```powershell
python -m scripts.review_forward --list [--limit N]
python -m scripts.review_forward --latest
python -m scripts.review_forward --run RUN_ID
python -m scripts.review_forward --signals RUN_ID [--limit N]
python -m scripts.review_forward --no-trades RUN_ID [--limit N]
python -m scripts.review_forward --ticks RUN_ID [--limit N]
python -m scripts.review_forward --export-summary RUN_ID --output outputs/forward/summary_RUN_ID.json
```

A run summary reports: `run_id`, profile (`id`/`name`/`hash`), `status`,
`started/ended_at`, `interval_seconds`, and the counts `tick_count`,
`signal_count`, `duplicate_signal_count`, `no_trade_count`, `error_count`, plus
`latest_tick_time` / `latest_decision` / `latest_selected_trade` /
`latest_no_trade_reason` / `latest_heartbeat_status` and the selected-trade
summaries. A missing run exits non-zero with a helpful message (no traceback).

**Run a safe local forward session, then review it:**

```powershell
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --interval-seconds 60 --market-hours-only
python -m scripts.run_forward --profile vertical_wing_score_best_1dte --once
python -m scripts.review_forward --latest
python -m scripts.review_forward --signals latest --limit 10
python -m scripts.review_forward --no-trades latest
python -m scripts.review_forward --ticks latest --limit 20
```

**Streamlit "Forward runs (monitoring)" section** (read-only): latest-heartbeat
caption, a run-selector dropdown over discovered runs, the five count metrics,
tables of selected signals / no-trade reasons / latest ticks, the run-folder path,
and a copy-only code block of the `run_forward`/`review_forward` commands. **No
start/stop buttons, no subprocess launch, no process management** — that's a later
phase. Inspecting forward runs never reads or prints secrets.

##### Phase 9A — local forward-runner process control (start / stop / status — NO execution)

Phase 9A lets you start, stop, and inspect a **local background forward-run
process** from one CLI. This is **local process control only**: it launches and
manages a `scripts.run_forward` *monitoring* loop. It does **not** place orders,
submit paper orders, preview orders, select a broker account, reconcile
positions, or auto-execute anything. Every control-state file carries
`no_execution: true` and `execution_mode: disabled_local_monitoring`.

State lives under `outputs/forward/control/` (override with `--forward-root`):

| File | Purpose |
|---|---|
| `forward_runner.pid` | PID of the launched background runner |
| `control_state.json` | active / pid / run_id / profile (`id`/`name`/`hash`) / command / `started_at` / `last_seen_at` / `status` / `latest_heartbeat_path` / `latest_manifest_path` / `no_execution` / `execution_mode` |
| `stop_requested.json` | graceful-stop sentinel the runner polls each tick |
| `logs/{ts}_{profile}.out.log` / `.err.log` | captured stdout/stderr of the background runner |

`status` is the source of truth and **reconciles** the stored state against a
real, **non-destructive** PID-liveness probe (Windows `OpenProcess` +
`GetExitCodeProcess`; POSIX `os.kill(pid, 0)` — never `psutil`, never a signal
that could disturb the process). A stored "running" state whose PID is dead is
reported as `stale` (not `running`); when there is no state at all the status is
`stopped`.

**Control CLI** (`scripts/control_forward.py` — Windows PowerShell, no admin):

```powershell
# Inspect current control status (safe; reconciles PID liveness)
python -m scripts.control_forward status

# Print the exact, safe run command WITHOUT launching anything (copy/paste)
python -m scripts.control_forward command --profile vertical_wing_score_best_1dte --interval-seconds 60 --market-hours-only

# Launch a DETACHED background runner (refuses if one is already live)
python -m scripts.control_forward start --profile vertical_wing_score_best_1dte --interval-seconds 60 --market-hours-only
python -m scripts.control_forward start --profile vertical_wing_score_best_1dte --once
python -m scripts.control_forward start --profile vertical_wing_score_best_1dte --max-ticks 5 --interval-seconds 60

# Graceful stop (writes stop sentinel; runner exits cleanly at next tick)
python -m scripts.control_forward stop

# Graceful stop + terminate ONLY the stored PID if still alive
python -m scripts.control_forward stop --force

# Remove pid/state/stop files — ONLY after confirming the PID is not alive
python -m scripts.control_forward cleanup-stale
```

`start` uses the **same Python/venv** as the CLI (`sys.executable`) and launches a
detached background process (Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`;
POSIX `start_new_session`). It refuses to start a second runner while one is live.
`stop` is graceful first — it writes `stop_requested.json`, which the runner polls
at the top of each tick and then exits with manifest `status: stopped`. `--force`
additionally terminates **only** the PID recorded in `control_state.json`, and
only if that PID is alive — it never scans for or kills anything else.
`cleanup-stale` removes the control files **only** after confirming the stored PID
is not alive (it refuses, loudly, if the process is still running). No control
command ever reads or prints secrets or `.env` contents.

**`run_forward` integration** (additive — standalone behavior is unchanged when
absent): `--control-state-path PATH` makes the runner write live progress
(`run_id`, `status`, `last_seen_at`, latest heartbeat/manifest paths, latest
decision/selected-trade) into the shared control state; `--stop-file PATH` makes
the runner check for the stop sentinel each tick and exit cleanly
(`status: stopped`) when it appears. With neither flag, `run_forward` behaves
exactly as in Phase 7/8 and writes no control state.

**Streamlit "Forward runs (monitoring)" section** also shows a **read-only**
control block: Runner / Active / PID metrics, a `stale` warning when the stored
PID is dead, and a copy-only code block of the `control_forward`
`status`/`command`/`start`/`stop`/`cleanup-stale` commands. **There are no
start/stop buttons in the UI** — it never launches or kills a process; it only
displays commands to copy. Live broker execution remains deferred to a later
phase.

##### Phase 9B — multi-strategy local paper trade lifecycle + P&L (NO execution)

Phase 9B turns SELECTED scanner signals into **simulated** credit-spread trades,
monitors them across forward ticks, applies take-profit / stop-loss / end-of-day
exits, tracks realized + unrealized P&L, and runs **multiple saved strategy
profiles together** in one local paper portfolio.

This is **local paper accounting only.** No broker orders. No Tasty order
preview. No broker paper orders. No live execution. No historical backtesting
(that's Phase 10). Every portfolio ledger stamps `no_execution: true` /
`execution_mode: local_paper_lifecycle_only`, and a test greps the order/broker
vocabulary out of all five new files.

**Modules:** `src/paper/models.py` (`PaperTrade` record + `PaperLifecycleConfig`),
`src/paper/lifecycle.py` (the engine), `src/paper/ledger.py` (portfolio ledger +
reconciliation). The engine **reuses** the existing P&L math in
`src/paper/manual_tracker.py` — it does not re-derive it.

**P&L convention (credit spread):**
- `entry_credit` is **positive** (cash received to open).
- the current value to close is a **positive debit**.
- `unrealized_pnl = (entry_credit − current_debit) × 100 × contracts`
- `realized_pnl   = (entry_credit − exit_debit)    × 100 × contracts`
- `max_profit = entry_credit × 100 × contracts`
- `max_loss (magnitude) = (spread_width − entry_credit) × 100 × contracts`

The **mark** (current debit) is re-priced each tick from that tick's
`ranked_candidates.csv`: an open trade is matched by `(side, short_strike,
long_strike, selected_expiry)` and the spread mid `= short_mid − long_mid` becomes
the mark (`spread_bid = short_bid − long_ask`, `spread_ask = short_ask −
long_bid`). If no current quote is available the update is flagged
`quote_unavailable_no_exit` and the trade is **not** exited (unless the EOD rule
fires).

**Exit rules (per tick):**
- **TP** — close when `current_debit ≤ entry_credit × PAPER_TAKE_PROFIT_PCT` (0.50 → 50% of credit).
- **SL** — close when `current_debit ≥ entry_credit × PAPER_STOP_LOSS_PCT` (1.50 → 150% of credit).
- **EOD** — close when ET time `≥ PAPER_EOD_EXIT_TIME` (15:55) and `PAPER_EXIT_ON_EOD=true`; fires even if a quote is unavailable (closes at the last-known mark).
- `exit_reason ∈ take_profit | stop_loss | eod_exit | manual_mark_closed | quote_unavailable_no_exit | error`.

**Duplicate / conflict rules** (events `duplicate_skipped` / `blocked_by_limits`,
machine-readable reasons): identity `= profile_hash | symbol | selected_expiry |
side | short_strike | long_strike | target_dte | trade_date`. A new trade is
skipped if that identity is already open; portfolio limits
(`PAPER_MAX_OPEN_TRADES_TOTAL`, `PAPER_MAX_OPEN_TRADES_PER_PROFILE`,
`PAPER_ALLOW_MULTIPLE_OPEN_PER_PROFILE`, `PAPER_ALLOW_DUPLICATE_STRIKES`) block
the rest.

**Configuration** comes from env (`PAPER_*`), CLI flags, or a `--profiles-file`
`lifecycle:` block (precedence: **CLI > profiles-file > env > default**). It is
deliberately **not** part of the Phase 6 run-profile schema — a strategy profile
never carries execution/lifecycle intent.

**Run a local paper portfolio (Windows PowerShell):**

```powershell
python -m scripts.run_portfolio_forward --profiles vertical_wing_score_best_1dte,vertical_wing_no_trade --once
python -m scripts.run_portfolio_forward --profiles-file config/portfolio_profiles.yaml --interval-seconds 60 --market-hours-only
python -m scripts.run_portfolio_forward --profiles A,B --max-ticks 5
```

Output lands under `outputs/portfolio_forward/runs/{portfolio_run_id}/` (mirrored
to `latest/`): `portfolio_manifest.json`, `portfolio_tick_log.jsonl`,
`profile_tick_log.jsonl`, `paper_trades_open.csv`, `paper_trades_closed.csv`,
`paper_trade_events.jsonl`, `portfolio_summary.json`, `heartbeat.json`,
`reconciliation_report.json`, and each profile's `scanner/{profile_id}/` outputs.

**Local reconciliation** (no broker) cross-checks the local ledgers and flags
`open_trade_missing_open_event`, `closed_trade_still_in_open_file`,
`duplicate_open_identity`, and invalid status transitions; the report carries
`broker_position_reconciliation: "deferred"`.

**Review the portfolio (read-only):**

```powershell
python -m scripts.review_portfolio_forward --latest
python -m scripts.review_portfolio_forward --list
python -m scripts.review_portfolio_forward --open latest
python -m scripts.review_portfolio_forward --closed latest
python -m scripts.review_portfolio_forward --events latest --limit 20
python -m scripts.review_portfolio_forward --reconcile latest
```

The Streamlit "Portfolio forward (paper lifecycle)" section surfaces the latest
heartbeat, open/closed trade tables, the P&L summary, the event log, and the
reconciliation report — **read-only, with no execution or broker controls.**

##### Phase 9C — ZerσSigma Algo Cockpit UI refresh + Strategy Builder + safe controls

Phase 9C re-skins the Streamlit app into a dark, ZerσSigma-branded **tabbed
command-center** (no dominant sidebar), adds a full **Strategy Builder** (Phase 6
profile CRUD) and **safe local runner controls** (start/stop/status buttons over
the Phase 9A process controller). **UI / profile-management only — no trading-logic
changes, no broker execution, no orders, no order preview.**

**Launch the cockpit (Windows PowerShell):**

```powershell
streamlit run .\src\app\streamlit_main.py
# or
python -m streamlit run .\src\app\streamlit_main.py
```

**Layout** — the ex-sidebar selectors move to a top **⚙ Controls & providers**
expander; everything else lives in six tabs:

| Tab | Contents |
|---|---|
| 🛰 **Live Cockpit** | provider status · market/structure cards · ranked candidates + per-candidate breakdown · daily selector · decision |
| 🧱 **Strategy Builder** | list/clone/create/edit/validate/save Phase 6 run-profiles |
| ▶ **Forward Runner** | control status + **Start / Stop / Cleanup / Refresh** buttons · forward heartbeat/summary · signal/no-trade/tick tables |
| 💼 **Portfolio Paper** | paper lifecycle review (open/closed/P&L/events/reconciliation + a realized-P&L bar chart) · manual paper desk + equity curve |
| 🗒 **Logs / Review** | EOD summary · session-config debug |
| ⚙ **Settings** | session risk overrides · read-only paper-lifecycle (`PAPER_*`) config |

Styling lives in the **pure** `src/app/ui_helpers.py` (`brand_css()` + card/pill/
format helpers; palette adapted from the Dashboard theme — dark `#0b0f14`,
electric-green `#00E5A8`, blue `#2d6cff`). No new dependencies; charts use built-in
`st.line_chart` / `st.bar_chart`.

**Strategy Builder** (`src/app/profile_builder.py`, pure): browse a table of
`profiles/*.yaml`; pick **New** (from a safe template), **Edit**, or **Clone**;
edit every Phase 6 field grouped by section; **Validate & compute hash** surfaces
the deterministic `profile_hash` (or the validation errors); **Save** writes
`profiles/{profile_id}.yaml` but **refuses to overwrite** unless you tick
*"overwrite existing profile"*. Validation rejects `execution_mode` + credential
keys, so **no secrets or execution intent can enter a profile**. Paper-lifecycle
knobs are intentionally **not** part of the Phase 6 schema — they show read-only
under Settings (sourced from env / CLI / `config/portfolio_profiles.yaml`).

**Safe runner controls** (`src/app/control_ui.py`, guards over the Phase 9A
`control` module): the Forward Runner tab shows reconciled status + **Refresh /
Start / Stop / Cleanup stale** buttons. **Start** uses the selected profile +
interval / once / max-ticks / market-hours settings and **refuses to launch a
second runner** when one is already active. **Stop** requests a *graceful* stop
first; a separate **⚠ Force stop** checkbox (off by default) is required before a
force-terminate of the stored PID. Every control surface is labeled **LOCAL
MONITORING ONLY — NO BROKER EXECUTION**, and copy-paste terminal equivalents are
shown alongside the buttons.

##### Phase 9D — cockpit UX polish + clearer operational workflow

Phase 9D makes the cockpit tighter, more readable, and easier to operate. **UX +
operational-workflow only — no scanner / selector / quote / lifecycle / risk
changes, no broker execution.** New pure helpers live in
`src/app/cockpit_helpers.py` (formatting, spot fallback, provider defaults, log
export, review prompt).

**Launch:** `streamlit run .\src\app\streamlit_main.py` (or
`python -m streamlit run .\src\app\streamlit_main.py`).

- **Realistic provider defaults.** Structure defaults to `zerosigma_api` and
  quotes to `tastytrade` **when configured** (detected via env-var *presence*
  only — never secret values), otherwise the sandbox provider; `mock`/`stub`/
  `null` are labeled *(sandbox)* / *(manual marks)*. Execution stays
  `local_paper` — **NO BROKER EXECUTION**.
- **Spot fallback.** If the quote spot is missing (or `0.0`), the Market panel
  shows `structure.spot` with a `Zσ structure` source badge instead of a blank.
  When the chain is unavailable it prints a compact reason + suggested actions
  (try during RTH, switch to `mock`, check Tasty auth).
- **Compact formatting.** DA-GEX renders `4.18B` / `735M`; strikes/walls/floors
  as plain numbers; spot `7,609.78`; P&L `$1,234.56`. Tighter metric cards, less
  padding, and a top **operational status strip** (run profile · structure ·
  quote · runner · selected trade · open paper · realized P&L · NO BROKER
  EXECUTION badge).
- **Run Strategy.** The Forward Runner tab is now a *Run Strategy* panel: select a
  profile, **👁 Preview scan once**, **▶ Start / ■ Stop / 🧹 Cleanup / 🔄 Refresh**,
  the **exact copyable command**, current control status, the latest decision, and
  open-paper P&L at a glance.
- **Strategy Builder vs Session & Paper Settings.** The Builder explains *"profiles
  are saved strategy recipes…"*; **Settings** is renamed **Session & Paper
  Settings** with *"…affect this local session… do not rewrite saved profiles
  unless you save one in Strategy Builder."* Advanced fields are tucked behind
  **Advanced selector filters / expiry controls / risk fields / strategy params**
  expanders. **Strict target DTE** is renamed **"Require exact DTE match"** (with a
  tooltip) under Advanced expiry controls.
- **Logs / export.** Download buttons for the latest forward `tick_log.jsonl` /
  `signal_log.jsonl` / `no_trade_log.jsonl` and portfolio `paper_trade_events.jsonl`
  / `portfolio_summary.json` / `reconciliation_report.json`, plus a **Copy review
  prompt** block (forward run / selection / quote / no-trade / P&L language). Files
  missing → graceful empty state. (No in-app LLM — export/review helpers only.)
- **Portfolio Paper** leads with **open trades + unrealized P&L**, then closed /
  realized / total, events, and reconciliation, with clear empty states ("No open
  paper trades. Start a portfolio forward run or wait for a selected signal.") and
  setup steps when no run exists.

##### Phase 9E — Operator Mode + Zσ Strat Tester + first-class symbols

Phase 9E makes the cockpit operable without a law degree: a **Simple/Advanced
mode** toggle, a much simpler **Strategy Builder**, the **Zσ Strat Tester** rename,
and **first-class ticker/symbol selection**. **UX + symbol/profile wiring only — no
trading-logic / scanner / selector / quote / lifecycle changes, no broker
execution.** Pure helpers live in `src/app/operator_mode.py`.

**Data engines (conceptual split):** **ZerσSigma API = the *exposure* engine**
(DA-GEX / VEX / DEX / CEX / TEX, gamma regime, walls / floors, MaxVol / DDOI,
exposure context). **Tastytrade = the *market-data* engine** (quotes, option chain,
bid/ask/mid/mark, volume, open interest, contract metadata). In prominent UI copy
the structure provider is the **Exposure source** and the quote provider is the
**Market data source** (internal names + CLI flags unchanged).

- **Simple Mode (default ON).** Hides advanced fields; shows the core workflow:
  pick/create a strategy → choose ticker → DTE → side preference → selector style →
  data source → risk profile → save → preview → start a local paper test → review
  P&L. *"Simple Mode gets you running. Advanced Mode exposes filters, exact DTE
  behavior, quote validation rules, and selector constraints."*
- **Strategy Builder (simple first).** Simple fields — Profile name, Ticker,
  Strategy type, Target DTE, **Side preference** (Both / Calls only / Puts only /
  Observe only), **Selector style** (Best score / Best credit / Conservative
  (lowest breach risk) / No trade), **Data source** (Live / Sandbox), Risk profile.
  They map to existing Phase 6 fields (`allow_call_credit` / `allow_put_credit` /
  `daily_selector` / `structure_provider` / `quote_provider`). Advanced Mode shows
  the full field set behind **Advanced selector filters / expiry controls / risk
  fields / strategy params** expanders. "Require exact DTE match" lives only under
  *Advanced expiry controls* (no "Strict DTE" wording anywhere visible).
- **Data source simplification.** Simple Mode replaces the provider dropdowns with
  one choice: **Live: ZerσSigma exposures + Tasty market data** (→ `zerosigma_api`
  + `tastytrade`) or **Sandbox: Stub exposures + Mock market data** (→ `stub` +
  `mock`). Advanced Mode keeps the explicit Exposure-source / Market-data-source
  dropdowns.
- **First-class symbol.** Type any ticker (uppercased, default `SPX`). It's saved
  into the profile's `symbol` and flows to the scanner/runner via profile loading
  (`run_forward` / `run_portfolio_forward` read the symbol from the profile). A
  compact **symbol-health panel** distinguishes four things: *symbol accepted*,
  *Tasty market data available*, *ZerσSigma exposures available*, and *strategy
  eligible*, with a clear reason when not. **Not every ticker has ZerσSigma
  exposure coverage** — Tasty may serve quotes for a symbol that has no ZerσSigma
  exposures; **Sandbox prices SPX regardless of the symbol entered.**
- **Zσ Strat Tester.** The former "Forward Runner" tab, reframed as a 5-step paper
  test: select profile → **👁 Preview strategy** → **▶ Start paper test** → **■ Stop
  test** → review. Shows active profile / symbol / data source / runner status /
  latest decision / open paper P&L + the **NO BROKER EXECUTION** badge; terminal
  commands move to an *Advanced / terminal commands* expander.
- **Logs.** Operator-friendly labels (**Strategy test log**, **Selected trades
  export**, **No-trade reasons export**, **Paper trade events**, **Portfolio
  summary**, **Reconciliation report**); raw filenames show only in Advanced Mode.

##### Phase 9F — final operator pass + Zσ Strat Builder + Strategy Stats + Dashboard-style controls

Phase 9F is the clean operator pass before live use: a header-first layout, the
**Zσ Strat Builder** rename with preset info cards, a **Strategy Stats & Review**
tab, a sandbox-vs-live symbol-health fix, button/copy cleanup, and
**Dashboard-matched control styling**. **UI / copy / layout only — no trading
logic changes, no broker execution.** New pure helpers live in
`src/app/operator_mode.py` + `src/app/cockpit_helpers.py`.

- **Header-first layout.** The branded **ZerσSigma Algo Cockpit** header now renders
  at the very top, with the Simple/Advanced toggle in the header strip (no longer
  clipped) and the *Controls & data source* expander *below* it. Subtitle:
  *"Scanner · Zσ Strat Builder · Zσ Strat Tester · Paper Portfolio · Strategy Stats"*
  (no "forward runner"). Safety badges (`LOCAL · NO BROKER EXECUTION`,
  `exec: local_paper`) kept.
- **🧱 Zσ Strat Builder.** Renamed tab + page. Pick a **preset** → an **info card**
  explains it (symbol, strategy, DTE, side, selector style, data source, risk
  profile, *what it's designed to test*, enabled, safety) with friendly
  descriptions for the four committed profiles (+ a generic fallback). Clear
  **Create new / Edit selected / Clone selected** buttons (no radio-first); the
  profile table is tucked into an expander.
- **🧪 Zσ Strat Tester.** A clearer 5-step flow; **Start local paper test** /
  **Preview strategy** / **Stop test** / **Clear stale runner** / **Refresh
  status**; an obvious warning when a runner is already active (Start/Preview
  disabled with a plain reason); "No active profile selected" when blank.
- **📊 Strategy Stats & Review** (was Logs / Review). Latest-run summary +
  historical stats aggregated from existing flat files (runs, ticks, selected
  signals, paper trades, win/loss, realized/unrealized P&L, common no-trade
  reasons, latest EOD best candidate) with a *"More stats will appear after
  additional local paper runs"* empty state; friendly download labels (+ EOD
  summary); review prompt.
- **Symbol-health fix.** In **Sandbox** the panel reads *Tasty market data: sandbox
  mock · ZerσSigma exposures: sandbox stub · Strategy eligible: sandbox eligible*
  with a "Sandbox uses SPX mock/stub data regardless of ticker" note — no more
  alarming "unavailable/unavailable" when stub/mock is intentionally selected.
  **Live** mode still reports real availability + a reason when not eligible.
- **Manual desk / Settings copy.** "Record manual paper trade" (+ "local records
  only… do not sync with Tastytrade or any brokerage"); "Apply local session
  settings"; Settings note clarifies saved profiles change only from Zσ Strat
  Builder.

**Dashboard-style controls.** Shared CSS in `ui_helpers.brand_css()` matches the
ZerσSigma Dashboard: primary actions are bright **green pills**
(`linear-gradient(135deg,#00e5a8,#81ffd8)`), secondary/restrained-danger are
**dark outlined**, disabled buttons dim to `opacity .42`, and **selectboxes are
pill-styled with the typing-caret hidden** (`caret-color: transparent` +
`cursor: pointer`) so they read as dropdowns, not text fields. *Streamlit
limitation:* its select is a baseweb component (not a native `<select>`), so the
caret/cursor are tamed rather than fully replaced — keyboard accessibility is
preserved.

##### Phase 10C — full trader UX audit + Simple-Mode cleanup + after-hours DTE + backtests

A trader-first pass over Simple Mode (UI only — no strategy/selector/risk/backtest logic changed, no
execution surface added):

- **No dev jargon in Simple Mode.** Candidate cards split into a clean Simple view (Setup · Score ·
  Credit · Quote Status · Risk Status · Blocker · Anchor · Anchor volume · Distance) and an Advanced-only
  raw view (threshold / gap / score_edge / quote bucket / b/a quality / clock skew / Phase 4.x notes /
  raw `st.json`). New friendly labels in `operator_mode` (`anchor_label`, `candidate_quote_status_label`,
  `candidate_risk_status_label`, `candidate_blocker_label`).
- **"Runner" → "Test Status".** The tester card reads **Test Status** / **Active paper test**; "Clear
  stale test"; force-stop + PID are Advanced-only (force-stop = "⏹ Force stop local test process").
- **Corridor explainer.** "Corridor is active only when the 10K call floor is below spot AND the 10K put
  ceiling is above spot — CW1 (10K call floor) < Spot < PW1 (10K put ceiling)." Labels relabeled
  *10K call floor (CW1)* / *10K put ceiling (PW1)*.
- **After-hours DTE preview.** `resolve_preview_dte(now_et, profile_dte, mode)` previews 1DTE for a 0DTE
  profile after 17:00 ET (pre-midnight), back to 0DTE next session. Live Cockpit + Run Strategy show a
  "🌙 after-hours preview … Profile DTE 0DTE / Preview chain 1DTE" banner; the on-demand Tasty quote
  diagnostic defaults to the rolled DTE. **The profile / paper-test / backtest DTE are never mutated.**
- **Strategy Builder.** "Validate strategy" → **Check Strategy Setup** (+ "does not run or trade"
  explainer); the **enabled** checkbox is now "Show in main strategy list" and actually curates the
  Simple list (with an all-disabled fallback so it's never empty); the data-source radio is **Profile
  default data source** with a *Current run source* caption + a mismatch warning (the app source wins).
- **📈 Backtests tab (new, 7th).** Builds the exact read-only CLI
  (`python -m scripts.backtest_run --symbol SPX --profile all-main --latest-days 20 --dte 0 …`) and a
  **Refresh Latest Results** reader of `outputs/backtests/latest` → Trades / Win Rate / Total P&L / Max
  Drawdown / TP-SL-EOD cards + a by-profile table. *"Uses local saved snapshots only. No live API calls.
  No broker execution. No order preview."* (Backtests are not launched from the UI — a run can take
  minutes; the heavy main-chain 1DTE re-fetch is deferred.)

##### What's still missing under `public_only`

| Field | Still None under public_only? | How to populate |
|---|---|---|
| `put_ceiling_2k` / `call_floor_2k` | populated from `wings.*` | already works |
| `put_ceiling_5k` / `call_floor_5k` | yes | switch to `bearer`/`login`/`service_token` + `ZS_API_ENABLE_EXPOSURE_SERIES=true` + active subscription |
| `maxvol` | populated from `max_*_vol_strike` fallback | already works |
| `gamma_flip` | populated from `gamma.flip` | already works |
| `call_wall` / `put_wall` | populated from `max_*_oi_strike` | already works |
| `ddoi_pin` | yes | requires `/exposure/ddoi` (subscription + `DO_SPACES_*` on the server) |

Optional:

```
ZS_API_TIMEOUT_SECONDS=10
ZS_API_VERIFY_SSL=true
ZS_API_MAX_RETRIES=3
ZS_API_ENABLE_EXPOSURE_SERIES=true    # /exposure/series requires subscription
ZS_API_ENABLE_DDOI=false              # /exposure/ddoi requires subscription + Spaces
ZS_STRUCTURE_PROVIDER=zerosigma_api   # default selection at startup
```

If `ZS_STRUCTURE_PROVIDER=zerosigma_api` but auth env vars are missing, the
factory falls back to `stub` and surfaces a clear warning in the cockpit.
Tokens, passwords, and service keys are NEVER displayed or logged.

#### Endpoints consumed by the read-only provider

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/v1/market/snapshot?symbol=SPX` | public | spot + aggregated exposures (`total_gex_bn`, `da_gex_bn`, `vex`, `dex`, `cex`) |
| `GET /api/v1/exposure/series?symbol=SPX&metric=volume&mode=split` | subscription | per-strike call/put volumes → derives `PUT_CEILING_{2K,5K}`, `CALL_FLOOR_{2K,5K}`, `maxvol` |
| `POST /api/v1/auth/login` or `/auth/service-token` | n/a | auth handshake; cached JWT used as `Authorization: Bearer …` |

`gamma_regime` is derived from `sign(da_gex_bn)`. Fields not exposed by the
current ZS API contract (`gamma_flip`, `call_wall`, `put_wall`, `ddoi_pin`)
land as `None` and are listed in `snapshot.raw["missing_fields"]` for audit.

### Provider separation (Phase 1.5)

The cockpit deliberately separates structure context from quote pricing.
The two providers live behind independent interfaces and the strategy is
the only layer that sees both at once:

```
┌───────────────────────────┐    ┌─────────────────────────────────┐
│ StructureProvider          │    │ QuoteProvider                    │
│  → StructureSnapshot       │    │  → OptionChainSnapshot           │
│     · MaxVol               │    │     · spot                       │
│     · DA-GEX / gamma       │    │     · OptionQuote per strike     │
│     · PUT_CEILING(2K/5K)   │    │       (bid, ask, mid, volume,    │
│     · CALL_FLOOR(2K/5K)    │    │        OI, optional Greeks)      │
│     · DDOI pin             │    │     · quote_ts                   │
│     · structure_ts         │    │  → SpotQuote (back-compat)       │
└────────────┬───────────────┘    └─────────────┬───────────────────┘
             │                                  │
             ▼                                  ▼
        Strategy.generate_candidates(structure, chain, params)
                     → filter → score → select → decide
```

Both providers in Phase 1 read from the same canonical mock dataset
(`src/providers/_mock_data.py`) so they stay in agreement without
importing each other — production providers will pull from independent
real services (ZS API for structure, broker API for quotes).

### What's mock / stubbed (Phase 1.5)

| Layer | Phase 1.5 implementation | Phase 2+ plan |
|---|---|---|
| `StructureProvider` | `StubStructureProvider` — structure context only (MaxVol, DA-GEX, gamma regime, DDOI pin, PUT/CALL 2K/5K levels). No chain quotes. | `ZeroSigmaApiStructureProvider` against `/api/v1/market/*` and `/api/v1/exposure/*` (stubbed, raises NotImplementedError). |
| `QuoteProvider` | `MockQuoteProvider` (default, deterministic SPX chain), `NullQuoteProvider` (manual marks), and **Phase 4** `TastytradeQuoteProvider` (live Tasty REST, opt-in via `QUOTE_PROVIDER=tastytrade` or `--quote-provider tastytrade`). | DXLink-streaming Tasty provider, plus other brokers when needed. |
| `ExecutionProvider` | `disabled` / `local_paper` / `manual_trade_tracking`. **No live orders.** | `broker_paper`, `manual_confirm`, `live_tiny`, `live` — stubbed today, raise NotImplementedError. |

**Next step:** wire the real `ZeroSigmaApiStructureProvider` against
`/api/v1/market/*` + `/api/v1/exposure/*`. Provider boundaries are now
clean — that integration only needs to populate `StructureSnapshot` /
`ExposureContext` from JSON; it does NOT touch the quote-side flow.

### Adding a new strategy

1. Drop a new package under `src/strategies/<strategy_name>/` exposing a class that implements `src/strategies/base.py::Strategy` (the protocol with `generate_candidates`, `score`, `select`, `explain`).
2. Add a YAML entry in `config/strategies.yaml` with `module`, `class`, `enabled: true`, and any `default_parameters` / `editable_parameters` / `required_data_fields`.
3. That's it — the cockpit's sidebar selector picks it up at next launch; `scripts/run_scanner.py` will exercise it in the loop; the decision log gets an entry per tick.

No app / provider / risk / reporting / storage code imports a specific strategy. The registry is the only seam.

---

## Configuration

All runtime behavior is config-driven. No magic in code.

| File | Purpose |
|---|---|
| `.env` | Machine-specific secrets and base URLs (gitignored). |
| `config/strategies.yaml` | Strategy registry: which strategies exist, parameters, enabled flag. |
| `config/risk_profiles.yaml` | **Session-start risk templates** (see below). Editable at runtime in the cockpit. |
| `config/providers.yaml` | Provider wiring: structure source, quote source, execution mode. |
| `config/scanner.yaml` | Scan windows, polling cadences, hard-filter thresholds. |

### Risk model (planned vs theoretical)

Every credit-spread candidate carries **two independent risk numbers**:

| Concept | Formula (per spread) | What it answers |
|---|---|---|
| **Theoretical max loss** | `spread_width − credit` | "What if the spread goes fully ITM with no stop?" |
| **Planned stop risk** | `credit × (stop_multiple − 1)`, capped at theoretical | "What do I intend to lose if my stop fires?" |

Both are converted to dollars via `× 100 × contracts` and each has a
separate cap on the active risk profile:

- `max_planned_trade_loss_percent` / `..._dollars` — primary "can I take this?" gate
- `max_theoretical_trade_loss_percent` / `..._dollars` — hard ceiling on full defined risk

**Worked example** (5-wide vertical, $0.80 credit, 5 contracts, `SL_150_PERCENT_LOSS`):
- Theoretical max loss: `(5.00 − 0.80) × 100 × 5 = $2,100`
- Planned stop risk: `((0.80 × 2.5) − 0.80) × 100 × 5 = $600`

Under the `aggressive_paper_10k` template (planned cap 10% = $1,000;
theoretical cap 30% = $3,000) this trade passes both gates.

`BASELINE_CASH_SETTLE` (no stop) **falls back to theoretical max loss** for
the planned-risk gate — safer than waving the trade through.

### Risk profiles are session defaults, not hardcoded

`config/risk_profiles.yaml` ships two templates:

- `aggressive_paper_10k` (default — Dan's current 5-lot, $10K paper sizing)
- `conservative_paper_10k` (1-lot, tighter caps)

The Streamlit cockpit will (in a later phase) let the user **edit every
session field** before the scanner starts — starting balance, contracts per
trade, daily-loss caps, planned/theoretical trade-loss caps, spread width,
stop variant, profit targets, max open positions, no-trade score threshold,
scan windows, preferred entry windows, minimum credit, max bid/ask width,
minimum distance from spot. Every edit will be logged to
`outputs/runs/{date}/config_change_log.jsonl`. The YAML files are loaded as
**defaults / templates** — not as immutable production rules.

See [`plan.md`](plan.md) for the full architecture and roadmap, and
[`docs/reference_notes.md`](docs/reference_notes.md) for read-only notes on the
ZerσSigma data contracts this cockpit consumes.

---

## Adding a new strategy

Strategies are completely pluggable. To add one:

1. Create a folder `src/strategies/<your_strategy>/` with at minimum a
   `strategy.py` that exports a class implementing the
   [`Strategy`](src/strategies/base.py) protocol
   (`generate_candidates`, `score`, `select`, `explain`).
2. Split candidate construction and scoring into `candidates.py` and
   `scoring.py` if it helps (Vertical Wing does this — see
   [`src/strategies/vertical_wing/`](src/strategies/vertical_wing/) as a
   reference implementation).
3. Register the strategy in
   [`config/strategies.yaml`](config/strategies.yaml):

   ```yaml
   strategies:
     your_strategy_v1:
       display_name: "Your Strategy v1"
       enabled: true
       module: "src.strategies.your_strategy.strategy"
       class:  "YourStrategyV1"
       symbol: "SPX"
       default_parameters: { ... }
       editable_parameters: [ ... ]
       required_data_fields: [ ... ]
   ```

4. That's it. The Streamlit selector, the scanner, the decision log, the
   risk filters, and the paper tracker all pick it up automatically.

No framework code needs to change to add or remove a strategy. If you
ever find yourself editing `app/`, `providers/`, `risk/`, `reporting/`,
or `storage/` to support a specific strategy — stop. That coupling
belongs inside the strategy module.

---

## Project layout

```
zerosigma-algo/
├── README.md            # this file
├── plan.md              # architecture, phases, open questions
├── notes.md             # append-only running notes
├── .env.example         # machine-specific config template
├── pyproject.toml       # deps + tooling
├── config/              # YAML configs (strategies, risk, providers, scanner)
├── docs/                # design notes, reference docs
├── scripts/             # entry-point scripts
├── src/                 # application code
│   ├── app/             #   Streamlit cockpit
│   ├── strategies/      #   strategy registry + per-strategy modules
│   ├── providers/       #   structure / quotes / execution adapters
│   ├── risk/            #   limits + hard filters
│   ├── paper/           #   paper-account + manual-trade tracking
│   ├── reporting/       #   EOD summary, decision log
│   ├── storage/         #   path resolution, CSV append helpers
│   └── utils/           #   config loading, time, logging
├── outputs/             # generated artifacts (gitignored)
│   ├── latest/          #   most recent snapshot
│   ├── runs/            #   per-run CSV/JSONL logs
│   └── daily/           #   end-of-day summaries
└── tests/               # pytest suite
```

---

## Strategy presets + adjustable TP/SL (Phase 9G)

The cockpit ships a **dynamic-first** preset stack. **Dynamic side-selection
presets are the PRIMARY live presets**; the call-only presets are explicit
**controls** so you can measure what dynamic side-selection adds.

| Preset | Kind | Side policy | When | TP / SL |
|---|---|---|---|---|
| `morning_5k_dynamic_tp75` | dynamic | both sides (balanced) | 10:55–11:05 ET | TP 75% · SL 150% |
| `morning_2k_dynamic_no_tp` | dynamic | both sides (balanced) | morning, 2K | no TP · SL 150% |
| `eod_5k_dynamic_sl150_no_tp` | dynamic | both sides (balanced) | target 15:15 ET | no TP · SL 150% |
| `eod_5k_dynamic_sl200_no_tp` | dynamic | both sides (balanced) | target 15:15 ET | no TP · SL 200% |
| `morning_5k_call_tp75_control` | control | call only | morning, 5K | TP 75% · SL 150% |
| `morning_2k_call_no_tp_control` | control | call only | morning, 2K | no TP · SL 150% |
| `eod_5k_call_sl150_no_tp_control` | control | call only | target 15:15 ET | no TP · SL 150% |
| `eod_5k_call_tp50_control` | control | call only | target 15:15 ET | TP 50% · SL 200% |
| `regime_put_credit_test` | regime | put only | morning, 5K | no TP · SL 150% |
| `observe_dynamic_5k` | observe | both sides, never trades | morning, 5K | — |

All ship **SAFE**: stub exposures + mock market data + `enabled: false`. To go
live, switch the **Exposure source → ZerσSigma** and **Market data source →
Tasty** in the cockpit and enable the profile.

**Dynamic side selection** uses the `balanced_structure_premium_valid` selector,
which scores BOTH the call-credit and put-credit candidate each tick on a
transparent, normalized blend of *premium, distance safety, structure, MaxVol/
gamma alignment, quote quality, existing score* minus a *planned-risk penalty* —
then picks the better side and explains why (e.g. *"Selected CALL_CREDIT because
it had stronger structure, acceptable credit, safer distance from spot than the
PUT_CREDIT alternative"*). It is never "highest premium wins" or "farthest
distance wins", and it is deterministic.

**Adjustable TP/SL** lives on the profile (`stop_loss_pct` / `take_profit_pct`
+ modes) and is editable from the Builder Simple Mode (SL 150/200/custom, TP
None/50/75/custom). **Wiring status:** TP/SL and dynamic-exit settings are saved
as profile metadata and shown in the info card, but the **paper lifecycle still
applies the `PAPER_*` env values** at test time — per-profile TP/SL execution and
dynamic exits are **configured but not active yet (deferred)**. No paper P&L math
changed.

**Zσ Strat Tester** wording was cleaned up: **Scan every** (how often the local
paper tester checks for a new signal), **Stop after scans** (Advanced only),
**Running: Yes/No**, and a friendly **Latest test run** label (e.g. *"Vertical
Wing · Jun 2 · 10:31 PM"*) — the full run id + PID live under *Advanced details*.

---

## Operator decision layer + structure depth (Phase 9H)

The Live Cockpit opens with an **Operator read** panel that translates raw
structure into plain English (it never invents data — a missing field reads
*unavailable*):

- **Structure Read** — spot vs primary gamma, nearest wing, regime.
- **Trade Bias** — what the gamma regime implies (pinning vs directional).
- **Candidate Risk** — proximity to the nearest wing, accelerated by negative gamma.
- **Best Eligible Setup** — the top eligible candidate this scan (or *none*).
- **Why / Why Not** — the one-line rationale.

> *"Primary gamma sits at 7600 with secondary gamma at 7570. Spot is below primary
> gamma and near the 7550 floor. Negative gamma regime means structure may be less
> pinning and moves can accelerate."*

**Prime structure cards** are now Spot · Gamma regime · DA-GEX · MaxVol · **Primary
gamma** · **Secondary gamma**. Primary/secondary gamma map from the ZS payload
`gamma.cluster_primary` / `cluster_secondary`; when those are absent the UI derives
a display-only primary/secondary from the gamma walls/flip nearest spot (labelled
*derived*), and says *unavailable* when nothing is present.

**DDOI was removed from the prime cockpit** (it is not in the public ZS payload, so
it was always blank). It now appears only under **Advanced structure / raw
diagnostics**, and only when a value is present.

**Wing Stack** shows the structural levels by volume threshold:

| | 2K | 5K | 10K |
|---|---|---|---|
| Put ceilings | ✓ | ✓ | ✓ (needs ≥10k-volume series) |
| Call floors | ✓ | ✓ | ✓ (needs ≥10k-volume series) |

plus the **nearest wing**, the **primary wing** (strongest available tier nearest
spot), and the signed distance from spot to each. The **10K tier** is derived the
same way as 2K/5K (strike where volume crosses 10,000) and populates from the live
subscription volume series; sandbox/mock data peaks well below 10k, so 10K reads
`—` there with an explanatory note.

**Profiles are grouped by purpose** in Simple Mode — *Primary live paper tests*
(dynamic) → *Controls* → *Research / Observe* → *Legacy* — defaulting to Primary;
Advanced Mode exposes every profile. The Zσ Strat Tester also flags a **profile ↔
latest-run mismatch**: if the latest completed test came from a different profile
than the one selected, it warns you to start a fresh test rather than reading stale
results as if they belonged to the selected profile.

Backtest prep (Phase 10) landed as a plan + read-only scaffold:
`docs/phase10_backtest_plan.md`, `src/replay/`, and
`python -m scripts.discover_replay_data` — replay reuses the **same** structure
mapping (`build_snapshot_from_payload`) and the same scanner/selector/lifecycle path,
so there is no backtest fork.

---

## Trader cockpit cleanup (Phase 9I)

Simple Mode is now a clean trader cockpit; Advanced Mode keeps the developer
detail.

- **Data source is never ambiguous.** The Zσ Strat Tester shows the **App data
  source** (top controls) and a **Data source for this run** panel that reconciles
  it against the selected profile — Data source · Exposure source · Market data
  source · **Status** (ready / warning / unavailable). If the app and profile
  disagree it warns: *"Selected profile is configured for Sandbox, but app controls
  are Live…"*. Simple Mode runs on the app source (explicit); Advanced Mode offers a
  *Use app / Use profile* toggle.
- **Quotes say WHY they're unavailable.** Instead of a vague "chain unavailable",
  you get a concise reason — *market closed or stale Tasty chain*, *Tasty returned no
  chain for the selected expiry/root*, *Tastytrade is not configured*, *provider set
  to manual marks* — with raw provider state under an Advanced expander. Unknown
  causes never overclaim: *"provider returned no usable chain."*
- **Less debug clutter in Simple Mode.** Advanced structure / raw diagnostics (and
  DDOI) are Advanced-only; `python -m scripts …` terminal blocks are Advanced-only
  (Simple Mode is button-driven); the Manual Paper Desk is hidden in Simple Mode.
- **Strategy dropdown shows only Main Strategies** (the dynamic-first presets) with a
  *Show comparison and legacy profiles* checkbox. Categories are **Main Strategies /
  Comparison Tests / Research · Disabled / Legacy · Archived**.
- **Stats & Review has charts.** Equity curve, drawdown curve, daily P&L,
  P&L-by-profile, exit-reason and selected-signals breakdowns, plus a **max
  drawdown** metric (with % when a starting balance is set). Empty data shows *"More
  stats will appear after additional local paper runs."*
- **EOD summary is one click.** A prominent **Generate / Refresh EOD summary** button
  shows the last-generated timestamp + a ⚠ stale / ✅ up-to-date badge, and safely
  auto-generates once when it's stale on open (no background loop, local outputs
  only).
- **Latest run vs selected profile stays honest.** The Tester flags a mismatch when
  the latest completed test came from a different profile; the friendly run label
  (*"Vertical Wing · Jun 2 · 10:31 PM"*) leads, the raw run id stays in Advanced.

### Backtest data discovery

`python -m scripts.discover_backtest_sources` (read-only; roots from `--root` →
`ZSA_TRADING_ROOT` → `~/Dropbox/Trading`, **no hardcoded username**) locates the
real exposure data for Phase 10: the SPX `SPX_RAW_*.csv` per-strike files and the
Wingonomics outputs. Wingonomics detects 10K wings by the *same* volume-threshold
rule this repo uses, so it's the validation ground-truth — see
`docs/phase10_backtest_plan.md §13`. We consume it; we never run or modify it.

---

## Wing Dominance Score — WDS (Phase 9J)

A 10K wing is **not** automatically strong. Dan's wing logic scores how *dominant*
the wing (W1) is versus the adjacent next strike (W2):

```
WSR = W2_volume / W1_volume          (side-specific volume)
WDS = 1 - WSR                        (higher = cleaner / more dominant)
```

- **CALL floor:** W1 = lowest strike with CALL volume ≥ 10,000; W2 = one strike
  *lower*.
- **PUT ceiling:** W1 = highest strike with PUT volume ≥ 10,000; W2 = one strike
  *higher*.
- **Tiers:** WDS ≥ 0.75 = **Tier 1** (clean/dominant), 0.50–0.75 = **Tier 2**
  (usable), 0.30–0.50 = **Tier 3** (mixed/caution), < 0.30 = **Tier 4** (weak).
- Displayed as a percent: *"WDS: 82% — Tier 1"*. A weak wing reads *"10K wing is
  weak because adjacent strike volume is 82% of W1."*

The Live Cockpit leads with the **dominant WDS wing as the primary structure** — but
only when the wing **corridor is active** (see the next section). It explicitly frames
the nearest 2K/5K wing as *immediate breach risk, not the primary structure*:

> *"Structure status: Active corridor. Dominant wing is PUT_CEILING 10K at 7600 with
> WDS 58% — Tier 2 (usable)."*

True WDS needs both W1 and W2 volume; when W2 is missing it is never invented —
*"10K wing exists, but true WDS is unavailable because the adjacent W2 volume is
missing from the current payload."* WDS is **display-only**; weighting the selector by
WDS is deferred. (This matches the real `wingonomics.py` wing selection; Wingonomics
itself does not compute WDS — we add it per Dan's spec.)

---

## Wing corridor validity — CW1 < Spot < PW1 (Phase 10A)

A wing structure is only **active** when the call floor is below spot **and** the put
ceiling is above spot — i.e. spot sits *inside* the corridor:

```
corridor active  ⇔  CW1 (call floor 10K)  <  Spot  <  PW1 (put ceiling 10K)
```

A call floor priced **above** spot is not a floor; a put ceiling **below** spot is not a
ceiling. When the corridor is not formed, the cockpit says so plainly and does **not**
promote any wing to "active dominant structure":

> *"Structure status: Inactive — corridor not formed. CALL_FLOOR 10K at 7600 is above
> spot, so it is not acting as the active floor. Raw WDS for CALL_FLOOR 10K at 7600 is
> 69% (raw context only — NOT active structure)."*

The raw WDS is still computed (useful context) but clearly labelled raw/inactive, the
dominant side reads `unavailable`, and the nearest local wing is breach risk only. The
corridor status (`✅ Active` / `⛔ Inactive`), CW1/Spot/PW1, and the active-or-raw WDS all
show in the **Wing Stack** panel. The rule lives in one pure helper
(`cockpit_helpers.wing_corridor_status`) reused by the live cockpit and the backtester.

---

## Local historical backtester (Phase 10A)

Phase 10A maps Dan's saved per-strike exposure CSVs into the **same** structure + chain
objects the live path uses — **no separate strategy, no broker, no order preview, no live
API calls**. It is the data-mapping foundation for the Phase 10B replay runner.

```powershell
# 1) Discover what data exists (SPX/SPY/QQQ, 0DTE + 1DTE), read-only
python -m scripts.discover_backtest_sources --symbols SPX SPY QQQ --include-1dte

# 2) Map ONE entry snapshot for one symbol/date and read out the structure
python -m scripts.backtest_dry_run --symbol SPX --profile morning_5k_dynamic_tp75 --latest --entry 11:00

# 3) Map the entry snapshot for every date in a range -> repo-local CSV (one row/date)
python -m scripts.backtest_scan_dates --symbol SPX --profile eod_5k_dynamic_sl150_no_tp `
    --start 2026-05-01 --end 2026-06-03 --entry 15:15 --limit 10
```

- **Multi-symbol:** SPX, SPY, QQQ (each `<SYM>_RAW_*.csv` under `TOS Data/Daily
  Exposures/<SYM>`); 1DTE is **discovered but deferred** (full 1DTE logic is future).
  SPY/QQQ wing thresholds default to the SPX 2K/5K/10K and are flagged provisional
  (symbol-specific calibration is Phase 10B).
- **Same mapping, no fork:** raw rows go through the SHARED `map_payload_to_snapshot`
  (2K/5K/10K wings + Phase 9J W2/WDS) and a bid/ask `OptionChainSnapshot`, then the same
  profile + selector shapes.
- **Entry windows:** Morning `11:00` (10:55–11:05), EOD `15:00/15:15/15:30` (±15/±30);
  the closest snapshot in-window wins, ties prefer at-or-after.
- **Corridor recorded:** every scan row carries `corridor_valid / cw1 / pw1 /
  corridor_reason / raw_wds / active_wds`, so corridor state is auditable per date.
- **Outputs are repo-local:** only under `outputs/backtests/latest/` and
  `outputs/backtests/runs/<stamp>_<label>/` — **never** inside the raw `TOS Data` folders.
  (`OUTPUT_DIR` / `DATA_DIR` relocate the `outputs` root if set.)

---

## Historical replay runner (Phase 10B)

Phase 10B runs the saved snapshots through the **same live path** — the real
`VerticalWingV1` strategy, the live risk filters, and the Phase 5 selector
(`select_daily_trade`, including `balanced_structure_premium_valid`) — and then
**simulates** the TP/SL/EOD exit from the post-entry snapshots. **No strategy fork, no
broker, no order preview, no Tastytrade, no ZerσSigma live API.**

```powershell
# One profile across a date range
python -m scripts.backtest_run --symbol SPX --profile morning_5k_dynamic_tp75 `
    --start 2026-01-01 --end 2026-06-03 --dte 0 --run-label test

# All 4 primary dynamic profiles over the most recent 20 dates
python -m scripts.backtest_run --symbol SPX --profile all-main --latest-days 20 `
    --dte 0 --run-label smoke

# Add the call-only / put-regime / observe controls
python -m scripts.backtest_run --symbol SPX --profile all-main --include-controls `
    --latest-days 20 --dte 0 --run-label smoke_with_controls
```

- **Reuses the live selector, no fork:** candidates come from the real
  `generate_candidates` (CALL_CREDIT short at PUT_CEILING, PUT_CREDIT short at CALL_FLOOR,
  2K/5K tier from the profile), the live `apply_filters` risk caps gate them, and
  `select_daily_trade` picks the side — the backtest only adapts the candidate into the
  selector's row shape via the live `compute_readiness`.
- **Exit simulation** (matches the reference backtest): mid-to-mid repricing after entry;
  **TP** when debit ≤ `(1 − capture) × credit` (TP75 → 25%, TP50 → 50%); **SL** when
  debit ≥ `(1 + loss) × credit` (SL150 → 2.5×, SL200 → 3.0×); first event wins (SL wins a
  tie); **EOD** settles to cash-settle intrinsic at the first snapshot ≥ 16:00.
- **Reports** under `outputs/backtests/{latest,runs/<stamp>_<label>}/`: `trades.csv`,
  `candidates.csv`, `daily_pnl.csv`, `equity_curve.csv`,
  `summary_by_{profile,symbol,corridor,wds_tier}.csv`, `no_trade_reasons.csv`,
  `run_config.json` — win rate, P&L, expectancy, profit factor, max drawdown, TP/SL/EOD
  counts, CALL vs PUT frequency, active-vs-inactive corridor, and per-WDS-tier performance.
- **SPY / QQQ** run on the same code path, but their wing thresholds are flagged
  **provisional** (`threshold_scheme` / `threshold_warning` on every row) — the SPX 2K/5K/10K
  volume thresholds are not yet calibrated for those symbols, so results are not
  over-interpreted (calibration is Phase 10C).

The runner is read-only and idempotent; nothing here places, previews, or routes an order.
Phase 10C adds SPY/QQQ threshold calibration, a `wingonomics_daily_stats.csv` cross-check,
corridor/WDS → selector weighting, and full 1DTE support.

---

## Safety guardrails

- No code in this repo connects to a broker. The `ExecutionProvider` interface
  exists with modes: `disabled`, `local_paper`, `manual_trade_tracking`. Live
  modes (`broker_paper`, `manual_confirm`, `live_tiny`, `live`) are stubbed and
  raise `NotImplementedError`.
- The ZerσSigma API connection is **planned but not yet wired**. The default
  `StructureProvider` is a stub.
- All filesystem writes land under `outputs/` (or wherever `DATA_DIR` /
  `OUTPUT_DIR` point in `.env`).
- This repo never reads or writes to sibling repos. Dashboard, ZS API, and
  worker code are read-only references only — see `docs/reference_notes.md`.
