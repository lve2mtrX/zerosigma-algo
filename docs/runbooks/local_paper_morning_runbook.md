# Local Paper Morning Runbook

Use this during the next RTH session before starting a local paper test.

## 1. Launch the cockpit and confirm readiness

Use the Desktop shortcut or Windows launcher:

```powershell
.\tools\windows\Launch_ZeroSigma_Algo_Cockpit.bat
```

As a terminal-only fallback, run `python -m scripts.run_streamlit`.

Confirm the Morning Startup Checklist, the app source is Live, the symbol is
SPX, and the safety banner says local paper / no broker execution.

## 2. Run RTH diagnostics

```powershell
python -m scripts.diagnose_tasty_quotes --symbol SPX --dte 0
python -m scripts.diagnose_cockpit_quote_status --symbol SPX --dte 0
python -m scripts.diagnose_live_readiness --symbol SPX --profile morning_5k_call_tp75_control --dte 0
```

Expected during RTH:

- ZerσSigma structure available.
- Tasty OAuth configured/authenticated.
- Required strikes present.
- Profile DTE equals quote-chain DTE.
- Quotes show Available.
- Start Paper Test is enabled.

If the profile is 0DTE but the quote chain is 1DTE, it is after-hours preview
only. Re-check during RTH.

## 3. Choose benchmark profile

Primary benchmark:

- `morning_5k_call_tp75_control`
- Starting account suggestion: $10,000
- Contracts: 1

Secondary benchmark:

- `morning_2k_call_no_tp_control`
- Starting account suggestion: $10,000
- Contracts: 1

These are not production-approved. They are local paper benchmarks.

## 4. Preview, then start

Use Preview Strategy first. Start Paper Test only when the Morning Startup
Checklist passes and Start Paper Test is enabled.

Do not start if quotes are stale, wide, missing required strikes, mismatched DTE,
or if ZS/Tasty auth is blocked.

