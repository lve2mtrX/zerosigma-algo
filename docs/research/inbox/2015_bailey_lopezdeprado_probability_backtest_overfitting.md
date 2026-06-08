# The Probability of Backtest Overfitting

Source: Journal of Computational Finance, 2015 (open-access author PDF, davidhbailey.com)
Author / Organization: David H. Bailey, Jonathan Borwein, Marcos López de Prado, Qiji Jim Zhu
Date: 2015
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253  ·  PDF: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
Local file: docs/research/inbox/2015_bailey_lopezdeprado_probability_backtest_overfitting.pdf
Type: methodology
Tags: backtest overfitting, PBO, combinatorially symmetric cross-validation, walk-forward, model selection
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Formal framework to estimate the Probability of Backtest Overfitting (PBO) via Combinatorially Symmetric Cross-Validation (CSCV): split the return matrix of N candidate configurations into in-sample/out-of-sample combinations and measure how often the in-sample-best config underperforms the OOS median. High PBO = your selection process is likely picking noise.

## Useful concepts
- PBO via CSCV (a concrete, computable overfitting metric across a set of configs).
- "Logit of OOS rank of the IS-best" distribution.
- Performance degradation and probability-of-loss curves.

## Possible strategy hypotheses
- Guardrail: when we compare many presets/thresholds on the same replay data, compute PBO over that config set; only trust selections with low PBO.

## Data required
- The per-configuration return series from our backtest runs (we already produce per-profile trade/P&L tables).

## How we could test this in ZeroSigma
Feed the per-profile daily P&L matrix from the backtesting module into a CSCV/PBO routine; report PBO alongside the comparison table so "best preset" comes with an overfitting probability.

## Risks / reasons it may not work
Needs several configs + enough days to be meaningful; our local data is ~6-7 months for SPX 0DTE (small for strong claims).

## Decision recommendation
Keep
