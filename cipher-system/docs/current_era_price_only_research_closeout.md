# Current-Era Price-Only Research Closeout

**Run date:** 2026-08-03  
**Scope:** frozen 2023-2025 Holdout C price-only panel, nine symbols, no volume features or volume evaluation.

## Model study

The pre-registered study contains four origins, nine symbols, and horizons of 5 and 20 sessions: 72 model cases. Origins are 2023-05-03, 2024-01-11, 2024-09-12, and 2025-06-17. TimesFM used native p10-p90 quantiles. Kronos-mini used ten fixed seeds and empirical p10-p90 intervals.

The immutable result is `data/market_quality/current_era_price_only_model_results_20260803T122526Z.json`.

| Model | Cases | Naive wins | Win rate | 80% interval coverage | MAE |
|---|---:|---:|---:|---:|---:|
| TimesFM | 72 | 29 | 40.28% | 40.28% | 16.5239 |
| Kronos-mini | 72 | 31 | 43.06% | 18.06% | 16.9052 |

At horizon 5, TimesFM won 44.44% and Kronos-mini 50.00%. At horizon 20, both won 36.11%. The earlier TimesFM promise does not hold in this less-correlated current-era expansion. Both interval systems materially under-cover their nominal 80% interval. Neither is a paper candidate.

## Qlib and RD-Agent(Q)

The Qlib-compatible adapter produced `data/market_quality/current_era_price_only_qlib_panel.parquet` with 13,410 daily rows and only `datetime`, `instrument`, `open`, `high`, `low`, and `close`. Four pre-registered context-only candidates were written through the immutable factor artifact service and screened chronologically:

* 20-session momentum.
* 5-session reversal.
* 10/30-session price trend.
* volatility-adjusted 5-session return.

The durable screen report is the timestamped `data/market_quality/price_only_factor_screen_*.json` artifact. Development evidence was restricted to 2023-2024 and untouched OOS evidence to 2025. No candidate has a stable OOS rank-IC improvement across both horizons, so no factor was elevated beyond context or made promotion eligible.

RD-Agent(Q) and Qlib are importable after the narrow `pydantic-ai-slim==1.107.1` compatibility repair. The LLM-backed RD-Agent loop was not run because no configured model endpoint was available in the local environment. The deterministic screen is recorded as an adapter validation, not as an RD-Agent discovery result.

## VectorBT and LEAN boundary

VectorBT now has an explicit price-only signal contract. It records `data_scope=price_only`, rejects volume use by contract, and points to the same ordered promotion states used by other validated strategies. The screen itself remains non-promotable until it has the existing chronological walk-forward, regime, event/quiet, statistical, and LEAN reconciliation evidence. No strategy cleared those gates in this run, and no paper or live execution was enabled.

## Volume-sensitive track

The full volume gate, including the unchanged 5% reconciliation threshold, remains intact. Volume-sensitive backtesting, liquidity filtering, volume-based ranking, and promotion based on volume remain blocked pending an independent, semantically comparable one-minute reference. No daily volume substitution, vendor patching, scaling, inferred volume, or threshold relaxation was used here.
