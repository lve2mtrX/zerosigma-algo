# Research Inbox Index — Pass 1: 0DTE / Dealer Gamma / Options Microstructure

Collected: 2026-06-07 · Curator pass: automated discovery + **independent verification** (human review pending).

**What this is:** a curated candidate library of external references for ZeroSigma's SPX/SPY/QQQ 0DTE
defined-risk credit-spread research. These are inputs to human-guided review — **not** model training data,
**not** approved strategy changes, **not** execution logic.

**How gathered + verified:** discovery via web search across the priority topics; every KEEP was
independently verified before saving — open-access PDFs were downloaded and checked for a real `%PDF`
header + sane size; arXiv IDs and Cboe pages were confirmed by fetching the source page (title/author/year
match); SSRN abstract IDs were corroborated by title search (SSRN itself returns HTTP 403 to automated
fetches). No paywalls were bypassed; no copyrighted full text was saved unless open-access. **Nothing
committed or pushed.**

Counts: **~23 candidates** surfaced · **10 open-access PDFs downloaded** · **15 KEEP** (10 with local PDF,
5 reference-only) · 8 MAYBE · 7 rejected.

---

## Summary table (KEEP)

| # | Title | Author / Org | Year | Type | Quality | Relevance | Local PDF | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | 0DTEs: Trading, Gamma Risk and Volatility Propagation | Dim, Eraker, Vilkov | 2023 | academic | High | High | yes | inbox |
| 2 | 0DTE Index Options and Market Volatility: How Large is Their Impact? | Vasquez, Amaya, Pearson, Garcia-Ares | 2025 | academic | High | High | yes | inbox |
| 3 | 0DTE Option Pricing | Bandi, Fusari, Renò | 2024 | academic | High | High | yes | inbox |
| 4 | Ultra-short-term volatility surfaces | Bandi, Fusari, Gazzani, Renò | 2026 | academic | High | High | yes | inbox |
| 5 | Differential ML for 0DTE Options (Stoch Vol + Jumps) | Sakuma | 2026 | academic/ML | Medium | Medium | yes | inbox |
| 6 | Hedging Demand and Market Intraday Momentum | Baltussen, Da, Lammers, Martens | 2021 | academic | High | High | yes | inbox |
| 7 | Deep Hedging | Bühler, Gonon, Teichmann, Wood | 2018 | methodology/ML | High | Medium | yes | inbox |
| 8 | Pseudo-Mathematics and Financial Charlatanism (backtest overfitting) | Bailey, Borwein, López de Prado, Zhu | 2014 | methodology | High | High | yes | inbox |
| 9 | The Probability of Backtest Overfitting | Bailey, Borwein, López de Prado, Zhu | 2015 | methodology | High | High | yes | inbox |
| 10 | The Deflated Sharpe Ratio | Bailey, López de Prado | 2014 | methodology | High | High | yes | inbox |
| 11 | Much Ado About 0DTEs — Market Impact of SPX 0DTE | Mandy Xu / Cboe | 2023 | exchange research | High | High | no (web) | inbox |
| 12 | 0DTEs Decoded: Positioning, Trends, and Market Impact | Mandy Xu / Cboe | 2025 | exchange research | High | High | no (web) | inbox |
| 13 | The Market for 0DTE: Liquidity Providers in Volatility Attenuation | Adams, Fontaine, Ornthanalai | 2024 | academic | High | High | no (SSRN) | inbox |
| 14 | Does 0DTE Options Trading Increase Volatility? | Brogaard, Han, Won | 2023 | academic | High | High | no (SSRN) | inbox |
| 15 | Hope at a Reasonable Price: Customer Limit Orders in 0DTE | SEC DERA | 2025 | regulatory research | High | High | no (SEC 403) | inbox |

Note files: one `YYYY_author_short_title.md` per row above, in this folder.

---

## Detailed entries (KEEP)

### 1. 0DTEs: Trading, Gamma Risk and Volatility Propagation
- Author/Org: Chukwuma Dim, Bjorn Eraker, Grigory Vilkov · Year: 2023
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4692190 (OA conf PDF via westernfinance-portal.org)
- Local: `2023_dim_eraker_vilkov_0dtes_gamma_volatility_propagation.pdf`
- Type: academic paper · Tags: 0DTE, dealer gamma, volatility propagation, MM inventory
- Quality: High · Relevance: High
- Why it matters: Core evidence that 0DTEs do NOT propagate volatility; net dealer gamma is usually positive (stabilising) — directly informs the corridor/WDS net-gamma read.
- Testable hypothesis: Positive-net-gamma replay days → higher credit-spread win rate / lower intraday range than negative-net-gamma days.

### 2. 0DTE Index Options and Market Volatility: How Large is Their Impact?
- Author/Org: Aurelio Vasquez, Diego Amaya, Neil D. Pearson, Pedro A. Garcia-Ares · Year: 2025
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5113405 (OA PDF via cdn.cboe.com/.../gammasqueezes.pdf)
- Local: `2025_vasquez_pearson_0dte_index_options_market_volatility.pdf`
- Type: academic paper · Tags: gamma squeeze, OMM gamma, hedge rebalancing
- Quality: High · Relevance: High
- Why it matters: Upper-bound estimate of the "gamma squeeze" — bounds how far hedging can move spot against a 0DTE spread; informs wing-width vs corridor sizing.
- Testable hypothesis: Adverse intraday moves beyond our wing width are rare enough (given bounded hedging impact) that defined-risk selling has positive expectancy in active corridors.

### 3. 0DTE Option Pricing
- Author/Org: Federico Bandi, Nicola Fusari, Roberto Renò · Year: 2024
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4503344 (OA conf PDF via northernfinanceassociation.org)
- Local: `2024_bandi_fusari_reno_0dte_option_pricing.pdf`
- Type: academic paper · Tags: 0DTE pricing, ultra-short tenor, characteristic function
- Quality: High · Relevance: High
- Why it matters: A τ→0 fair-value model → cleaner rich/cheap signal for spread legs vs our current mid-based credit.
- Testable hypothesis: Legs flagged "rich" by a 0DTE model fair value earn more credit-per-risk; selecting rich short legs improves expectancy.

### 4. Ultra-short-term volatility surfaces
- Author/Org: Federico Bandi, Nicola Fusari, Guido Gazzani, Roberto Renò · Year: 2026
- Source: https://arxiv.org/abs/2603.29430 · Local: `2026_ultra_short_term_vol_surfaces.pdf`
- Type: academic paper · Tags: short-dated vol surface, skew, ATM term-structure oscillation
- Quality: High · Relevance: High
- Why it matters: Short-dated skew/surface (topic 7) + quantifies how different a 1DTE surface is from 0DTE (backs the cockpit's 0DTE-vs-1DTE after-hours-preview separation).
- Testable hypothesis: 0DTE skew sign predicts which credit side (call vs put) carries more premium-per-risk.

### 5. Differential Machine Learning for 0DTE Options with Stochastic Volatility and Jumps
- Author/Org: Takayuki Sakuma · Year: 2026
- Source: https://arxiv.org/abs/2603.07600 · Local: `2026_differential_ml_0dte.pdf`
- Type: academic paper (ML) · Tags: differential ML, Greeks, PIDE, 0DTE pricing
- Quality: Medium · Relevance: Medium
- Why it matters: Fast/accurate 0DTE Greeks could feed gamma/vega into scoring. Methodology reference only (no model training in this repo).
- Testable hypothesis: n/a (methodology); future fair-value/Greeks overlay.

### 6. Hedging Demand and Market Intraday Momentum
- Author/Org: Baltussen, Da, Lammers, Martens · Year: 2021 (JFE 142(1):377-403)
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3760365 (OA PDF: academicweb.nd.edu/~zda/intramom.pdf)
- Local: `2021_baltussen_da_hedging_demand_intraday_momentum.pdf`
- Type: academic paper · Tags: intraday momentum, short gamma, last-30-min, EOD
- Quality: High · Relevance: High
- Why it matters: Short-gamma hedging drives last-30-min momentum → directly relevant to EOD preset exit timing/risk.
- Testable hypothesis: On short-gamma days the last 30 min trends with the day's move; EOD credit-spread exit timing should adjust accordingly.

### 7. Deep Hedging
- Author/Org: Bühler, Gonon, Teichmann, Wood · Year: 2018
- Source: https://arxiv.org/abs/1802.03042 · Local: `2018_buehler_deep_hedging.pdf`
- Type: methodology/ML · Tags: cost-aware hedging, risk measures, RL
- Quality: High · Relevance: Medium
- Why it matters: Anchor that hedging/exit policy must be COST-aware — supports building a realistic options slippage model for backtests (topic 9 gap).
- Testable hypothesis: n/a (methodology); informs cost-aware exit/slippage modelling.

### 8. Pseudo-Mathematics and Financial Charlatanism
- Author/Org: Bailey, Borwein, López de Prado, Zhu · Year: 2014 (Notices of the AMS)
- Source: https://www.ams.org/notices/201405/rnoti-p458.pdf · Local: `2014_lopezdeprado_pseudomath_backtest_overfitting.pdf`
- Type: methodology · Tags: backtest overfitting, minimum backtest length, multiple testing
- Quality: High · Relevance: High
- Why it matters: The honesty guardrail for our preset comparisons — minimum backtest length given # of configs tried; supports "don't over-interpret provisional SPY/QQQ results."
- Testable hypothesis: n/a (guardrail); cap configs vs sample length; require walk-forward before promoting a preset.

### 9. The Probability of Backtest Overfitting
- Author/Org: Bailey, Borwein, López de Prado, Zhu · Year: 2015
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 (OA PDF: davidhbailey.com/dhbpapers/backtest-prob.pdf)
- Local: `2015_bailey_lopezdeprado_probability_backtest_overfitting.pdf`
- Type: methodology · Tags: PBO, CSCV, walk-forward
- Quality: High · Relevance: High
- Why it matters: Computable PBO metric over our per-profile P&L matrix → report alongside the backtest comparison table.
- Testable hypothesis: n/a (guardrail); compute PBO across our preset set; distrust low-PBO-failing selections.

### 10. The Deflated Sharpe Ratio
- Author/Org: Bailey, López de Prado · Year: 2014 (JPM 40(5):94-107)
- Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 (OA PDF: davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Local: `2014_bailey_lopezdeprado_deflated_sharpe_ratio.pdf`
- Type: methodology · Tags: deflated Sharpe, selection bias, non-normality
- Quality: High · Relevance: High
- Why it matters: Our credit-spread P&L is fat-tailed/asymmetric → raw Sharpe is misleading; DSR is the right "is this real?" stat for preset ranking.
- Testable hypothesis: n/a (guardrail); add DSR to backtest reports' metrics.

### 11. Much Ado About 0DTEs — Evaluating the Market Impact of SPX 0DTE Options
- Author/Org: Mandy Xu / Cboe · Year: 2023 · Source: https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/
- Local: none (public web article; reference only — verified live 2026-06-07)
- Type: exchange research · Quality: High · Relevance: High
- Why it matters: Net OMM exposure is tiny (≈0.04–0.17%) because customer flow is balanced — frames the net-flow-imbalance question (topic 6).
- Testable hypothesis: Rare high-imbalance 0DTE-flow days are where our spreads stop out; a balance filter helps.

### 12. 0DTEs Decoded: Positioning, Trends, and Market Impact
- Author/Org: Mandy Xu / Cboe · Year: 2025 · Source: https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact/
- Local: none (public web article; reference only — verified live 2026-06-07)
- Type: exchange research · Quality: High · Relevance: High
- Why it matters: >95% of 0DTE trades are defined-/limited-risk → external validation of ZeroSigma's defined-risk focus.
- Testable hypothesis: Spread bid/ask in our replay data is tight enough that defined-risk fills are realistic.

### 13. The Market for 0DTE: The Role of Liquidity Providers in Volatility Attenuation
- Author/Org: Adams, Fontaine, Ornthanalai · Year: 2024 · Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4881008
- Local: none (SSRN-gated; reference only — metadata verified via search)
- Type: academic paper · Quality: High · Relevance: High
- Why it matters: Vol falls 60–90 bps on 0DTE days via MM counter-directional hedging (intraday reversal) — reversal regime favours range-bound premium selling.
- Testable hypothesis: Reversal-regime days (net-gamma forcing reversal) → higher credit-spread win rate than momentum-regime days.

### 14. Does 0DTE Options Trading Increase Volatility?
- Author/Org: Brogaard, Han, Won · Year: 2023 · Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4426358
- Local: none (SSRN-gated; reference only — metadata verified via search)
- Type: academic paper · Quality: High · Relevance: High
- Why it matters: The COUNTERPOINT (0DTE → +vol, driven by retail speculation). The literature is genuinely split — hold both views.
- Testable hypothesis: Extreme 0DTE-volume (speculation) days produce wider range / more stop-outs; a volume-spike stand-down filter helps.

### 15. Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market
- Author/Org: SEC DERA · Year: 2025 · Source: https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf
- Local: none (SEC.gov 403 to automation — download manually in a browser; URL verified via search)
- Type: regulatory research · Quality: High · Relevance: High
- Why it matters: Neutral, regulator-grade evidence on customer order flow + execution in 0DTE — best fit for topics 6 (net customer flow) and 9 (execution/slippage), our two weakest-covered topics.
- Testable hypothesis: Calibrate a realistic spread fill/slippage model (fills at mid ± fraction of bid/ask) for the backtesting module.

---

## MAYBE candidates (indexed; not downloaded this pass)

| Title | Author / Org | Year | Source | Quality | Relevance | Why / caveat |
|---|---|---|---|---|---|---|
| Do S&P500 Options Increase Market Volatility? Evidence from 0DTEs | Adams, Dim, Eraker, Fontaine, Ornthanalai, Vilkov | 2025 | SSRN 5641974 | High | Medium | Newer combined paper merging the propagation/attenuation camps; SSRN-gated. Revisit when reconciling the split literature. |
| Intraday Jumps and 0DTE Options: Pricing and Hedging Implications | Miloš Božović | 2025 | SSRN 5223127 | Medium | Medium | SV + Poisson-jump model for 0DTE pricing/hedging; SSRN-gated; overlaps Bandi/Fusari/Renò. |
| Does Option Trading Have a Pervasive Impact on Underlying Stock Prices? | Ni, Pearson, Poteshman, White | 2021 | RFS (academic.oup.com) | High | Medium | The "expected hedging demand" mechanism; PAYWALLED at OUP (an OA working-paper version may exist — find before keeping). |
| Zero DTE Options Gamma Hedging | Dmitry Garmash | 2024 | SSRN 5329719 | Medium | Low-Med | Sensitivity of 0DTE gamma-hedging profitability; SSRN-gated; practitioner-flavoured. |
| Options Strategies Quick Guide (defined-risk: spreads/condors) | Options Industry Council (OIC) | n/a | optionseducation.org/referencelibrary | Medium | Medium | Educational reference for defined-risk structures; documentation, not a study. |
| Henry Schwartz's Zero-Day SPX Iron Condor Strategy: A Deep Dive | Cboe Insights | 2025 | cboe.com/insights | Medium | Medium | Concrete 0DTE iron-condor framing (defined-risk, topic 8); exchange article, illustrative not rigorous. |
| 0DTE Trading Rules | Grigory Vilkov | 2023 | SSRN 4641356 | Medium | Medium | Practitioner ruleset from a core 0DTE researcher; SSRN-gated; verify before relying. |
| Stock Price Clustering on Option Expiration Dates | Ni, Pearson, Poteshman | 2005 | SSRN 519044 | Medium | Low | Classic expiration-day pinning/hedging; pre-0DTE era — revisit only for a pinning sub-study. |

---

## Rejected candidates (NOT downloaded)

| Title / Source | Why rejected |
|---|---|
| "Where Does Gamma Hedge Drive the Intraday Market Move?" (afajof.org/management/viewp.php?n=129472) | Source URL is an AFA submission-portal node, not a stable citation; could not cleanly verify author/venue. Superseded by the verified Baltussen/Da and Adams/Fontaine papers. |
| "Walk-Forward Optimization: How It Works…" (blog.quantinsti.com) | Educational blog, not a primary methodology source. The López de Prado PBO / Deflated-Sharpe / AMS papers cover overfitting + validation rigorously. |
| "The Rise of 0DTE Options: Cause for Concern…" (harbourfront/relative-value substack/blog) | Low-quality aggregator blog; no original data/methodology. |
| "0DTE Options: The Hidden Driver of Market Volatility" (ebc.com) | Broker marketing blog; no methodology. |
| "0DTE Gamma Exposure Explained / 2026" (tradeedgepro.net) | SEO content blog; not a reference. |
| "Do S&P500 0DTEs Options Increase Market Volatility?" (quantpedia.com) | Aggregator summary of an SSRN paper — kept the underlying paper (MAYBE 5641974), rejected the aggregator. |
| "Evaluating LLM Detection of Gamma Exposure Patterns" (arXiv 2512.17923) | Off-topic (LLM evaluation), not relevant to 0DTE microstructure/strategy. |

---

## Priority-topic coverage map

| # | Priority topic | Covered | Best source(s) |
|---|---|---|---|
| 1 | SPX 0DTE options | ✅ | Dim/Eraker/Vilkov; Vasquez/Pearson; Cboe (Xu) |
| 2 | 0DTE market impact | ✅ | Vasquez/Pearson; Dim/Eraker/Vilkov; Cboe; (counter) Brogaard; Adams/Fontaine |
| 3 | dealer gamma / MM hedging | ✅ | Vasquez/Pearson; Adams/Fontaine; Baltussen/Da |
| 4 | gamma exposure & intraday vol | ✅ | Dim/Eraker/Vilkov; Vasquez/Pearson |
| 5 | delta-hedging demand & intraday momentum | ✅ | Baltussen/Da (downloaded) |
| 6 | options volume imbalance / net customer flow | 🟡 partial | SEC DERA (couldn't auto-download); Cboe "Much Ado" (balanced flow) |
| 7 | short-dated vol surface / skew | ✅ | Ultra-short-term vol surfaces; Bandi/Fusari/Renò |
| 8 | 0DTE credit spreads / iron condors / defined-risk | 🟡 partial | Cboe "0DTEs Decoded" (>95% defined-risk); Cboe iron-condor article; OIC guide — no rigorous study yet |
| 9 | execution / slippage modeling for options backtests | 🟡 partial / GAP | SEC DERA limit-orders (manual download); Deep Hedging (cost-aware framing) — no dedicated options-backtest-slippage paper found |
| 10 | walk-forward / overfitting controls | ✅ strong | López de Prado AMS; Probability of Backtest Overfitting; Deflated Sharpe Ratio |

Gaps for **Pass 2**: a dedicated options-backtest execution/slippage methodology paper; a rigorous
defined-risk (iron-condor / credit-spread) premium-selling study; an OA copy of the SEC DERA paper and
of "Does Option Trading Have a Pervasive Impact…".

---

## Best extracted hypotheses (cross-source)

1. **Net-gamma regime gates spread expectancy.** Bucket replay days by our wing-volume net-gamma / corridor
   sign; expect higher credit-spread win rate + lower intraday range on positive-/stabilising-gamma days
   (Dim/Eraker/Vilkov + Vasquez/Pearson). *Testable now with the backtesting module.*
2. **Reversal vs momentum regime split.** Adams/Fontaine (attenuation/reversal) and Baltussen/Da (short-gamma
   momentum) may apply in different gamma regimes; classify days and test whether range-bound premium selling
   wins in reversal regimes and loses in momentum regimes.
3. **Volume-spike stand-down.** Brogaard et al. attribute residual +vol to speculative retail; test whether a
   0DTE-volume z-score filter (stand down on extreme-volume days) reduces stop-outs.
4. **Skew-conditioned side selection.** Ultra-short-term skew sign predicts which credit side carries more
   premium-per-risk (Ultra-short vol surfaces / Bandi-Fusari-Renò).
5. **Overfitting guardrails as first-class metrics.** Add PBO + Deflated Sharpe to backtest reports and enforce
   minimum-backtest-length before promoting any preset (López de Prado trio) — protects the SPY/QQQ provisional
   results from over-interpretation.
6. **Cost-aware fills.** Build a defensible spread slippage model (fills at mid ± fraction of bid/ask) grounded
   in the SEC DERA limit-order evidence + Deep Hedging's cost-aware framing (topic-9 gap).

---

Status legend: inbox / reviewed / rejected / promoted. All KEEP entries are **inbox** pending Dan's review.
Nothing here changes strategy, selector, or risk logic. No commit / no push.
