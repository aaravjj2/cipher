# Deleted modules, and what was true of them

Deletions are recorded here rather than left to `git log` alone, because the useful
question later is not "what was removed" but "was anything lost". The rule applied:
**a module is deleted only when it has no importer and its findings, if it produced
any, are already written down.** Where a module produced nothing, that fact is the
record.

One commit per family, so any revert is cheap and targeted.

---

## The advanced-analytics cluster — 11 modules, 3,446 lines

Deleted 2026-08-08. Zero importers across `core/`, `scripts/`, `tests/`, `app/`
and `web/src`. Verified by whole-word search over `.py`, `.sh`, `.json`, `.mjs`,
`.ts` and `.tsx`, not by import statements alone, so a dynamic reference would have
shown up.

| module | lines | what it was |
|---|---|---|
| `signal_aggregator.py` | 392 | composite scoring across cluster, flow and regime |
| `flow_imbalance.py` | 304 | call/put ratio, imbalance score, unusual-flow detection |
| `regime_detector.py` | 264 | adaptive volatility regime from realized vol and IV rank |
| `dynamic_strike_zones.py` | 341 | ATR-based adaptive strike-zone classification |
| `cluster_confidence.py` | 373 | bootstrap resampling and significance for clusters |
| `cluster_decay.py` | 353 | half-life and lifecycle-stage modelling |
| `smart_money_divergence.py` | 314 | large-trade detection and divergence |
| `cross_ticker_correlation.py` | 283 | cross-ticker cluster similarity and consensus |
| `pre_entry_factor_scorer.py` | 175 | pre-entry factor scoring |
| `gex_surface_interpolation.py` | 389 | RBF and linear surface fill for sparse chains |
| `gex_momentum.py` | 258 | GEX timeseries momentum and lifecycle prediction |

They formed a closed set: the only references any of them had were to each other.
`flow_imbalance` was imported only by `signal_aggregator`; `regime_detector` only
by `signal_aggregator` and `dynamic_strike_zones`; and `signal_aggregator`, which
was the piece meant to wire them together, had no importer at all.

**Nothing is lost, because nothing was produced.** These modules never ran in the
product, so they generated no result, no artifact and no finding to preserve. What
they did contain is worth recording as a caution: `signal_aggregator.py:285-298`
computed its composite score from three literals —

```
confidence_data = {"confidence_score": 0.6}
decay_data      = {"lifecycle_stage": "mature"}
divergence_data = {"divergence": "none", "confidence": 0.0}
```

— under the comment *"For now, use placeholder data … (Would integrate full modules
in production)"*, while `cluster_confidence.py`, `cluster_decay.py` and
`smart_money_divergence.py` sat in the same directory computing exactly those
quantities. `regime_detector.py:136-145` likewise returned a hardcoded affine
confidence rather than an estimated one. Had this cluster ever been switched on, it
would have reported constants as measurements.

**Not deleted, despite appearing on an earlier dead-code list:** `core/data_fetcher.py`.
An inventory reported it as having zero importers. It does not — `scripts/run_obsidian_backtest.py`
and `scripts/backtest_structural_fib.py` both use it, and `load_bars` from the
former is the loader behind every sweep in `docs/backtest-findings.md` and
`docs/strategy-catalog-findings.md`. It is load-bearing.

---

## Not deleted: the 19 PowerShell launchers

The improvement plan listed all 19 `scripts/*.ps1` for deletion on the grounds that
"this is Linux — every one is a broken path". That premise is wrong. Only **2 of 19**
contain a Windows path (`Start-CipherFrontTestSupervisor.ps1` and
`Start-ClusterForwardTest.ps1`); the rest are parameterised and take their Python
executable as an argument.

They are also the only documented way to launch roughly a dozen CLI-only modules —
`gex_capture`, `company_research_engine`, `setup_research_engine`,
`option_contract_planner`, `index_daytrade_context`, `scan_option_mark_capture`,
`tradier_stream_capture` among them. Deleting them would remove the entry point
without removing the code.

It would also contradict a decision made an hour earlier: the paper-executor port
deliberately kept Windows working (credential manager tried first,
`CIPHER_PAPER_RUNTIME` still honoured) because the browser capture that feeds it
runs on Windows. Removing the Windows launchers while preserving Windows
compatibility in the code they launch is incoherent.

Kept. If they are ever retired it should be because the Windows capture path has
been replaced, not because a Linux shell cannot run them.
