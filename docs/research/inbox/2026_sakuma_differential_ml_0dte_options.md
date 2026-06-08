# Differential Machine Learning for 0DTE Options with Stochastic Volatility and Jumps

Source: arXiv:2603.07600 (submitted Mar 8, 2026)
Author / Organization: Takayuki Sakuma
Date: 2026
Link: https://arxiv.org/abs/2603.07600
Local file: docs/research/inbox/2026_differential_ml_0dte.pdf
Type: academic paper (ML / pricing methodology)
Tags: 0DTE, differential machine learning, stochastic volatility, jumps, PIDE, Greeks, ML hedging methodology
Quality rating: Medium
Relevance rating: Medium
Status: inbox

## Main idea
Applies differential machine learning (supervise on prices AND Greeks, plus a PIDE-residual penalty) to price 0DTE options under a stochastic-volatility jump-diffusion model. Uses a Black-Scholes-form representation with a maturity-gated variance correction to handle the τ→0 regime.

## Useful concepts
- Differential ML: train on price + pathwise Greeks → better, smoother Greeks (relevant if we ever need fast Greeks for hedging/score).
- Maturity-gated variance correction for ultra-short maturity.
- PIDE-residual regularisation (physics/PDE-informed loss).

## Possible strategy hypotheses
- A fast, accurate 0DTE Greeks engine could feed a cleaner gamma/vega read into candidate scoring without re-deriving from chains.

## Data required
- Model-simulated data for training (per the paper); our use would be conceptual.

## How we could test this in ZeroSigma
Not near-term. Methodology reference only — relevant if we add an ML pricing/Greeks overlay. Arms-length: we do not train models in this repo.

## Risks / reasons it may not work
Single-author, brand-new (2026), not peer-reviewed; ML pricing adds complexity and overfitting risk; out of scope for the current pipeline.

## Decision recommendation
Maybe
