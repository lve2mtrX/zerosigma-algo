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

```powershell
# Local Streamlit cockpit (placeholder UI in Phase 1)
python scripts/run_streamlit.py

# One-shot scanner pass (placeholder in Phase 1)
python scripts/run_scanner.py

# Generate EOD summary from today's outputs/
python scripts/run_eod_summary.py
```

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
