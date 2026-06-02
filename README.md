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
