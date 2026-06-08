# Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market

Source: U.S. SEC, Division of Economic and Risk Analysis (DERA) working paper
Author / Organization: SEC DERA
Date: 2025 (file ref dera-hope-reasonable-prc-2503)
Link: https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf
Local file: NOT downloaded — SEC.gov returns HTTP 403 to automated requests (anti-bot). Publicly available; download manually in a browser. URL verified via search on 2026-06-07.
Type: exchange/regulatory research
Tags: 0DTE, customer order flow, limit orders, execution quality, net customer flow, microstructure
Quality rating: High
Relevance rating: High
Status: inbox

## Main idea
SEC DERA study of how CUSTOMERS use limit vs market orders in the 0DTE market and what that implies for execution quality and order-flow composition. Regulator-grade, neutral microstructure evidence on the customer side of 0DTE — directly relevant to net customer flow (topic 6) and execution/slippage modelling (topic 9), the two weakest-covered topics in this pass.

## Useful concepts
- Customer limit-order behaviour + fill outcomes in 0DTE.
- Order-type composition as a flow/microstructure signal.
- Execution-quality framing from a neutral regulator.

## Possible strategy hypotheses
- Limit-order usage patterns inform a realistic fill model for our spread backtests (do marketable limits at mid fill? what slippage to assume?).

## Data required
- Their findings to calibrate a slippage/fill assumption; our per-strike bid/ask to apply it.

## How we could test this in ZeroSigma
Use it to build a defensible options slippage/cost model for the backtesting module (currently a gap) — e.g., assume fills at mid ± a fraction of the bid/ask spread justified by this paper.

## Risks / reasons it may not work
Could not auto-download (SEC 403) — Dan must fetch manually. May be more descriptive than prescriptive for exact slippage numbers.

## Decision recommendation
Keep (download manually)
