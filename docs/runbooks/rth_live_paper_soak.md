# RTH Live-Paper Soak Runbook

Use this workflow during regular trading hours to observe the existing local
paper lifecycle with ZeroSigma structure/Greeks and read-only Tasty quotes.

This workflow never previews, routes, or submits a broker order.

## Premarket setup

1. Open PowerShell in the repository and activate the project environment.
2. Confirm the current branch/worktree is expected.
3. Validate profiles and inspect the configured provider names.
4. Run the Greek, quote, and combined readiness diagnostics.
5. Start only when the combined readiness report says `READY`.

```powershell
cd "C:\Users\danca\Dropbox\Trading\ZeroSigma\zerosigma-algo"
.\.venv\Scripts\Activate.ps1

git status -sb
python -m scripts.manage_profiles --validate-all
Get-Content config\providers.yaml
```

Do not print `.env` or enumerate environment-variable values. The diagnostics
below report configuration presence and status without showing credentials.

## Provider and quote readiness

```powershell
python -m scripts.probe_zs_greek_api --symbol SPX --json --write-latest
python -m scripts.diagnose_tasty_quotes --symbol SPX --dte 0
python -m scripts.diagnose_cockpit_quote_status --symbol SPX --dte 0
python -m scripts.diagnose_rth_soak_readiness --profile morning_5k_call_tp75_control --symbol SPX --dte 0 --json
```

Confirm:

- ZeroSigma is configured and reachable.
- DA-GEX is available. R0 is normal before the path has accumulated observations.
- OpEx context is not unknown.
- Tasty resolves the expected root and exact target expiry.
- At least one quote passes the existing validation rules.
- The selected profile is valid and its DTE matches the quote chain.
- Paper lifecycle is enabled and the output path is writable.
- Pushover and voice show the intended enabled/disabled state.

Do not start when the readiness report says `BLOCKED`. Never bypass stale,
wide, invalid, missing-strike, root, expiry, or DTE blockers.

## Profile selection

Primary benchmark:

- `morning_5k_call_tp75_control`
- SPX 0DTE
- 1 local-paper contract

Optional comparison profile:

- `morning_2k_call_no_tp_control`

These are research benchmarks, not production approval.

## Start the RTH soak

Run the portfolio monitor in the foreground. Keeping it in the foreground makes
the owner and stop action unambiguous.

```powershell
python -m scripts.run_portfolio_forward --profiles morning_5k_call_tp75_control --interval-seconds 60 --market-hours-only --contracts 1 --output-dir outputs/portfolio_forward
```

For both benchmark profiles:

```powershell
python -m scripts.run_portfolio_forward --profiles morning_5k_call_tp75_control,morning_2k_call_no_tp_control --interval-seconds 60 --market-hours-only --contracts 1 --output-dir outputs/portfolio_forward
```

## Status and intraday review

Use a second PowerShell window for read-only status commands:

```powershell
python -m scripts.review_portfolio_forward --latest
python -m scripts.review_portfolio_forward --open latest
python -m scripts.review_portfolio_forward --events latest --limit 50
python -m scripts.review_rth_soak
Get-Content outputs\portfolio_forward\latest\heartbeat.json
```

The cockpit Paper Portfolio tab shows the Alert Center and RTH Review. Refresh
the review CLI to update its artifacts; the review never sends notifications.

## Stop and cleanup

1. Return to the PowerShell window running `run_portfolio_forward`.
2. Press `Ctrl+C` once.
3. Wait for the runner to persist its final manifest, heartbeat, ledgers, and
   local reconciliation report.
4. Confirm the latest run no longer reports `running`.

```powershell
python -m scripts.review_portfolio_forward --latest
python -m scripts.review_portfolio_forward --reconcile latest
```

Do not terminate every Python process: the cockpit or another local monitor may
also be using Python.

## EOD export and review

```powershell
python -m scripts.review_portfolio_forward --closed latest
python -m scripts.review_portfolio_forward --events latest --limit 100
python -m scripts.review_portfolio_forward --reconcile latest
python -m scripts.review_rth_soak --json

Get-ChildItem outputs\reviews\latest
Get-Content outputs\reviews\latest\rth_soak_review.md
```

Review:

- entered, held, open, and closed local-paper trades;
- TP, SL, EOD, regime/thesis, and quote exits;
- invalid, stale, wide, and missing-quote events;
- R1/R2/R3 and R4/R5/R6 sequences;
- MaxVol migrations and Greek degradation/recovery;
- delivered versus suppressed alerts and cooldown duplicates;
- whether regime exits helped or hurt realized P&L.

Backtest and learning outputs remain separate research references:

```powershell
Get-ChildItem outputs\backtests\latest
Get-ChildItem outputs\research\latest
```

Do not refresh backtests or learning outputs merely to make a soak report look
complete. Insufficient live-paper data should remain labeled insufficient.

## Troubleshooting

| Symptom | Safe action |
|---|---|
| ZeroSigma unavailable | Run `probe_zs_greek_api`; verify configuration presence without printing `.env`. |
| Greek status degraded | Review missing fields. Continue only if readiness remains `READY`; never synthesize values. |
| DA-GEX shows R0 | Allow chronological observations to accumulate. R0 at startup is expected. |
| OpEx context unknown | Stop and verify system date/calendar support. Do not assume an OpEx regime. |
| Tasty auth blocked | Run `diagnose_tasty_quotes`; correct OAuth configuration outside logs. |
| Root or expiry unavailable | Confirm market date, DTE, and SPX/SPXW listing. Do not use fallback expiry for strict 0DTE. |
| Quotes stale, wide, or invalid | Wait for healthy quotes. Do not loosen validation thresholds. |
| Missing required strikes | Wait for the requested chain or investigate structure anchors. Do not substitute strikes. |
| Duplicate alerts | Review cooldown/suppression counts in `alert_quality.csv`; do not disable journaling. |
| Review has no data | Confirm a portfolio run exists and use `review_portfolio_forward --latest`. |
| Runner appears stuck | Inspect `heartbeat.json`; stop with `Ctrl+C` in the owning terminal. |

