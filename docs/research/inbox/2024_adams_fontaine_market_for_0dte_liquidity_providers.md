# The Market for 0DTE: The Role of Liquidity Providers in Volatility Attenuation

Source: SSRN working paper (authors affiliated incl. Bank of Canada)
Author / Organization: Greg Adams, Jean-Sebastien Fontaine, Chayawat Ornthanalai
Date: 2024 (posted May 3, 2024)
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4881008
Local file: NOT downloaded — SSRN requires a session/login (no open-access mirror found). Metadata verified via search on 2026-06-07.
Type: academic paper
Tags: 0DTE, liquidity provision, volatility attenuation, dealer net gamma/delta, intraday reversal, term-structure roll-down
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Argues 0DTE liquidity providers ATTENUATE index volatility: index vol falls ~60–90 annualised bps on 0DTE-active days. Mechanism: intermediaries' longer-dated positions accumulate before expiry and "roll down" the term structure, raising instantaneous hedging needs in a direction that forces them to trade AGAINST contemporaneous index moves (liquidity provision) → stronger intraday order-flow reversals, muted momentum, lower vol. Attenuation comes from multi-day inventory dynamics, not from same-day 0DTE per se.

## Useful concepts
- Roll-down of longer-dated inventory into 0DTE exposure as the driver (not just expiration-day trades).
- Counter-directional MM trading = intraday mean reversion.
- Volatility attenuation (opposes the "0DTE adds vol" camp; complements Dim/Eraker/Vilkov).

## Possible strategy hypotheses
- On attenuation-regime days (net gamma forcing reversal), intraday mean reversion favours defined-risk premium selling (range-bound) → higher credit-spread win rate.
- Reversal signal could complement the Baltussen/Da momentum signal (they may apply in different gamma regimes).

## Data required
- Net dealer gamma/delta proxy (our wings), intraday SPX order-flow/returns.

## How we could test this in ZeroSigma
Classify replay days as reversal-prone vs momentum-prone via our net-gamma proxy; compare credit-spread outcomes. Pairs naturally with the Baltussen/Da note.

## Risks / reasons it may not work
SSRN-gated (read abstract; full text needs download by Dan). Uses richer position data than our proxy. Regime classification is the hard part.

## Decision recommendation
Keep
