# 0DTEs Decoded: Positioning, Trends, and Market Impact

Source: Cboe Insights (exchange research)
Author / Organization: Mandy Xu (Cboe Global Markets)
Date: 2025-05-02
Link: https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact/
Local file: NOT downloaded — public web article (copyrighted; reference only). Verified live via fetch on 2026-06-07.
Type: exchange research / article
Tags: SPX 0DTE, retail participation, defined-risk/limited-risk usage, positioning, trends
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Updated Cboe positioning report: ~5x growth in SPX 0DTE over three years (~2M contracts/day), retail ≈50–60% of volume, and notably OVER 95% of 0DTE trades use limited-/defined-risk structures (spreads, condors) rather than naked options. Strong external validation that ZeroSigma's defined-risk credit-spread focus is where the actual 0DTE flow lives.

## Useful concepts
- >95% defined-risk usage → the relevant population is spreads/condors, not naked.
- Retail share + positioning trends.
- Customer profile (who is on the other side of our spreads).

## Possible strategy hypotheses
- Since defined-risk is the dominant format, liquidity/fills for spreads should be reasonable → execution assumptions for spread backtests can be less pessimistic than for naked legs (still verify with bid/ask).

## Data required
- None to consume the report; for the fill claim, our per-strike bid/ask spreads.

## How we could test this in ZeroSigma
Use as context/justification for the defined-risk product focus. Cross-check the "limited-risk dominant" claim against our own spread bid/ask widths in replay.

## Risks / reasons it may not work
Exchange research; descriptive, not a signal; figures are aggregate.

## Decision recommendation
Keep
