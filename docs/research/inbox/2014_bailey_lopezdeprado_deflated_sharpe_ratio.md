# The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality

Source: Journal of Portfolio Management 40(5), 2014, 94-107 (open-access author PDF, davidhbailey.com)
Author / Organization: David H. Bailey, Marcos López de Prado
Date: 2014
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551  ·  PDF: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
Local file: docs/research/inbox/2014_bailey_lopezdeprado_deflated_sharpe_ratio.pdf
Type: methodology
Tags: Sharpe ratio, selection bias, multiple testing, non-normality, deflated Sharpe, false discovery
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Defines the Deflated Sharpe Ratio (DSR): adjusts an observed Sharpe for (a) the number of independent trials behind the selection, (b) skew/kurtosis of returns, and (c) sample length, to estimate the probability the true Sharpe is positive. Turns "this preset has Sharpe X" into "this preset's Sharpe survives multiple-testing + non-normal returns with probability p."

## Useful concepts
- Deflated Sharpe given N trials + higher moments.
- 0DTE credit-spread returns are highly non-normal (small wins, occasional max-loss) → vanilla Sharpe is misleading; DSR is the right correction.
- Expected maximum Sharpe under the null (selection bias benchmark).

## Possible strategy hypotheses
- Guardrail: report DSR (not raw Sharpe) when ranking presets, because our P&L distribution is fat-tailed/asymmetric by construction (defined-risk selling).

## Data required
- Per-preset return series (have), trial count (track in backtests), return skew/kurtosis (computable).

## How we could test this in ZeroSigma
Add DSR to the backtesting reports' metrics next to win-rate / total P&L / drawdown; use it as the headline "is this real?" number for preset comparison.

## Risks / reasons it may not work
Sensitive to the assumed number of independent trials; small-sample noise; still a summary statistic, not OOS proof.

## Decision recommendation
Keep
