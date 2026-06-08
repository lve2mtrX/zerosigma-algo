# 0DTE Option Pricing

Source: SSRN working paper (draft Mar 15, 2024; first draft Jul 7, 2023); open-access Northern Finance Association program copy
Author / Organization: Federico M. Bandi, Nicola Fusari, Roberto Renò
Date: 2024
Link: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4503344
Local file: docs/research/inbox/2024_bandi_fusari_reno_0dte_option_pricing.pdf (open access via portal.northernfinanceassociation.org)
Type: academic paper
Tags: 0DTE, option pricing, ultra-short tenor, characteristic function, Edgeworth expansion, hedging
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Develops a local-in-time pricing approach for ultra-short-tenor (0DTE) options using Edgeworth-like expansions of the log-return characteristic function. Reports material improvements in pricing AND hedging of 0DTE options vs state-of-the-art models, which mis-handle the ultra-short-maturity regime.

## Useful concepts
- Why standard models fail at τ→0 (the maturity-gated variance/jump behaviour).
- Local-in-time expansion as a tractable 0DTE pricer.
- Pricing + hedging improvements are reported jointly.

## Possible strategy hypotheses
- A better 0DTE fair-value model gives a cleaner "rich/cheap" signal for the legs of a credit spread → improve entry selection vs our current mid-based credit.
- Mispricing of wings (tails) at τ→0 could be where defined-risk sellers earn or lose edge.

## Data required
- Per-strike 0DTE bid/ask/mid (have CALL/PUT BID/ASK in TOS Data); spot; intraday timestamps.

## How we could test this in ZeroSigma
Not a near-term implementation (model is involved). Use as a reference if we add a fair-value overlay to candidate scoring; compare model mid vs market mid on replay data to flag rich/cheap legs.

## Risks / reasons it may not work
Implementation complexity; needs careful calibration; our pipeline currently uses market mids, not a model price. Pricing edge ≠ net edge after costs.

## Decision recommendation
Keep
