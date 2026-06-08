# Volatility Insights: Much Ado About 0DTEs — Evaluating the Market Impact of SPX 0DTE Options

Source: Cboe Insights (exchange research)
Author / Organization: Mandy Xu (Cboe Global Markets)
Date: 2023-09-08
Link: https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/
Local file: NOT downloaded — public web article (copyrighted; reference only). Verified live via fetch on 2026-06-07.
Type: exchange research / article
Tags: SPX 0DTE, market impact, market maker net exposure, customer flow balance, intraday volatility
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
Cboe's own analysis: although SPX 0DTE options were ~40%+ of SPX volume, options market maker NET exposure is tiny (≈0.04%–0.17% of daily S&P futures liquidity) because customer flows are roughly balanced across buyers/sellers and calls/puts. Concludes no discernible market disruption from 0DTEs; intraday vol and price patterns in line with history. The exchange-side counterweight to "gamma squeeze" fears.

## Useful concepts
- NET (not gross) MM exposure is what matters — balanced 2-sided customer flow nets down.
- 0DTE volume share ≠ destabilising hedging pressure.
- Frames the "net customer flow imbalance" question (priority topic 6).

## Possible strategy hypotheses
- If net 0DTE flow is usually balanced, large directional hedging cascades against a defined-risk spread should be rare → supports premium selling, but watch the unbalanced-flow days.

## Data required
- 0DTE buy/sell + call/put volume imbalance (we have per-strike CALL/PUT volume → can build an imbalance proxy).

## How we could test this in ZeroSigma
Build a daily net-flow-imbalance proxy from our volume data; check whether the rare high-imbalance days are where our spreads get stopped out.

## Risks / reasons it may not work
Exchange research (promotional framing risk); aggregate claim, not per-day tradeable signal; from the venue with an interest in 0DTE growth.

## Decision recommendation
Keep
