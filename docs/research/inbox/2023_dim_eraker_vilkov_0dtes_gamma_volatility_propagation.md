# 0DTEs: Trading, Gamma Risk and Volatility Propagation

Source: SSRN working paper (also Western Finance Association program copy)
Author / Organization: Chukwuma Dim, Bjorn Eraker, Grigory Vilkov
Date: 2023 (first posted Nov 17, 2023)
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4692190
Local file: docs/research/inbox/2023_dim_eraker_vilkov_0dtes_gamma_volatility_propagation.pdf (open-access conference copy via westernfinance-portal.org)
Type: academic paper
Tags: 0DTE, dealer gamma, market-maker inventory, volatility propagation, intraday volatility, SPX
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Examines the explosion in same-day-expiry SPX option trading and tests whether 0DTE activity destabilises the index. Finds market makers' net inventory (net gamma) is on average POSITIVE and negatively related to future intraday volatility. High open-interest gamma in 0DTEs does NOT propagate to higher realised volatility, and intraday 0DTE volume shocks do NOT amplify recent index returns — i.e. little support for the "0DTE makes markets fragile / gamma squeeze" narrative.

## Useful concepts
- Net dealer gamma sign + magnitude as a state variable (positive net gamma → stabilising hedging).
- Distinction between OPEN-INTEREST gamma vs intraday VOLUME shocks.
- Volatility propagation test design (does gamma today predict vol later?).

## Possible strategy hypotheses
- When estimated net dealer gamma is positive (stabilising), 0DTE defined-risk premium selling (credit spreads / iron condors) should see calmer intraday paths → higher win rate / less stop-out.
- Negative/short net-gamma regimes should correlate with larger intraday range → widen wings or stand down.

## Data required
- Per-strike 0DTE open interest + volume (we have SPX per-strike CALL/PUT volume in TOS Data).
- Intraday SPX returns / realised vol (have spot series).
- A net-gamma proxy from our wing/exposure data.

## How we could test this in ZeroSigma
Use the backtesting module to bucket replay days by our computed net-gamma / WDS corridor sign, then compare credit-spread win rate, P&L, and intraday MFE/MAE across buckets. Aligns directly with our corridor/WDS structure read.

## Data required / Risks
Their net-gamma is from proprietary OMM position data; our proxy is volume-threshold wings, so directional alignment is an assumption to validate, not a given.

## Risks / reasons it may not work
Proxy mismatch (our wing-volume net gamma vs their true OMM gamma); regime dependence; result is about index vol, not directly about our spread P&L.

## Decision recommendation
Keep
