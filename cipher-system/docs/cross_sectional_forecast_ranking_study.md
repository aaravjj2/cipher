# Cross-Sectional Forecast Ranking Study

## Scope

This is price-only forecast research. It uses no volume, creates no portfolio weights or orders, and does not modify the full volume-reconciled market-data gate. Holdout A (2015) was not accessed.

## Frozen Data

Development: `cross_sectional_ranking_development_20260802T194605Z.json` (six same-origin/horizon cohorts). Holdout B: `cross_sectional_ranking_holdout_b_results_20260802T194608Z.json` (four cohorts, 35 model cases). The Holdout B case roster was written before model loading.

## Results

Development mean Spearman IC: Kronos -0.011, TimesFM -0.169, momentum_20 +0.139. Holdout B mean Spearman IC: Kronos -0.180, TimesFM -0.345, ensemble -0.313, momentum_20 +0.099.

## Clustered Bootstrap

- kronos minus momentum_20: observed -0.279; 95% cohort-bootstrap CI [-0.525, -0.099] (n=4).
- timesfm minus momentum_20: observed -0.443; 95% cohort-bootstrap CI [-0.514, -0.325] (n=4).
- equal_weight_rank_ensemble minus momentum_20: observed -0.412; 95% cohort-bootstrap CI [-0.535, -0.331] (n=4).

## Verdict

Neither model is supported for cross-sectional ranking. This is a negative research result, not a model or strategy promotion. More independent market periods would be required before reassessment; the next study must reserve a new untouched period and keep this one sealed.
