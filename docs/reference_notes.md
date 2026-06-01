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
