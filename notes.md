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
