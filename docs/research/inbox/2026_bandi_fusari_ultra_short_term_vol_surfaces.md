# Ultra-short-term volatility surfaces

Source: arXiv:2603.29430 (submitted Mar 31, 2026)
Author / Organization: Federico M. Bandi, Nicola Fusari, Guido Gazzani, Roberto Renò
Date: 2026
Link: https://arxiv.org/abs/2603.29430
Local file: docs/research/inbox/2026_ultra_short_term_vol_surfaces.pdf
Type: academic paper
Tags: short-dated vol surface, skew, ATM term structure oscillations, ultra-short tenor, 0DTE/1DTE
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Studies the implied-volatility surface of options with maturities below one week ("ultra-short-term"). Documents pronounced oscillations in the at-the-money IV term structure across ultra-short tenors and proposes a joint modelling approach suited to that regime. Directly relevant to short-dated skew/surface (priority topic 7) and to the 0DTE→1DTE after-hours roll question.

## Useful concepts
- ATM IV term-structure oscillations across 0DTE/1DTE/sub-week tenors.
- Why a 1DTE surface is not just a scaled 0DTE surface (relevant to our after-hours preview roll).
- Joint surface modelling for ultra-short tenors.

## Possible strategy hypotheses
- Skew shape at 0DTE conditions which side (call-credit vs put-credit) carries more premium-per-risk → informs side selection.
- The 0DTE vs 1DTE surface difference quantifies how misleading an after-hours 1DTE preview is for a 0DTE profile (supports keeping them clearly separated, as the cockpit now does).

## Data required
- Per-strike 0DTE + 1DTE IV / bid-ask (have BID/ASK; IV would be derived); spot.

## How we could test this in ZeroSigma
Compute realised skew/term-structure from our replay chains; check whether skew sign predicts which credit side wins. Use as conceptual backing for the 0DTE-vs-1DTE preview separation.

## Risks / reasons it may not work
Very new (2026), not yet peer-reviewed; modelling is academic and heavy; our pipeline does not yet compute IV surfaces.

## Decision recommendation
Keep
