# Deep Hedging

Source: arXiv:1802.03042 (2018); published in Quantitative Finance (2019)
Author / Organization: Hans Bühler, Lukas Gonon, Josef Teichmann, Ben Wood
Date: 2018
Link: https://arxiv.org/abs/1802.03042
Local file: docs/research/inbox/2018_buehler_deep_hedging.pdf
Type: methodology (ML / hedging)
Tags: deep hedging, reinforcement learning, transaction costs, market frictions, risk measures, hedging under costs
Quality rating: High
Relevance rating: Medium
Status: inbox

## Main idea
Frames hedging a derivatives portfolio UNDER market frictions (transaction costs, liquidity limits, discrete trading) as an optimisation solved with neural networks / RL, minimising a convex risk measure of terminal P&L rather than assuming a frictionless replicating portfolio. The canonical "deep hedging" reference.

## Useful concepts
- Hedging is a cost-aware optimisation, not frictionless replication — costs change the optimal action.
- Convex risk measures (CVaR/entropic) as objectives — relevant to defined-risk P&L shaping.
- Model-agnostic policy learning from simulated paths.

## Possible strategy hypotheses
- Conceptual: our defined-risk credit spreads already cap risk; the deep-hedging lens reframes "when to exit / adjust" as a cost-aware policy. Mostly future-facing.

## Data required
- Simulated/real paths with realistic costs (we have bid/ask in TOS data for cost modelling).

## How we could test this in ZeroSigma
Not near-term and arms-length (no model training in this repo). Most useful as the reference that proper hedging/exit policy must be COST-aware — supports building a realistic options slippage/cost model for our backtests (priority topic 9, currently a gap).

## Risks / reasons it may not work
Heavy ML; far from our current rules-based pipeline; risk of over-engineering. Included as the methodological anchor for cost-aware execution, not as a build target.

## Decision recommendation
Keep
