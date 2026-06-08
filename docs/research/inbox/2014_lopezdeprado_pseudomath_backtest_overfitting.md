# Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance

Source: Notices of the American Mathematical Society, 61(5), May 2014 (open access)
Author / Organization: David H. Bailey, Jonathan M. Borwein, Marcos López de Prado, Qiji Jim Zhu
Date: 2014
Link: https://www.ams.org/notices/201405/rnoti-p458.pdf
Local file: docs/research/inbox/2014_lopezdeprado_pseudomath_backtest_overfitting.pdf
Type: methodology
Tags: backtest overfitting, walk-forward, minimum backtest length, multiple testing, out-of-sample, Sharpe inflation
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Shows that, with enough trials, a backtest can be tuned to ANY desired in-sample Sharpe with zero true skill, and that overfit strategies tend to UNDERPERFORM out-of-sample. Introduces the "minimum backtest length" idea: the more configurations you try, the longer the sample you need before a high in-sample Sharpe is even potentially meaningful. The accessible companion to the PBO/Deflated-Sharpe papers.

## Useful concepts
- Minimum Backtest Length (MinBTL) given number of trials.
- Overfit strategies are negatively biased OOS (the more you optimise, the worse OOS).
- "How many configurations did you try?" is the central honesty question.

## Possible strategy hypotheses
- Methodological guardrail, not a trade idea: cap the number of preset/threshold configurations we compare per data length; require OOS/walk-forward confirmation before promoting a preset.

## Data required
- Just our own backtest experiment log (count of configurations tried, sample length).

## How we could test this in ZeroSigma
Adopt as a rule in the backtesting module: record # of profile/threshold combinations evaluated; compute MinBTL; refuse to "promote" a preset whose in-sample edge is within the overfitting band for the # of trials. Directly supports our "do not over-interpret SPY/QQQ provisional results" stance.

## Risks / reasons it may not work
It is a caution, not a generator of edge; easy to acknowledge and still ignore in practice.

## Decision recommendation
Keep
