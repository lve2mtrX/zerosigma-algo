# Does 0DTE Options Trading Increase Volatility?

Source: SSRN working paper
Author / Organization: Jonathan Brogaard, Jaehee Han, Peter Y. Won
Date: 2023 (posted 2023; abstract_id 4426358)
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4426358
Local file: NOT downloaded — SSRN requires a session/login. Metadata verified via search on 2026-06-07.
Type: academic paper
Tags: 0DTE, volatility, retail speculation, gamma hedging control, causal impact
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
The COUNTERPOINT to Dim/Eraker/Vilkov and Adams/Fontaine: finds 0DTE trading DOES increase volatility — a 1-SD increase in 0DTE trading → ~+9.1% relative to mean volatility — and that the effect survives controlling for OMM gamma hedging, attributing the residual to speculative RETAIL flow rather than dealer hedging. Important to hold both views: the literature is genuinely split, and the mechanism (dealer hedging vs retail speculation) matters.

## Useful concepts
- Effect decomposition: dealer-hedging channel vs retail-speculation channel.
- Magnitude (~+9.1% vol per 1-SD 0DTE activity) as a sizing anchor.
- Identification/causality framing (vs the correlational "propagation" tests).

## Possible strategy hypotheses
- If retail speculation drives intraday vol spikes, days with extreme retail 0DTE volume are higher-risk for short premium → a volume-spike filter could stand the strategy down.

## Data required
- 0DTE volume (have), a retail-vs-institutional split proxy (hard from our data — limitation).

## How we could test this in ZeroSigma
Use 0DTE total volume z-score as a crude "speculation" proxy; test whether high-volume days produce wider intraday range / more spread stop-outs.

## Risks / reasons it may not work
SSRN-gated. Contradicts the attenuation papers — must reconcile (regime/period/method differences). Retail/institutional split not directly observable in our data.

## Decision recommendation
Keep
