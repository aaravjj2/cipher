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

---

## The AMZN setup pair — 2 modules, 1,689 lines

Deleted 2026-08-10. Zero importers outside the pair itself, and no importer of
either from `core/`, `scripts/`, `tests/`, `app/` or `web/src`. Verified by
whole-word search over `.py`, `.sh`, `.json`, `.mjs`, `.ts`, `.tsx` and `.ps1`.

| module | lines | what it was |
|---|---|---|
| `amzn_setup_strategy_lab.py` | 1,010 | first-pass AMZN setup research: daily/intraday signal formation against Alpaca SIP bars, next-bar-open entries, several target/stop and structure variants |
| `amzn_setup_refinement_lab.py` | 679 | second-pass validation of the first module's suggested refinements (EMA reclaim confirmation, MA pullbacks, tighter opening-range stops, VWAP confirmation, retests, failed-breakout fades) |

`amzn_setup_refinement_lab` imported from `amzn_setup_strategy_lab`; nothing
imported either. Both declared a `DEFAULT_OUTPUT` under
`data/historical_equities/` and neither path exists on disk — they were never
run. **Nothing is lost, because nothing was produced.**

---

## The watchlist-backtest set — 4 modules, 1,829 lines

Deleted 2026-08-10. Zero importers from outside the set, and no importer of the
set's entry point (`watchlist_final_strategy_backtest.py`) or of
`watchlist_history_analysis.py` from anywhere. Verified the same way as above.

| module | lines | what it was |
|---|---|---|
| `watchlist_exit_backtest.py` | 665 | scale-out/stop-move exit rules backtested against timestamped Discord watchlist alerts |
| `watchlist_indicator_exit_backtest.py` | 475 | indicator-turn (EMA/MACD) exit variant on the same alert data |
| `watchlist_final_strategy_backtest.py` | 339 | the combined final exit plan (both of the above plus posted-update scaling), the set's only entry point |
| `watchlist_history_analysis.py` | 350 | standalone Tradier option-history download/report for the watchlist, no relation to the other three beyond the shared theme |

None of the four has a test file, and none has ever produced an output artifact
on disk. **Nothing is lost, because nothing was produced.**

---

## Two dead options labs — 2 modules, 936 lines

Deleted 2026-08-10. Both zero-importer (confirmed against `core/`, `scripts/`,
`tests/`, `app/`, `web/src`, and every `.ps1`/`.sh`/systemd unit). Unlike the
sets above, both actually ran and left findings under `data/`, which is why
those findings are recorded here rather than only in the deleted code.

**`eod_best_strategy_options_lab.py`** (645 lines) translated the two locked EOD
ETF reversal strategies (`eod_best_strategy_lab`'s QQQ/IWM rules, unchanged)
into historical option outcomes across 0DTE/front/weekly/swing expirations and
ATM/OTM/vertical structures. Its run on 16 QQQ and 12 IWM signals
(`data/eod_best_strategy_options_lab/report.md`, generated 2026-07-27) found:
**no option translation cleared the preliminary robustness gate** (average,
median, exclusion-of-best-trade, and recent-performance checks) — the
underlying ETF strategies stayed materially stronger than every long-option
translation tried. The output files remain on disk; the module that produced
them does not.

**`option_outcome_factor_lab.py`** (291 lines) mined pre-entry and
delayed-confirmation factors that might separate winning from losing estimated
option outcomes, plus a small ridge fit. Its runs
(`data/factor_lab/option_outcome_factor_lab_*`, last 2026-07-22, n=16) found no
reliable rule: candidate filters split winners and losers inconsistently across
the small sample (e.g. `oi_under_1500` selected an 11-trade group with a
positive average P/L while the 5 rejected trades went the other way — not a
pattern that survives more data), and the module's own caveat notes true
scan-time option ticks were never captured, only estimated outcomes.
Inconclusive on a tiny sample, not a negative result — the difference matters
because the module cannot be re-run to get a bigger one; the option tick data
behind it was never point-in-time captured.

---

## Not deleted: the leveraged-ETF wheel set

Also proposed for deletion. The premise was wrong. All four modules are load-
bearing: `leveraged_etf_wheel_parameter_lab.py` imports directly from
`leveraged_etf_csp_wheel.py`; `leveraged_etf_wheel_iterate.py` shells out to
both `leveraged_etf_csp_wheel.py` and `leveraged_etf_wheel_download.py` by
path; `tests/test_leveraged_etf_csp_wheel.py` exercises `leveraged_etf_csp_wheel`
directly (467+ lines); and `scripts/prepare_leveraged_etf_wheel_data.py` plus
`config/leveraged_etf_wheel_universe.json` both exist specifically to feed it.

Kept, unexamined further — a set with its own test file and a dedicated data-
prep script is not a deletion candidate on this pass.
