# 0DTE Index Options and Market Volatility: How Large is Their Impact? (a.k.a. "How Large is the Gamma Squeeze?")

Source: SSRN working paper; open-access PDF hosted by Cboe (research_publications/gammasqueezes.pdf)
Author / Organization: Aurelio Vasquez, Diego Amaya, Neil D. Pearson, Pedro Angel Garcia-Ares
Date: 2025 (posted Feb 10, 2025)
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5113405
Local file: docs/research/inbox/2025_vasquez_pearson_0dte_index_options_market_volatility.pdf (open access via cdn.cboe.com)
Type: academic paper
Tags: 0DTE, gamma squeeze, options market maker gamma, hedge rebalancing, SPX, intraday volatility
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Quantifies the MAXIMUM plausible impact of options market makers' (OMM) 0DTE gamma hedging on S&P 500 index volatility. Uses proprietary trade data to estimate aggregate OMM position/gamma, models market volatility as a function of OMM gamma, and simulates a counterfactual where OMM gamma has no effect. Headline: the realistic impact is modest (bounded), not a runaway "gamma squeeze."

## Useful concepts
- Upper-bound estimation of hedge-rebalancing impact (counterfactual simulation).
- Aggregate OMM gamma as the key driver of any feedback.
- Short-dated options have large gamma → small position changes → large hedge trades, but net positions are small.

## Possible strategy hypotheses
- The marginal hedging-driven move per unit of net gamma gives a ceiling on how much an adverse intraday "pin/anti-pin" can move against a 0DTE credit spread — usable to size wing width vs corridor.

## Data required
- Net OMM gamma proxy (our wing-volume corridor), intraday SPX returns/realised vol.

## How we could test this in ZeroSigma
Regress our replayed intraday range on our net-gamma proxy; check whether the magnitude is consistent with "bounded impact" and whether adverse moves beyond wings are rare enough for defined-risk selling.

## Risks / reasons it may not work
Their estimate is an upper bound from proprietary data; our proxy is coarser. The paper is about index vol, not spread fills.

## Decision recommendation
Keep
