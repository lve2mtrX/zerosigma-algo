# Read-Only ZerσSigma Integration Notes

> Notes captured from a read-only inspection of the sibling repos:
> - `..\Dashboard` (ZerσSigma production Dashboard)
> - `..\zerosigma-api` (ZerσSigma public API)
>
> **The algo cockpit must not modify any of those files.** This document
> records what we observed so the cockpit's StructureProvider implementation
> (Phase 2) consumes the right contracts.
>
> External files inspected (read-only):
> - `Dashboard/app/ingest/worker_watchlist.py`
> - `Dashboard/app/ingest/snapshot_worker.py`
> - `Dashboard/app/jobs/job_ddoi_compute.py`
> - `Dashboard/app/api/latest.py`
> - `Dashboard/app/store.py`
> - `Dashboard/app/state.py`
> - `Dashboard/app/calcs/chain_compute.py`
> - `Dashboard/app/calcs/exposures.py`
> - `Dashboard/app/calcs/module_transforms.py`
> - `Dashboard/app/services/replay_service.py`
> - `Dashboard/app/ingest/schwab_client.py`
> - `zerosigma-api/app/main.py`
> - `zerosigma-api/app/api/v1/{auth,market,exposure,billing,me,users,admin,analytics}.py`
> - `zerosigma-api/app/services/{chain_series,redis_client,billing}.py`
> - `zerosigma-api/app/core/{security,dependencies,config,rate_limit}.py`
> - `zerosigma-api/app/schemas/{auth,billing}.py`
> - `zerosigma-api/app/models/user.py`
>
> **No external files were modified during inspection.**

---

## 1. Data flow (end-to-end)

```
Schwab API
   │
   ▼
Dashboard/app/ingest/worker_watchlist.py
   - polls spot every 2–10s
   - polls chain every 60s per symbol
   - computes wide chain CSV with greeks + exposures
   │
   ▼
Redis  (keys under prefix configurable via $ZS_REDIS_PREFIX, default "zs")
   - zs:latest:{SYMBOL}:spot_json
   - zs:latest:{SYMBOL}:chain_json
   - zs:latest:{SYMBOL}:chain_csv
   - zs:latest:{SYMBOL}:metrics_json
   - zs:latest:{SYMBOL}:meta_json
   - zs:latest:ES:factor
   - wings:snapshot:{SYMBOL}:{YYYY-MM-DD}
   - zs:prev_day_wings:{SYMBOL}  (alt primary key on API side)
   - zs:worker:{status,heartbeat_ts,last_error,last_record_json}
   - zs:watchlist  (comma-separated symbols)
   │
   ├──────────────────────────────────────────────────────────┐
   ▼                                                          ▼
Dashboard (Plotly/Dash UI — read direct from Redis)     zerosigma-api (FastAPI)
                                                          │
                                                          ▼
                                                   Algo Cockpit (us)
                                                   reads /api/v1/...
```

The cockpit talks **only** to `zerosigma-api`. It does NOT touch Redis directly.

---

## 2. API endpoints the cockpit will consume

All paths are relative to `ZS_API_BASE_URL` (e.g. `https://api.zerosigma.example`).

### 2.1 Auth

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/auth/login` | body `{email, password}` → `{access_token, token_type:"bearer", user_id}` |
| POST | `/api/v1/auth/refresh` | requires current bearer |
| POST | `/api/v1/auth/service-token` | server-to-server; requires `ADMIN_SERVICE_KEY` |
| POST | `/api/v1/auth/logout` | revokes JTI in Redis |

Tokens: JWT with `{sub: email, exp, jti}`, ~15-minute TTL, JTI tracked in
Redis for revocation. Send as `Authorization: Bearer <token>` header.

### 2.2 Market data (public — no auth required)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/market/spot?symbol=SPX` | current spot + quote timestamp |
| GET | `/api/v1/market/chain?symbol=SPX` | chain metadata (expiry, DTE, spot, straddle IV) |
| GET | `/api/v1/market/exposures?symbol=SPX` | aggregated `{total_gex_bn, total_vex_bn, gamma_flip, call_wall, put_wall, maxvol}` |
| GET | `/api/v1/market/snapshot?symbol=SPX` | `{symbol, timestamp, spot, exposures, chain}` |
| GET | `/api/v1/market/prev-wings?symbol=SPX&requested_date=YYYY-MM-DD` | prior-day wings; falls back to most recent |
| GET | `/api/v1/market/es-factor` | ES factor for SPX→ES conversion |

Server-side cache: 1s on spot/chain/exposures/snapshot. Rate limit: 60/min.

### 2.3 Exposure series (requires active subscription)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/exposure/series?symbol=SPX&metric=raw_gex&mode=net&weight=oi` | per-strike net or split exposure series |
| GET | `/api/v1/exposure/ddoi?symbol=SPX` | DDOI history from Spaces JSONL (5-min server cache) |

Allowed metric values: `raw_gex | da_gex | dex | vex | cex | volume`.
Allowed mode values: `net | split`.
Allowed weight values: `oi | volume`.

**Response shape — net mode**:
```json
{
  "symbol": "SPX",
  "metric": "raw_gex",
  "mode": "net",
  "weight": "oi",
  "spot": 5803.21,
  "ts": "2026-05-31T14:30:00-04:00",
  "strikes": [5500, 5505, ..., 6100],
  "net": [12.3, 9.1, ..., -4.2]
}
```

**Response shape — split mode**: same plus `calls: [...]`, `puts: [...]`
instead of `net`.

`VEX` is sign-flipped in net mode (landslide orientation).

Rate limit: 30/min on `/exposure/*`.

---

## 3. Wide chain CSV column contract

Returned from `/api/v1/market/chain` (or via the `chain_csv` field on
`/market/snapshot`).

Mandatory non-side columns:
```
snapshot_ts, snapshot_date, symbol, spot, expiry, strike, dte
```

Per-side columns: prefix `c_` for call, `p_` for put. The following suffixes
all exist on both sides:
```
bid, ask, mid, iv,
delta, gamma, vega, theta, rho, vanna, charm, speed, vomma, zomma,
gex_1pct, raw_gex_1pct, da_gex_1pct, dex_1pct,
vex_1vol, vex_skew_1vol,
cex, cex_skew, charm_skew,
speed_exp, vomma_exp, zomma_exp,
oi, volume
```

So e.g. `c_volume` is call volume at strike, `p_volume` is put volume.
`c_da_gex_1pct` is the call-side delta-adjusted GEX in $Bn per 1% move.

**Vertical Wingy uses `c_volume` and `p_volume` for PUT_CEILING / CALL_FLOOR
identification.**

---

## 4. Exposure metric semantics (from Dashboard `app/calcs/exposures.py`)

All values in $Bn, OI-weighted at snapshot time:

| Suffix | Meaning |
|---|---|
| `gex_1pct` | OI-weighted gamma exposure for a 1% spot move |
| `raw_gex_1pct` | unsigned gamma (no put/call sign convention) |
| `da_gex_1pct` | delta-adjusted gamma exposure (the "DA-GEX" we use for regime) |
| `dex_1pct` | delta exposure |
| `vex_1vol` | vega exposure per 1 vol point |
| `vex_skew_1vol` | skew-adjusted vex |
| `cex` | theta exposure (cash decay) |
| `cex_skew`, `charm_skew` | skew-adjusted variants |
| `speed_exp`, `vomma_exp`, `zomma_exp` | 2nd/3rd order Greek exposures |

`METRIC_SUFFIX` in `Dashboard/app/calcs/module_transforms.py` is the
authoritative mapping from UI metric names to CSV column suffixes.

`SIGNED_NET_METRICS` (net = c + p with sign): `delta, theta, dex, da_gex, vex, cex, ...`
`UNSIGNED_NET_METRICS` (net = c + p, no sign flip): `gamma, vega, gex, oi, volume`.

---

## 5. Refresh / write cadences

| Producer | Output | Cadence |
|---|---|---|
| `worker_watchlist.py` | `zs:latest:{SYMBOL}:spot_json` | every 2–10s |
| `worker_watchlist.py` | `zs:latest:{SYMBOL}:chain_csv`, `metrics_json`, `meta_json` | every 60s per symbol |
| `snapshot_worker.py`  | `history/raw/{SYMBOL}/{YYYY-MM-DD}/chain_daily.csv` (Spaces) | every 5 min pre/post mkt; every 60s during mkt |
| `job_ddoi_compute.py` | `history/ddoi/{SYMBOL}/ddoi_history.jsonl` (Spaces) | daily 07:00 ET |
| (worker)              | `zs:latest:ES:factor`                   | once at 17:00 ET when ES session closes |
| (worker)              | `wings:snapshot:{SYMBOL}:{YYYY-MM-DD}` | at session close (16:05 ET); kept 3 trading days |

**Cockpit polling plan** (matches without overshooting):

| Cockpit task | Endpoint | Cadence |
|---|---|---|
| structure context | `/api/v1/market/snapshot?symbol=SPX` | 60s |
| structure series (any metric) | `/api/v1/exposure/series?...` | 60s (per metric requested) |
| spot ticker | `/api/v1/market/spot?symbol=SPX` | 2–5s (well under 60/min limit) |
| prev-wings | `/api/v1/market/prev-wings?symbol=SPX` | once at startup + once at 09:30 ET |
| DDOI | `/api/v1/exposure/ddoi?symbol=SPX` | once per session start |
| ES factor | `/api/v1/market/es-factor` | once per session start |

---

## 6. Authentication choice for the cockpit

Two valid options:

**Option A — user JWT (preferred for a personal cockpit)**
1. `POST /api/v1/auth/login {email, password}` → get bearer.
2. Store in memory; refresh via `/api/v1/auth/refresh` before 15-min expiry.
3. The user must have an active subscription for `/exposure/*` endpoints.

**Option B — admin service token (server-to-server)**
1. Set `ZS_API_ADMIN_SERVICE_KEY` in `.env`.
2. `POST /api/v1/auth/service-token` with that key in the body.
3. Use the returned token as bearer.

Cockpit `StructureProvider` should support both, picking whichever env vars
are populated.

---

## 7. Known constraints / gotchas

- **Public/private split**: `/market/*` is public; `/exposure/*` requires
  active subscription. If the cockpit's user isn't subscribed the
  `/exposure/series` and `/exposure/ddoi` calls will 403 — the cockpit must
  degrade gracefully and log it (it can still scan from `/market/snapshot`).
- **Rate limits**: SlowAPI. 60/min on market, 30/min on exposure. Our planned
  cadence stays safely inside.
- **DDOI 503**: if the ZS API server doesn't have `DO_SPACES_*` env vars
  configured, `/exposure/ddoi` returns 503. Treat DDOI as optional.
- **Stale chain on quiet days**: chain only refreshes every 60s. Don't trust
  `snapshot_ts` for sub-minute precision.
- **Schwab built-in greeks**: chain greeks come from Schwab when available,
  fall back to BS. We do not need to recompute greeks locally.
- **ES factor is set once at 17:00 ET**: if the cockpit launches mid-session,
  the ES factor it reads is from the *previous* day's close.

---

## 8. Things the cockpit must NEVER do

- Modify Dashboard, ZS API, worker, or Schwab ingest files.
- Write to Redis under the `zs:` prefix (or any prefix the worker uses).
- Write to the `history/` prefix in DigitalOcean Spaces.
- Re-implement Greek calculations (use what the API serves).
- Bypass JWT validation or rate-limit middleware.
- Run with admin role for non-admin endpoints.
- Cache `/me/entitlement` (it's canonical and explicitly uncached server-side).

---

## 8a. Phase 2 Read-Only ZS API Contract Notes

> Captured during Phase 2 implementation of `ZeroSigmaApiStructureProvider`.
> Re-inspection of `zerosigma-api` and `Dashboard` (read-only). No external
> files were modified. Values below are CONTRACT DETAILS; **no real secrets
> were copied** — only env-var names.

### Auth

| Mechanism | Endpoint | Body | Returns | Notes |
|---|---|---|---|---|
| User JWT | `POST /api/v1/auth/login` | `{email, password}` | `{access_token, token_type:"bearer", user_id}` | 5/min. Token TTL = `ACCESS_TOKEN_EXPIRE_MINUTES` (default 15). |
| Service token | `POST /api/v1/auth/service-token` | `{email, service_key}` | `{access_token, token_type:"bearer"}` | 10/min. Server validates `service_key` against `ADMIN_SERVICE_KEY` env. **Caller must be an admin user.** Returns 501 if `ADMIN_SERVICE_KEY` is not configured on the server, 401 if key invalid, 403 if user not admin. |
| Refresh | `POST /api/v1/auth/refresh` | (Bearer) | new `{access_token, ...}` | 30/min. Old token remains valid until natural expiry. |
| Logout | `POST /api/v1/auth/logout` | (Bearer) | `{detail}` | Idempotent. Deletes JTI in Redis. |

Header for authenticated calls: `Authorization: Bearer <access_token>`.
Server tracks each JWT's JTI in Redis (`zs:token:{jti}`); revocation is via logout.

### Endpoints the Phase 2 StructureProvider consumes

| Method | Path | Auth | Cache | Phase 2 use |
|---|---|---|---|---|
| GET | `/api/v1/market/snapshot?symbol=SPX` | public | 1s | Primary call. Returns `{symbol, timestamp, spot:{...}, exposures:{...}, chain:{...}}`. |
| GET | `/api/v1/market/exposures?symbol=SPX` | public | 1s | Backup if `snapshot.exposures` is missing. Returns `{ts, total_gex_bn, da_gex_bn, dex, vex, cex}`. |
| GET | `/api/v1/exposure/series?symbol=SPX&metric=volume&mode=split` | **subscription** | 2s | Per-strike call/put volumes. Used to derive `PUT_CEILING_{2K,5K}` / `CALL_FLOOR_{2K,5K}` and `maxvol`. Rate 60/min. Returns 403 if user not subscribed → provider degrades gracefully. |
| GET | `/api/v1/exposure/ddoi?symbol=SPX` | **subscription** | 5min | DDOI history. Optional; sets `ddoi_pin = None` when 503/missing. Rate 30/min. Returns 503 if `DO_SPACES_*` not configured. |

**Response shapes** (real ZS field names):

```jsonc
// /market/exposures
{ "ts": "2026-03-18T14:30:00", "total_gex_bn": 12.34,
  "da_gex_bn": 5.67, "dex": 2.1, "vex": -0.5, "cex": 0.3 }

// /market/snapshot
{ "symbol": "SPX", "timestamp": "...",
  "spot": { "underlying": "SPX", "price": 5850.25, "timestamp": "..." },
  "exposures": { ... same as /market/exposures ... },
  "chain":     { "strikes": [...], "calls": [...], "puts": [...] } }

// /exposure/series (mode=split)
{ "symbol": "SPX", "metric": "volume", "mode": "split", "weight": "oi",
  "spot": 5850.25, "ts": "...",
  "strikes": [5700, 5750, ...],
  "calls":   [120,   200, ...],
  "puts":    [80,    150, ...] }
```

### Field-by-field mapping into `ExposureContext`

| ExposureContext field | Source | Derivation |
|---|---|---|
| `total_gex_bn` | `/market/exposures.total_gex_bn` | direct |
| `total_vex_bn` | `/market/exposures.vex` | direct (ZS uses `vex`, no `_bn` suffix) |
| `total_dex_bn` | `/market/exposures.dex` | direct (algo doesn't actually expose this field today; documented for future) |
| `total_cex_bn` | `/market/exposures.cex` | direct |
| `da_gex_signed` | `/market/exposures.da_gex_bn` | direct |
| `gamma_regime` | derived | `"positive" if da_gex_bn > 0 else "negative" if da_gex_bn < 0 else None` |
| `maxvol` | derived from `/exposure/series` (subscription) | strike with max `call_volume[k] + put_volume[k]` |
| `put_ceiling_2k` | derived from `/exposure/series` | `max(strike for k, vol in zip(strikes, puts) if vol >= 2000)` |
| `put_ceiling_5k` | derived | same with threshold 5000 |
| `call_floor_2k` | derived from `/exposure/series` | `min(strike for k, vol in zip(strikes, calls) if vol >= 2000)` |
| `call_floor_5k` | derived | same with threshold 5000 |
| `ddoi_pin` | `/exposure/ddoi` (subscription, 5-min cache) | most-recent record's `pin` field if present, else None |
| `gamma_flip` | _not exposed_ | None on Phase 2 launch — Dashboard's `gamma_flow_incremental` carries it but no API endpoint surfaces it. |
| `call_wall` / `put_wall` | _not exposed directly_ | None on Phase 2 launch — Dashboard derives from OI distribution; no API endpoint surfaces it. |

`StructureSnapshot.spot` is taken from `snapshot.spot.price`. `quote_ts` from `snapshot.timestamp` (parsed to aware `datetime`). `expiry` is from `snapshot.chain.expiry` if present, else `None`.

### Auth-flow algorithm used by the provider

```
1. If ZS_API_TOKEN env is set → use it directly as Bearer.
2. Else if ZS_API_USERNAME + ZS_API_PASSWORD are set →
       POST /auth/login {email, password} → cache access_token.
3. Else if ZS_API_ADMIN_SERVICE_KEY + ZS_API_USERNAME are set →
       POST /auth/service-token {email, service_key} → cache access_token.
4. Else → status() reports "no auth configured"; get_snapshot raises
       RuntimeError before any HTTP call.

On 401 from a data endpoint → invalidate cached token + retry once.
```

We do NOT call `/auth/refresh` in Phase 2 — simpler to re-mint via login/service-token on expiry.

### Rate-limit safety

Cockpit polls structure every ~60 s by default (`ZS_REFRESH_SECONDS`).
That's well inside ZS's published caps:

- public market endpoints (`/market/*`): no SlowAPI limit (1 s server cache)
- `/exposure/series`: 60/min
- `/exposure/ddoi`: 30/min
- auth: 5–30/min depending on endpoint

### ZS API server env vars (read-only knowledge — do NOT copy values)

`DATABASE_URL`, `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
`REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`,
`ADMIN_SERVICE_KEY`, `DO_SPACES_KEY/SECRET/REGION/BUCKET`,
`REVENUECAT_SECRET_API_KEY`, `REVENUECAT_WEBHOOK_AUTH`.

**The cockpit only cares about a tiny subset for outbound auth** — see
`.env.example` for the algo's own variable names (`ZS_API_*`).

### Why we don't read Redis or DigitalOcean Spaces directly

Dashboard reads Redis (`zs:latest:{SYMBOL}:*`) and Spaces history JSONL
directly because it lives inside the same VPC + has the credentials. The
algo cockpit is a **separate, portable process** that runs from a user's
laptop. Hitting the public ZS API is the supported integration path.

### Phase 2 acceptance criteria

- ✅ Single `/market/snapshot` call populates spot + exposure aggregates.
- ✅ Optional `/exposure/series?metric=volume&mode=split` enriches
  `put_ceiling_*` / `call_floor_*` / `maxvol` when the user is subscribed.
- ✅ Subscription gate (`/exposure/*` returning 403) degrades gracefully —
  those fields go to None, provider continues serving the rest.
- ✅ Auth failures surface via `status()` rather than crashing the cockpit.
- ✅ `gamma_flip`, `call_wall`, `put_wall`, `ddoi_pin` are explicitly None
  on launch day with a follow-up tracked in `notes.md`.

---

## 8b. Phase 3 Tastytrade Capability Probe Notes

> Research done for the Phase 3 probe scaffold. **Do not** treat this as
> the final QuoteProvider contract — these are findings to verify
> empirically once Dan runs `scripts.probe_tastytrade` against a real
> account. Sources cited inline; full URL list at the end of the section.

### Base URLs

| Environment | Host | Notes |
|---|---|---|
| Production | `https://api.tastyworks.com` | Note the **tastyworks.com** domain (not `tastytrade.com`). |
| Certification (sandbox) | `https://api.cert.tastyworks.com` | 15-minute delayed quotes; resets every 24 hours. |

Tastyware/tastytrade Python SDK constants `API_URL` / `CERT_URL` confirm
these verbatim. The `developer.tastytrade.com` site documents the same.

### Authentication

Two flows coexist; Tastytrade announced the legacy `/sessions` flow
would be deprecated (community references cite Dec 1, 2025 — the
deprecation has slipped before, verify empirically).

**Legacy session-token flow** (lowest friction for the smoke probe):

```
POST /sessions
Content-Type: application/json
{
  "login":       "<username-or-email>",
  "password":    "<password>",
  "remember-me": true
}
→ 200 OK
{
  "data": {
    "user":           { "email": "...", "username": "...", "external-id": "..." },
    "session-token":  "<token>",
    "remember-token": "<token>"   // present only when remember-me=true
  },
  "context": "/sessions"
}
```

Subsequent requests use the BARE token in the `Authorization` header —
**no `Bearer ` prefix**:

```
Authorization: <session-token>
```

Remember-token can be reused for password-less re-login by POSTing
`{"login": "...", "remember-token": "..."}`. The remember-token rotates
on each use.

**OAuth2 flow** (recommended/durable path forward — Personal OAuth
Application registered in the Tastytrade UI):

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=refresh_token&client_secret=<secret>&refresh_token=<token>
→ 200 OK
{ "access_token": "<token>", "token_type": "Bearer", "expires_in": 900, ... }
```

Authenticated requests use `Authorization: Bearer <access_token>`.
Access token TTL ≈ 900s; refresh via the same endpoint with the
refresh token. The SDK refreshes with a 60s buffer.

**Probe choice**: implement legacy `/sessions` for the smoke test; document
OAuth2 in the probe module's docstring as the path for the eventual
production QuoteProvider.

### REST endpoints the probe touches

All responses are wrapped in a `data` envelope and use **kebab-case**
field names.

| Endpoint | Path | Purpose |
|---|---|---|
| Login | `POST /sessions` | Returns `data.session-token`. |
| List accounts | `GET /customers/me/accounts` | `data.items[].account.account-number`. |
| Account details | `GET /customers/me/accounts/{n}` | `data.account-type-name`, margin-or-cash, etc. |
| Option chain (nested) | `GET /option-chains/{symbol}/nested` | Expiration → strikes → `{call, put, call-streamer-symbol, put-streamer-symbol, strike-price}`. Best shape for SPX/SPXW per-expiration walks. |
| Option chain (flat) | `GET /option-chains/{symbol}` | Flat list, grouped by expiration in the payload. |
| Option chain (compact) | `GET /option-chains/{symbol}/compact` | OCC symbols + streamer symbols, minimal payload. |
| Quote (single) | `GET /market-data/{instrument-type}/{symbol}` | `data.{bid, ask, mid, last, mark}`. `instrument-type` ∈ `equity \| equity-option \| index \| future \| future-option \| cryptocurrency`. |
| Quote (bulk) | `GET /market-data/by-type?equity-option=SYM1,SYM2,…` | Up to **100** symbols in one call across all instrument-type params. |
| DXLink token | `GET /api-quote-tokens` | `data.{token, dxlink-url, level}`. **Use this, not `/quote-streamer-tokens`** — the latter is reserved for Tastytrade's own apps and is out-of-TOS for API consumers. |
| Order dry-run (simple) | `POST /accounts/{n}/orders/dry-run` | Preflight only. Returns `buying-power-effect`, `fee-calculation`, `warnings`, `errors`. Does NOT route. |
| Order dry-run (multi-leg) | `POST /accounts/{n}/complex-orders/dry-run` | Same, for vertical / multi-leg orders. |

**For the Phase 3 probe**: hit ONLY `/sessions`, `/customers/me/accounts`,
`/option-chains/{symbol}/nested`, `/market-data/by-type`,
`/api-quote-tokens`. Skip `/orders` and `/complex-orders` entirely. The
`/dry-run` endpoint IS safe (no routing), but the cockpit must not POST
to it until Dan has reviewed the probe results.

### DXLink streaming (probe scope: confirm token availability only)

Tastytrade streams market data over **DXFeed DXLink** (WebSocket).

1. `GET /api-quote-tokens` → `{token, dxlink-url, level}`.
2. WebSocket connect to `dxlink-url` (community references point to
   `wss://tasty-openapi-ws.dxfeed.com/realtime`; **read it from the API
   response, don't hardcode**).
3. Handshake: client `SETUP` → server `AUTH_STATE=UNAUTHORIZED` →
   client `AUTH(token)` → server `AUTH_STATE=AUTHORIZED` → client
   `CHANNEL_REQUEST{service:'FEED', parameters:{contract:'AUTO'}}` →
   server `CHANNEL_OPENED` → client `FEED_SETUP` → client
   `FEED_SUBSCRIPTION{add:[{type:'Quote', symbol:'.SPXW250620C5000'}, ...]}`.
4. Keepalive every ~30s.

**Phase 3 probe scope**: fetch `/api-quote-tokens` and confirm `token`
is present + `dxlink-url` is reachable in URL form. **Do NOT open the
WebSocket** in the smoke probe — that's a deeper integration for the
production QuoteProvider.

### SPX vs SPXW — root resolution rule

Two **separate underlyings** on the Tasty API even though both appear
under `/option-chains/SPX/nested`:

| Symbol | Settlement | Expirations | Note |
|---|---|---|---|
| `SPX` | AM | Monthly only (3rd Friday) | Cash-settled, exercise at open. |
| `SPXW` | PM | Weeklies + 0DTE (Mon-Wed-Fri intraday) | The one VW v1 targets. |

**Practical rule (locked by Phase 3.1 probe behavior)**: any daily /
weekly / 0DTE SPX expiration uses the **SPXW** root, not SPX. Sending
`SPX  YYMMDDCKKKKKKKK` for those dates returns 0 quotes; the OCC
symbol must carry `SPXW` (padded to 6 chars: `SPXW  `). The 3rd-Friday
monthly is the only date you'd legitimately encode as `SPX  `. When
both AM and PM listings expire the same day, **prefer SPXW** — that's
what `TastyProbeClient.resolve_root_for(...)` does automatically.

On a date when both AM and PM listings expire (e.g. 3rd-Friday monthly
PLUS the same-day weekly), **both appear in the same expiration bucket**
under `/option-chains/SPX/nested`. Filter by the OCC root (SPX vs SPXW)
or by the `settlement-type` / `expiration-type` fields on the nested
chain. For VW 0DTE: walk `expirations` where `expiration-date == today`
AND the streamer-symbol carries `SPXW`.

OCC symbol = padded 21-char OPRA format. Streamer-symbol = DXFeed
dotted format, e.g. `.SPXW250620C5000` for the 2025-06-20 5000 SPXW
call. Both are exposed on every Option record (`symbol` and
`streamer-symbol` in the flat chain; `call`/`put` and
`call-streamer-symbol`/`put-streamer-symbol` in the nested chain).

### Certification (sandbox) capabilities

| Capability | State |
|---|---|
| Account registration | At `developer.tastytrade.com/sandbox`. |
| Quotes | 15-minute delayed. |
| Reset | Every 24 hours (positions + balances cleared). |
| Multi-leg orders | Supported (`/complex-orders` + `/complex-orders/dry-run` both exposed). |
| Fill behavior | Market → fill at $1. Limit price < $3 → immediate fill. Limit price > $3 → stays Live, never fills. |
| Index options (SPX/SPXW) | **Unconfirmed.** developer.tastytrade.com/sandbox does not enumerate per-instrument restrictions. Community reports indicate cert symbology can lag and return 422 on valid live symbols. Probe must treat this as a runtime check — catch 422 on the chain call. |

### Order preview (dry-run) — safe by design

`POST /accounts/{n}/orders/dry-run` and `POST /accounts/{n}/complex-orders/dry-run`
run preflights and **do NOT route to the exchange**. They return:

```jsonc
{
  "data": {
    "order":               { ... echo of the submitted shape ... },
    "buying-power-effect": { "change-in-buying-power": ..., ... },
    "fee-calculation":     { ... },
    "warnings":            [ ... ],
    "errors":              [ ... ]
  }
}
```

There is **no `submit=false` / `dry-run=true` flag** on the live order
endpoint. The dry-run is a separate path — code should hit `/dry-run`
exclusively when previewing.

**Phase 3 probe**: keep `--dry-run-vertical` behind an explicit CLI flag
(default off). Even the dry-run is one HTTP call away from a real order
path — make the user opt in.

### Rate limits

- **No public, documented per-endpoint limit.** developer.tastytrade.com
  + the FAQ do not state numbers.
- Community SDKs (tastyware/tastytrade, tasty-agent MCP server) self-
  throttle at ~2 req/s (~120/min) as a defensive default.
- The FAQ notes the API inspects User-Agent and can return errors / IP
  blocks on suspicious patterns. **Set a descriptive User-Agent.**
- For higher limits or per-endpoint specifics, contact
  api.support@tastytrade.com.

### Knowns we are deliberately deferring

1. Empirical status of legacy `/sessions` after the announced deprecation
   date — verify before the probe ships against real creds.
2. Whether cert supports SPX/SPXW end-to-end (chain + quotes + dry-run).
   The probe will surface a 422 cleanly if the cert chain endpoint
   refuses the symbol.
3. DXLink WebSocket connection itself — Phase 3 only confirms token
   acquisition. Real streaming lands in the production QuoteProvider.
4. OAuth2 personal-app refresh flow with 2FA — there's lingering
   ambiguity in the SDK issue tracker about whether headless refresh
   needs an interactive consent step.

### Sources

Tastytrade developer docs:

- https://developer.tastytrade.com/api-guides/sessions/  (legacy `/sessions`, `session-token`, `remember-me`)
- https://developer.tastytrade.com/api-guides/oauth/  (OAuth2 — recommended)
- https://developer.tastytrade.com/api-overview/
- https://developer.tastytrade.com/basic-api-usage/  (base URLs, BARE-token Authorization header for legacy, kebab-case, data envelope)
- https://developer.tastytrade.com/api-guides/instruments/  (`/option-chains/{symbol}`, `/nested`, `/compact`)
- https://developer.tastytrade.com/streaming-market-data/  (`/api-quote-tokens` vs `/quote-streamer-tokens`, DXLink)
- https://developer.tastytrade.com/sandbox/  (cert host, 15-min delay, 24h reset, fill behavior)
- https://developer.tastytrade.com/open-api-spec/orders/  (dry-run + complex-order dry-run)
- https://developer.tastytrade.com/order-management/  (Order Dry Run section)
- https://developer.tastytrade.com/faq/  (User-Agent / IP-block guidance; no rate-limit statement)

Reference SDK (unofficial, the authoritative reverse-engineering source):

- https://github.com/tastyware/tastytrade  (Python; constants in `__init__.py`, session flow in `session.py`, chains in `instruments.py`, REST quotes in `market_data.py`, DXLink in `streamer.py`)
- https://github.com/tastyware/tastytrade/issues/142  (DXLink vs legacy DXFeed; `/api-quote-tokens` is the API-user endpoint)
- https://github.com/tastyware/tastytrade/issues/269  (legacy `/sessions` deprecation, migration to OAuth2)

Reference SDK (official, JS):

- https://github.com/tastytrade/tastytrade-api-js
- https://github.com/tastytrade/tastytrade-api-js/blob/master/lib/services/orders-service.ts  (order paths incl. `/dry-run`, `/reconfirm`)

---

## 9. Future recommendations for the ZS team

These are out of scope for this repo; recording so they aren't lost:

1. **Bundled `structure-levels` endpoint** — return `put_ceiling`,
   `call_floor`, `maxvol`, `gamma_flip`, `call_wall`, `put_wall`, `ddoi_pin`
   in one payload. Saves the cockpit from per-metric polling.
2. **`last_updated_ts` on `/market/snapshot`** — let the cockpit short-circuit
   poll when nothing changed.
3. **Optional SSE / websocket variant of `/market/snapshot`** — only matters
   if cockpit cadence ever needs to drop below 60s.
4. **Public `/api/v1/market/structure-context`** — expose `expected_move`,
   regime classification (positive vs negative gamma), trend label so multiple
   consumers don't reinvent them.
5. **Per-symbol last-refresh metadata** on `/market/snapshot` payload — helps
   detect Schwab outages.

None of these block Phase 1.

---

## 10. Phase 4 — `TastytradeQuoteProvider` REST endpoints + validation

Promoted the Phase 3 probe into the production `QuoteProvider`. No new
Tasty endpoints beyond what Phase 3 documented — Phase 4 just wires them
into the scanner / cockpit through the `QuoteProvider` Protocol.

### Endpoints consumed at scan-tick rate

| Endpoint | Auth | Phase | Purpose |
|---|---|---|---|
| `POST /oauth/token` (refresh) | OAuth | 3 → 4 | Mint a short-lived access token from the long-lived refresh token. Provider reuses the probe's `TastyProbeClient.login()`. |
| `POST /sessions` | legacy | 3 → 4 | Fallback when OAuth env vars are not all set. BARE Authorization header thereafter. |
| `GET /option-chains/{symbol}/nested` | both | 3 → 4 | One per `get_option_chain()` call — only to resolve SPX vs SPXW for the requested expiry. |
| `GET /market-data/by-type?option=<comma-OCC>` | both | 4 | One per scan tick — fetches BOTH C+P sides of each `required_strike` in a single call. Cap 100 symbols per request. |

The probe (`scripts.probe_tastytrade`) remains the operator-facing
diagnostic; the provider is the in-process consumer.

### `QuoteValidation` thresholds (`src/providers/quotes/types.py`)

Applied PER QUOTE inside the provider, BEFORE the quote is handed to
the strategy. Failed quotes are NOT removed from the chain — they ride
along with `validation_passed=False` + a short snake_case
`validation_rejection_reason` so CSV / JSONL stays grep-friendly.

| Check | Default | Env var | Reason string |
|---|---|---|---|
| Missing bid OR ask | always on | n/a | `missing_bid_or_ask` |
| ask < bid (crossed) | on | `TASTY_REJECT_CROSSED_MARKET=true` | `crossed_market(bid=…,ask=…)` |
| bid <= 0 (no market) | on | `TASTY_REJECT_ZERO_BID=true` | `zero_bid` |
| (ask − bid) > $5.00 | on | `TASTY_QUOTE_MAX_SPREAD_ABS=5.00` | `spread_abs(0.00>5.00)` |
| (ask − bid)/mid > 50% | on | `TASTY_QUOTE_MAX_SPREAD_PCT=0.50` | `spread_pct(60.0%>50%)` |
| now − quote_time > 10s | on | `TASTY_QUOTE_MAX_AGE_SECONDS=10` | `stale(age=12.0s>10s)` |

All five thresholds default to conservative values — appropriate for an
ACTIVE RTH session. After-hours quotes will fail `stale` immediately;
operators should set `TASTY_QUOTE_MAX_AGE_SECONDS=0` to disable the
age check when reviewing EOD data.

### Provider selection precedence

```
--quote-provider <name>          # CLI flag on scripts.run_scanner
  ↓ fallback
QUOTE_PROVIDER=<name>            # .env
  ↓ fallback
config/providers.yaml: quotes.active
  ↓ fallback
"mock"                           # safe default — synthesized chain, no network
```

`<name>` is one of `mock`, `null`, `tastytrade`. Any unknown name falls
back to `mock` with a warning. Selecting `tastytrade` without
`TASTY_CLIENT_ID/SECRET/REFRESH_TOKEN` (or `TASTY_USERNAME/PASSWORD`):

- **Scanner** raises `TastytradeConfigurationError` and exits with code 4.
- **Streamlit cockpit** falls back to mock visibly (yellow warning) so
  the UI stays loadable.

### Files to point at when wiring future quote providers

| File | Role |
|---|---|
| `src/providers/quotes/base.py` | `QuoteProvider` Protocol — the 5 methods every provider must implement. |
| `src/providers/quotes/types.py` | `OptionQuote`, `OptionChainSnapshot`, `QuoteRequest`, `QuoteValidation`. |
| `src/providers/quotes/factory.py` | Provider selection precedence + instantiation. |
| `src/providers/quotes/tastytrade_provider.py` | Reference implementation — composes a probe client, applies validation, attaches root metadata. |
| `tests/test_phase4_tastytrade_provider.py` | Test patterns — fake probe, `MockTransport` not required for happy path. |

---

## 11. Phase 4.1 audit metadata + target-DTE plumbing

Phase 4.1 is an observability + plumbing pass. No scoring weights changed,
no execution paths added. Defaults are byte-identical to Phase 4.

### 11.1 Expiry selection (`src/utils/expiry.py`)

`pick_target_expiry(now_et, target_dte, *, mode, allow_after_hours_roll,
available_expiries, after_hours_cutoff_et='16:00', explicit_expiry=None)`
returns a frozen `ExpiryDecision(expiry, source, reason, root_hint,
days_out)`.

Algorithm:

1. **Explicit override** — when `explicit_expiry` is set, short-circuits
   with `source='explicit'`.
2. **Anchor** — defaults to today; if `target_dte=0` AND
   `allow_after_hours_roll=True` AND now ≥ `after_hours_cutoff_et`, advance
   the anchor by one day (`source='after_hours_roll'` if it later matches a
   chain expiry).
3. **Compute target date** under DTE mode:
   - `trading_days`: anchor advanced N trading days (skips weekends + NYSE
     holidays 2025-2027).
   - `calendar_days`: anchor + N calendar days (no skip).
4. **Match against available_expiries**:
   - In list → `source='target_dte_match'` (or `today` for N=0)
   - Not in list → pick nearest FORWARD expiry → `source='fallback_only_available'`
   - No forward expiry at all → `expiry=None`, `source='fallback'`,
     `reason='no_forward_expiry'`. Scanner emits NO_TRADE (clean, no traceback).
5. **root_hint heuristic** — `'SPXW'` when target is within 7 calendar days
   of today, else `None`. The actual root resolution happens at the broker
   layer (`tasty_probe.validate_root_hint` or `resolve_root_for`).

`DTE_MODE` env / `--dte-mode` flag / `scanner.expiry.dte_mode` YAML choose
between `calendar_days` and `trading_days`. Trading_days respects the
hardcoded `us_market_holidays(year)` set — **annual review needed** for
year 2028 and beyond (`_SUPPORTED_YEARS = frozenset({2025, 2026, 2027})`).
The function raises `ValueError` if called for an unsupported year, so
drift cannot happen silently.

### 11.2 Risk rejection structured fields

Each cap filter (`_f_planned_trade_loss_within_cap`,
`_f_theoretical_trade_loss_within_cap`) stamps these onto `Candidate.meta`
regardless of pass/fail outcome (so audit always sees the numbers it
compared):

```python
c.meta['risk_rejections'] = {
    'planned_loss_cap': {
        'type':         'planned_loss_cap',
        'risk_dollars': 1400.0,                # value compared
        'cap_dollars':  1000.0,                # cap (None when no cap configured)
        'stop_variant': 'BASELINE_CASH_SETTLE',
        'contracts':    1,
        'passed':       False,                 # explicit pass/fail
        'reason':       'planned stop risk $1400 > cap $1000 ...',
    },
    'theoretical_loss_cap': { ... },           # mirrors structure
}

# Scalar mirrors
c.meta['planned_stop_risk_dollars']     = 1400.0
c.meta['planned_stop_risk_cap_dollars'] = 1000.0
c.meta['planned_stop_risk_passed']      = False
c.meta['theoretical_loss_dollars']      = 280.0
c.meta['theoretical_loss_cap_dollars']  = 3000.0
c.meta['theoretical_loss_passed']       = True
c.meta['risk_rejection_type']           = 'planned_loss_cap'  # last failing cap, or None
```

The human-readable `c.rejection_reasons: list[str]` is **untouched** — the
existing 'planned stop risk $X > cap $Y' string is still appended. Phase 4.1
is purely additive.

### 11.3 Quote quality bucket

> **SUPERSEDED by Phase 4.2 — see §12.** The bucket below is the ORIGINAL 4.1
> definition on **absolute-$** bins. Phase 4.2 MIGRATED the bucket to
> **pct-of-mid** bins so it shares the SAME cutoffs as the `bid_ask_quality`
> score (they can no longer contradict). The table is retained for historical
> context only; §12 is authoritative.

`compute_readiness(...)` classifies each candidate's quote into one of
(4.1 absolute bins — superseded):

| Bucket | Rule (4.1, superseded) |
|---|---|
| `invalid`   | Either leg has `validation_passed=False` (validator wins) |
| `unknown`   | No leg-width data AND no validation result (mock chain default) |
| `good`      | worst-leg bid-ask abs ≤ **$0.10** |
| `acceptable`| worst-leg bid-ask abs ≤ **$0.20** |
| `poor`      | worst-leg bid-ask abs ≤ **$0.50** |
| `wide`      | worst-leg bid-ask abs >  $0.50 |

In 4.1 the bucket was **distinct from** the `bid_ask_quality` score component
(an absolute $0.20 cap that clipped to 0.0 when the worst leg exceeded it), so
a wide-but-valid quote could score 0.0 yet bucket `acceptable`/`poor`. **Phase
4.2 fixes that contradiction** — the score and bucket now share one pct-of-mid
ruleset (§12).

A `quote_invalid:<reason>` blocker enters `selector_blockers` only when bucket
is `invalid` — the other widths still pass `candidate_passes_quote_filters`
so wide-but-quoted SPX setups stay eligible for selector consideration in
Phase 5.

### 11.4 Selector readiness flags

`compute_readiness(...)` returns four `candidate_passes_*` boolean flags
plus a composite `selector_eligible_base`:

| Flag | True when |
|---|---|
| `candidate_passes_score_threshold` | `c.score >= threshold` |
| `candidate_passes_score_edge`      | `c.score_edge >= MIN_SCORE_EDGE` |
| `candidate_passes_trade_filters`   | No shape-filter rejection reasons in `c.rejection_reasons` |
| `candidate_passes_risk_filters`    | No entry in `c.meta['risk_rejections']` with `passed=False` |
| `candidate_passes_quote_filters`   | `quote_quality_bucket != 'invalid'` |
| `selector_eligible_base`           | score_threshold AND trade AND risk AND quote (NOT edge — Phase 5 may add) |
| `candidate_is_marginal`            | score >= threshold AND edge < MIN_SCORE_EDGE |

`selector_blockers` is a list of human-readable strings —
`'score_below_threshold(score=0.50<thr=0.60)'`,
`'score_below_min_edge(edge=+0.0013<min=0.02)'`,
`'risk_rejected:planned_loss_cap'`, `'quote_invalid:validation_failed'`,
`'trade_filter:credit below floor 0.50'`. Phase 5 selector code can sort,
group, or filter on these.

### 11.5 Env vars + CLI flags + YAML knobs added

| Source | Name | Default | Notes |
|---|---|---|---|
| Env | `TARGET_DTE` | `0` | days-to-expiry; CLI `--target-dte` wins |
| Env | `DTE_MODE` | `trading_days` | `calendar_days` or `trading_days` |
| Env | `ALLOW_AFTER_HOURS_EXPIRY_ROLL` | `false` | roll one day past after_hours_cutoff_et |
| Env | `MIN_SCORE_EDGE` | `0.02` | flags `marginal_score=True` but doesn't gate decisions |
| Env | `STRICT_ROOT_HINT` | `false` | tasty_probe explicit root mismatch → hard fail |
| CLI | `--target-dte 0\|1\|2` | (from env) | scanner only |
| CLI | `--dte-mode calendar_days\|trading_days` | (from env) | scanner only |
| CLI | `--allow-after-hours-roll` | (from env) | scanner only |
| CLI | `--print-candidates` | False | per-candidate stdout audit blocks |
| YAML | `scanner.expiry.target_dte` | `0` | overridden by env / CLI |
| YAML | `scanner.expiry.dte_mode` | `trading_days` | overridden by env / CLI |
| YAML | `scanner.expiry.allow_after_hours_roll` | `false` | overridden by env / CLI |
| YAML | `scanner.expiry.after_hours_cutoff_et` | `"16:00"` | HH:MM ET |

Precedence: CLI > env > YAML > default. Defaults match today's behavior so
no operator action is required.

## 12. Phase 4.2 — relative bid/ask quality + strict DTE + clock skew

Three surgical changes. NOTHING else in scoring/weights/threshold/risk-caps is
touched; no execution. The `bid_ask_quality` recalibration is the ONLY
sanctioned scoring change.

### 12.1 Quote VALIDATION vs quote QUALITY (two different things)

| | Quote VALIDATION | Quote QUALITY |
|---|---|---|
| What | broker per-leg pass/fail | strategy sub-score `bid_ask_quality` |
| Where | `QuoteValidation.validate` (`types.py`) | `src/utils/quote_quality.py` |
| Output | `OptionQuote.validation_passed` + `*_validation_passed` CSV cols | `bid_ask_quality` ∈ [0,1] + `quote_quality_bucket` |
| Rejects | crossed / zero-bid / wide-abs / wide-pct / **positive-age** stale | (does not reject — feeds a 0.05-weighted score) |
| 4.2 change | **none** (byte-identical) | abs cap → **relative pct-of-mid** |

A quote can PASS validation and still earn a low `bid_ask_quality` (or vice
versa). They are independent and intentionally so.

### 12.2 Relative `bid_ask_quality` + bucket (shared pct-of-mid cutoffs)

The shared pure module `src/utils/quote_quality.py` (stdlib-only; never
contains the `vertical_wing` substring, so `src/selector/readiness.py` may
import it without tripping `test_no_vw_leak`) is the single source of truth for
BOTH the score and the bucket — keyed on the WORST leg's bid-ask spread as a
fraction of its mid (`worst_leg_bid_ask_pct_of_mid`):

| worst-leg pct-of-mid | `bid_ask_quality` (relative) | `quote_quality_bucket` |
|---|---|---|
| ≤ `BID_ASK_GOOD_PCT` (3%) | `1.0` | `good` |
| > 3% … ≤ `BID_ASK_ACCEPTABLE_PCT` (7%) | linear **0.8 → 0.6** | `acceptable` |
| > 7% … ≤ `BID_ASK_POOR_PCT` (15%) | linear **0.5 → 0.2** | `poor` |
| > 15% | `0.0` | `wide` |
| `None` (no usable mid) or negative | `0.0`* | `unknown` |
| crossed / missing leg (`worst_abs is None`) | `0.0` | `invalid` |
| any leg `validation_passed=False` | `0.0` | `invalid` (validator wins) |

\* When `worst_pct is None` (no mid) the SCORE auto-falls back to the absolute
path (`1 - worst_abs/cap`) so a missing mid never silently zeroes an otherwise
valid quote; the BUCKET reports `unknown`.

Worked endpoints (verifiable in `tests/test_phase4p2_quote_quality.py`):
`pct=0.05 → 0.7`, `pct=0.07 → 0.6`, `pct=0.11 → 0.35`, `pct=0.15 → 0.2`. The
live motivating case `pct=0.0645` (a $0.20 leg on a ~$3.10 mid) → **~0.6275**
(bucket `acceptable`), where the old absolute $0.20 cap gave **0.0**.

`candidates.py` STAMPS all five into `Candidate.meta`: `bid_ask_quality`,
`bid_ask_quality_mode`, `bid_ask_quality_reason`, `quote_quality_bucket`,
`quote_quality_reason`. `readiness.py` PREFERS the stamped bucket/reason and
only calls the shared helper for fixtures/mock candidates lacking them
(`readiness.py` never imports `vertical_wing`).

### 12.3 `bid_ask_*` config table

| Source | Name | Default | Notes |
|---|---|---|---|
| YAML | `…default_parameters.BID_ASK_QUALITY_MODE` | `relative` | `relative` (pct-of-mid) \| `absolute` (legacy cap) |
| YAML | `…default_parameters.BID_ASK_GOOD_PCT` | `0.03` | ≤ this → score 1.0 / bucket `good` |
| YAML | `…default_parameters.BID_ASK_ACCEPTABLE_PCT` | `0.07` | ≤ this → 0.8–0.6 / `acceptable` |
| YAML | `…default_parameters.BID_ASK_POOR_PCT` | `0.15` | ≤ this → 0.5–0.2 / `poor`; above → 0.0 / `wide` |
| YAML | `…default_parameters.BID_ASK_MAX_ABS_CAP` | `1.00` | absolute-mode (or pct=None) cap. **Set 0.20 for 4.1 parity** |
| Env | `BID_ASK_QUALITY_MODE` … `BID_ASK_MAX_ABS_CAP` | (match YAML) | env substitution into YAML `${…}`; `strategy.py` `float()`-casts the numerics |
| Env | `QUOTE_AGE_CLOCK_SKEW_TOLERANCE_SECONDS` | `2.0` | labels within/beyond-tolerance skew magnitude only |

**Operator parity caveat:** `BID_ASK_MAX_ABS_CAP` defaults to **1.00**, NOT the
legacy 0.20. `BID_ASK_QUALITY_MODE=absolute` reproduces 4.1 ONLY if you also
set `BID_ASK_MAX_ABS_CAP=0.20`.

The 0.05 `bid_ask_quality` weight (`score_weights`), the 0.60
`no_trade_score_threshold`, and `hard_filters.max_bid_ask_width=0.20` are
**UNCHANGED**.

### 12.4 Clock-skew clamp rule

In `run_scanner.py._candidate_row` the oldest-leg `quote_age_seconds` is the
scanner's observability age:

| raw oldest age | emitted `quote_age_seconds` | `quote_clock_skew_detected` | `quote_clock_skew_seconds` |
|---|---|---|---|
| `None` (no quote_time) | `None` | `False` | `0.0` |
| `>= 0` (normal) | raw (rounded) | `False` | `0.0` |
| `< 0` (quote ts ahead of clock) | **`0.0`** | `True` | `abs(raw)` |

Both within-tolerance and beyond-tolerance negatives clamp to 0.0;
`QUOTE_AGE_CLOCK_SKEW_TOLERANCE_SECONDS` (default 2.0) documents the magnitude
distinction only. `QuoteValidation.validate` is **not** touched, so its
positive-age staleness rejection (`age > max_age_seconds`) is byte-identical
and tiny/negative skews never trigger a stale rejection.

### 12.5 Strict target-DTE reason + blocker

| Knob | Default | Effect when true |
|---|---|---|
| CLI `--strict-target-dte` | (from env) | scanner only; tri-state `store_true` |
| Env `STRICT_TARGET_DTE` | `false` | |
| YAML `scanner.expiry.strict_target_dte` | `false` | |

Precedence CLI > env > YAML > default. When `target_dte` can only be served by
an expiry FALLBACK (`expiry_decision.source ∈ {fallback,
fallback_only_available}`), strict mode:

- forces `decision.decision = "NO_TRADE"` AFTER `strat.select` (all candidate
  observability preserved; the `eff_expiry`/chain fetch is left untouched so
  there is no None-chain traceback);
- adds the `strict_target_dte_unavailable` entry to `selector_blockers`;
- overrides `expiry_selection_reason` (esr) to `strict_target_dte_unavailable`
  (wins over the `matches_target`/`fallback` derivation);
- flips `selector_eligible_base` to `False`.

An **exact** match (e.g. `--target-dte 0` resolving to `source='today'`) is NOT
a fallback, so strict is a no-op there. `pick_target_expiry` is byte-identical
(its 18 expiry tests stay green); strict enforcement lives entirely in
`run_scanner.py` + `readiness.py`.

### 12.6 New CSV/JSONL/audit fields

Six columns APPENDED at the TAIL of `_DEFAULT_RANKED_FIELDS` (existing indices
preserved, never inserted mid-list):

`bid_ask_quality_mode`, `bid_ask_quality_reason`, `quote_clock_skew_detected`,
`quote_clock_skew_seconds`, `strict_target_dte`, `strict_target_dte_passed`.

The `bid_ask_quality` SCORE reuses the existing `bid_ask_quality` column;
`quote_quality_bucket`/`quote_quality_reason` + `worst_leg_bid_ask_abs`/
`…pct_of_mid` reuse existing columns (no duplicates). `decision_log.jsonl`
auto-rides the new fields because the scanner mirrors them onto
`Candidate.meta` (like `_readiness`) and the JSONL writer embeds meta verbatim.
`snapshot_summary` also carries `strict_target_dte` + `strict_target_dte_passed`.

### 12.7 Deliberate no-touch / documented mock tweak

- `QuoteValidation.validate` (`types.py`): NOT touched.
- `src/utils/expiry.py`: NOT touched (a None expiry is silently rescued by
  `eff_expiry`, so a sentinel can't force NO_TRADE; the 18 expiry tests assert
  `source='fallback'`/`'fallback_only_available'`).
- `src/reporting/decision_log.py`: NOT touched (pass-through embeds meta).
- `src/providers/_mock_data.py`: ONE sanctioned tweak — the four legs of the
  two default-selected mock spreads (5780/5785/5815/5820) tightened
  `bid_ask_width` 0.10 → 0.02. A flat $0.10 on a sub-$1 OTM long leg (e.g. 5820
  `c_mid=0.50` → 20% of mid) is correctly `wide`/0.0 under the relative scorer
  and would otherwise break the mock smoke invariant (one CALL_CREDIT + one
  PUT_CREDIT tradeable). All mids/volumes/OI and every other strike's width are
  UNCHANGED. (The design's original "no mock change needed" premise was wrong —
  it analyzed the short anchor legs, not the worst/long legs.)

---

## 13. Phase 5 — daily trade selector (selection layer)

SELECTION ONLY — never executes/submits/previews. Lives in the PURE module
`src/selector/daily_selector.py` (no `vertical_wing` import; operates on candidate
ROW dicts + `SelectorConfig`, with `gamma_regime` passed as context). Runs in
`scripts/run_scanner.py` ONCE per tick over the union of all strategies' rows,
AFTER `compute_readiness` has stamped the rows, BEFORE the decision-log + CSV
writes — so the selector result rides into both.

### Modes + tie-breakers

| Mode | Primary | Tie-breakers |
|---|---|---|
| score_best_valid (default) | max score | credit → \|distance\| |
| best_credit_valid | max credit | score → \|distance\| |
| closest_wing_valid | min \|distance_from_spot\| | score → credit |
| farthest_wing_valid | max \|distance_from_spot\| | score → credit |
| call_credit_only | max score among CALL_CREDIT (else NO_TRADE) | credit → \|distance\| |
| put_credit_only | max score among PUT_CREDIT (else NO_TRADE) | credit → \|distance\| |
| lowest_breach_risk_valid | max composite total | breach_total → score → \|distance\| |
| regime_aligned_valid | max score when gamma_regime ∈ {positive,neutral} | credit → \|distance\| |
| no_trade | — (always NO_TRADE) | — |

`lowest_breach_risk_valid` composite (higher = safer, all components exposed in
`selector_score_components`):
```
distance_component = distance_weight * |distance_from_spot|
credit_component   = credit_weight   * credit
risk_component     = -risk_weight     * (planned_stop_risk_pct * 100)   # missing pct → 0.0 + partial=true
total              = distance_component + credit_component + risk_component
```

### Eligibility gate (shared by every *_valid + side-only mode)

Excludes (each adds a selector blocker): `rejected`; `selector_eligible_base=false`
(when `REQUIRE_SELECTOR_ELIGIBLE_BASE`); any `candidate_passes_{trade,risk,quote,
score_threshold}=false`; `quote_validation_passed=false` / bucket `invalid` (when
`REQUIRE_QUOTE_VALIDATION`); marginal / no-edge (when `REQUIRE_SCORE_EDGE`); side
disabled (`side_disabled_by_config`); `MIN/MAX_SELECTOR_{SCORE,CREDIT,
DISTANCE_FROM_SPOT}` (`selector_score_below_min`, `selector_credit_below_min`,
`selector_distance_below_min`, `selector_distance_above_max`). Both sides off →
NO_TRADE `no_sides_allowed`. Conflict (unbreakable tie at the selection boundary)
→ NO_TRADE `selector_conflict` when `NO_TRADE_ON_SELECTOR_CONFLICT`.

### Decision distinction

`pre_selector_decision` = the strategy's own `select()` outcome (untouched).
`post_selector_decision` = TRADE_CALL_CREDIT / TRADE_PUT_CREDIT / NO_TRADE derived
from the selected row's side. `selected_trade` = per-row bool (≤ MAX_TRADES_PER_DAY
true). All candidates are preserved — rejected/ineligible rows are never hidden.

### Config precedence

CLI > env > `config/scanner.yaml → scanner.selector` > default. Keys:
DAILY_TRADE_SELECTOR, MAX_TRADES_PER_DAY, ALLOW_CALL_CREDIT, ALLOW_PUT_CREDIT,
REQUIRE_SELECTOR_ELIGIBLE_BASE, REQUIRE_QUOTE_VALIDATION, REQUIRE_SCORE_EDGE,
NO_TRADE_ON_SELECTOR_CONFLICT, MIN_SELECTOR_SCORE, MIN_SELECTOR_CREDIT,
MIN_SELECTOR_DISTANCE_FROM_SPOT, MAX_SELECTOR_DISTANCE_FROM_SPOT,
LOWEST_BREACH_RISK_{DISTANCE,CREDIT,RISK}_WEIGHT.
