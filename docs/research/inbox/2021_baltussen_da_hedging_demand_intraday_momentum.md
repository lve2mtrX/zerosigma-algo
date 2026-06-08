# Hedging Demand and Market Intraday Momentum

Source: Journal of Financial Economics 142(1), 2021, 377-403 (open-access author PDF, Notre Dame)
Author / Organization: Guido Baltussen, Zhi Da, Sten Lammers, Martin Martens
Date: 2021
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3760365  ·  PDF: https://academicweb.nd.edu/~zda/intramom.pdf
Local file: docs/research/inbox/2021_baltussen_da_hedging_demand_intraday_momentum.pdf
Type: academic paper
Tags: intraday momentum, delta-hedging demand, short gamma, last-30-minutes, option market makers, leveraged ETFs
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Documents strong, pervasive market intraday momentum across 60+ global futures (1974-2020): the last-30-minutes return is positively predicted by the rest-of-day return, then reverts over following days. Attributes it to concentrated HEDGING DEMAND from short-gamma agents (option market makers, portfolio insurers, leveraged ETFs) whose mechanical end-of-day rebalancing has non-fundamental price impact.

## Useful concepts
- Intraday momentum: first-half-of-day return predicts last-30-min return.
- Short-gamma hedging as the mechanism; effect strongest when aggregate gamma is short.
- Reversion over subsequent days (non-fundamental impact).

## Possible strategy hypotheses
- On short-gamma days, the last 30 minutes trends in the direction of the day's move → a 0DTE EOD entry should account for "momentum into the close" (directional risk to the tested side / EOD exit timing).
- Time-of-day conditioning: EOD presets may behave differently from morning presets specifically because of this hedging-driven drift.

## Data required
- Intraday SPX returns by time-of-day (have spot series); a gamma-sign proxy (have wings).

## How we could test this in ZeroSigma
Our presets already split morning vs EOD windows. Bucket replay days by net-gamma sign and measure last-30-min drift + its effect on EOD credit-spread exit P&L (TP/SL/EOD). Could inform EOD exit timing.

## Risks / reasons it may not work
Futures-level result; index-options/credit-spread translation is indirect. Effect is statistical, not every-day.

## Decision recommendation
Keep
