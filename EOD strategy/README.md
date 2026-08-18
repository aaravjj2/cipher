# EOD strategy

Dedicated, read-only research workspace for the pasted **Obsidian EOD Algo
Strategy v4.1** conversion.

## Scope

- 1-minute bars, final 30 minutes of the US session (the original primary).
- 1-minute bars, **full session** — signals allowed all day.
- Session-aligned 5-minute bars with a longer EOD window.
- Session-aligned 5-minute bars, **full session**.
- Symbols: 41, spanning broad and sector ETFs, financials, energy, healthcare, consumer,
  industrials and tech. The universe defaults to whatever the archive holds, so growing the
  test pool is a download rather than a code change (`--symbols` overrides it).
- No options and no order submission.
- Results are simulated research estimates, not trading advice.

## Layout

- `scripts/run_obsidian_pine_ytd.py` — Pine-parity stock runner.
- `scripts/experiment_obsidian_eod.py` — development-fold sweep and locked-holdout scorer.
- `scripts/deep_dive_1m_candidate.py` — post-hoc analysis of the crowned 1m candidate's holdout trades (`results/obsidian_eod_1m_candidate_deep_dive.md`).
- `core/` — isolated detector and data-store helpers.
- `tests/` — offline invariants.
- `data/historical_equities/obsidian_pine_ytd_2026/` — copied SIP 1-minute bar archive.
- `results/` — reports and resumable checkpoints.

The original Cipher files and original cache remain untouched. This workspace is
intentionally self-contained and uses the copied dataset. The downloader helper
is retained only for reproducibility; this experiment does not download data.
If a future run downloads data, credentials may be resolved from the workspace's
local environment and the repository-level environment fallback used by the
copied shared credential helper; no credentials are written into results.

## Continue the optimization loop

From the repository root:

```bash
PYTHONPATH='EOD strategy' python3 'EOD strategy/scripts/experiment_obsidian_eod.py' --family 1m
PYTHONPATH='EOD strategy' python3 'EOD strategy/scripts/experiment_obsidian_eod.py' --family 5m
PYTHONPATH='EOD strategy' python3 'EOD strategy/scripts/experiment_obsidian_eod.py' --family all
```

The sweep writes:

- `results/obsidian_eod_optimization_2026.json`
- `results/checkpoints/1m_final_30_primary.json`
- `results/checkpoints/5m_longer_eod_secondary.json`

If interrupted, rerunning the same command resumes completed candidates from the
family checkpoint. A candidate is selected from development folds only; the
June–August 2026 holdout is scored after selection and is not used to choose
parameters.

## Current experimental rules

The development folds are January–February, March–April, and May 2026. The
holdout begins June 1, 2026. Candidates require at least 20 trades per fold,
minimum breadth across symbols, and positive average return in at least two of
three folds. A holdout candidate is selected only from the strict all-fold-
positive subset; if that subset is empty, no family candidate is crowned.

The 5-minute family is secondary and must not replace the primary 1-minute
objective merely because it happens to have a better holdout number.

## Current result (2026-08-12)

The 1-minute family crowned a candidate; the 5-minute family did not (no configuration was
positive in all three development folds, and the strict rule correctly declines to crown one).

The crowned 1-minute candidate **does not clear the cash hurdle**, and its positive result is
carried by a single symbol:

| | value |
|---|---|
| pooled holdout sum (423 trades, 10 symbols) | **+6.999%** |
| equal-weight compounded per symbol, 71 days | **+0.735%** |
| annualized | **+3.84%** |
| vs 4% risk-free | **−0.16 pp** |
| symbols compounding positive | 6 / 10 |
| annualized excluding AMD alone | **−1.12%** |

Two things to keep straight when reading any report in `results/`:

1. **A pooled sum is not a return.** It adds per-trade percentages across all ten symbols, so
   it reads roughly ten times larger than trading them equally weighted would have produced —
   here 9.5x. The pooled figure is the right way to measure the signal and the wrong way to
   state performance. `holdout_economics` in the JSON, and the "What the holdout is actually
   worth" section of the deep-dive report, carry the equal-weight translation.
2. **The edge is concentrated.** AMD supplies 50% of all positive contribution. Dropping it
   turns the result negative, and dropping any of AAPL, AMZN, META, or NVDA leaves it below
   the hurdle. Passing an all-folds-positive rule and beating a fixed-time control on 7/10
   symbols are both real, and neither establishes an edge that survives its best name being
   removed.

The methodology itself checks out: selection reads development folds only, the holdout is
evaluated after the candidate is fixed, and incomplete sessions are excluded before indicators
are computed. The problem was never the protocol — it was that the reports stated pooled sums
without the translation, and stated no comparison against cash at all.

Fields renamed for accuracy: `gross_sum_return_pct` summed `net_return_pct` and is now
`net_sum_return_pct`, alongside `pre_cost_sum_return_pct` and `slippage_drag_pct`. Slippage
costs 24.4% of the pre-cost pooled total, which no previous output showed.

## Widening the search (2026-08-12)

The question changed from "does this candidate work" to "where does an edge live", so the
search space grew in three directions: more tickers, the full session as well as the last
thirty minutes, and the indicator lengths that had never been varied.

### Universe: 10 → 41 symbols

The original ten were eight mega-cap tech names plus SPY and QQQ — concentrated enough that a
single semiconductor name supplied half the crowned candidate's positive contribution. The
archive now holds 41: broad and sector ETFs (DIA, IWM, SMH, XLE, XLF, XLI, XLK, XLP, XLV),
financials (JPM, BAC, GS), energy (XOM, CVX), healthcare (UNH, LLY, JNJ), consumer (WMT, COST,
HD, MCD, KO), industrials (CAT, BA), and more tech (AVGO, CRM, DIS, INTC, MU, NFLX, ORCL).
4.48M one-minute SIP bars from 2025-12-02.

Diluting the edge across more names is the point, not a side effect: a signal that only works
on the names it was found on has not been shown to work.

### Families

Each time window is its own family. Selecting one winner from a grid that mixes 10/20/30/40
minute windows answers only "which window won this search"; it cannot say whether the effect is
specific to the last ten minutes or survives a wider window. So each window selects on the
development folds independently and gets its own locked-holdout score.

| family | bars | window | grid |
|---|---|---|---|
| `1m_last_10` | 1m | last 10 min | 12 candidates |
| `1m_last_20` | 1m | last 20 min | 12 candidates |
| `1m_final_30_primary` | 1m | last 30 min | 12 candidates |
| `1m_last_40` | 1m | last 40 min | 12 candidates |
| `5m_last_30` | 5m | last 30 min | 8 candidates |
| `5m_last_60` | 5m | last 60 min | 8 candidates |
| `5m_last_90` | 5m | last 90 min | 8 candidates |
| `5m_last_120` | 5m | last 120 min | 8 candidates |
| `1m_full_session` | 1m | whole session | 24 candidates |
| `5m_full_session` | 5m | whole session | 24 candidates |

Run one with `--family 1m_full` / `5m_full`, or all of them with `--family all`.

`arm_minutes` is not swept in the full-session grids. Under `mode="Full Session"` the detector
gates every regular-hours bar, so `arm_minutes` only shifts the `hot` boost window and cannot
change which bars are eligible; sweeping it would triple the candidate count while testing
almost nothing. That budget goes to the indicator lengths instead.

### Indicators

Now swept, having previously been fixed at the pasted strategy's values:

- MACD-style lengths as three coherent triples — `8/21/5` (the pasted source), `5/13/4`
  (faster), `12/26/9` (classic). Sweeping fast/slow/sig independently mostly produces
  degenerate pairs where the fast length meets or exceeds the slow one, which inverts the
  histogram's meaning.
- `trend_len` ∈ {100, 150, 200}, the trend EMA behind the A/B grade.

The detector already computed EMA, RMA, SMA, rolling stdev, rolling highest and true range, so
this widened what the sweep varies rather than adding new indicator maths.

## Three bugs this uncovered

**Full Session mode was inert.** `mode: "Full Session"` has always existed in the parameters
and the detector's internal `gate` honoured it, but `BarState` published only `in_window` — the
end-of-day arming window — and the runner filtered candidates on that. So the detector produced
all-day signals and the runner discarded every one outside the final thirty minutes, with no
error and an identical trade list. `signal_gate` is now the mode-aware flag. On real SPY bars
the fix takes the candidate count from **195 to 1,938**, of which 1,743 fall before 15:30.

**Swept signal parameters were dropped.** `detector_params` was assembled from three hardcoded
fields, so `mode`, the EMA lengths and `trend_len` never reached the detector. Every
full-session and indicator candidate would have run as EOD Focus with default indicators and
reported identical numbers under different labels.

**The state cache was keyed on the same three fields.** Even with the parameters forwarded, two
candidates differing only in an unkeyed field would share precomputed indicator states and the
second would be scored with the first's indicators.

None of the three raises an error; all three produce a plausible report. They are covered by
`tests/test_full_session_mode.py` and `tests/test_grid_parameter_plumbing.py`.

## What the widened search returned (2026-08-12)

All ten families ran to completion on 41 symbols. **Not one produced a selectable candidate.**
Nine had zero candidates pass the eligibility gate; `1m_last_20` had one, which then failed the
all-folds-positive rule. No holdout was scored by the sweep, because there was nothing to score.

### The fold decides the sign, not the parameters

Pooled net % over 41 symbols, summarised across every candidate in each grid:

| family | fold 1 (Jan–Feb) | fold 2 (Mar–Apr) | fold 3 (May) | median spread | sd across params |
|---|---|---|---|---|---|
| `1m_last_10` | −9.68 (0/12 pos) | −11.63 (0/12) | −0.81 (4/12) | 10.8 | 3.7 |
| `1m_last_20` | −0.37 (5/12) | +3.94 (10/12) | −2.05 (1/12) | 6.0 | 6.0 |
| `1m_final_30_primary` | −27.27 (0/12) | +3.67 (11/12) | −5.25 (0/12) | 30.9 | 4.7 |
| `1m_last_40` | −30.82 (0/12) | −12.64 (0/12) | −9.06 (0/12) | 21.8 | 6.5 |
| `5m_last_30` | −5.12 (0/8) | −11.57 (0/8) | +0.38 (6/8) | 12.0 | 4.7 |
| `5m_last_60` | +4.12 (6/8) | +1.96 (5/8) | −8.07 (0/8) | 12.2 | 7.8 |
| `5m_last_90` | −4.05 (2/8) | −7.97 (0/8) | −6.01 (1/8) | 3.9 | 8.2 |
| `5m_last_120` | −4.25 (3/8) | +2.57 (6/8) | −16.97 (0/8) | 19.5 | 9.8 |
| `1m_full_session` | −2.38 (11/24) | −3.45 (12/24) | −48.44 (2/24) | 46.1 | 78.0 |
| `5m_full_session` | +5.64 (14/24) | **+85.78 (24/24)** | **−59.03 (0/24)** | 144.8 | 74.9 |

Within a fold the sign is close to unanimous across parameter sets. `5m_full_session` is the
clearest case: every one of its 24 candidates made money in Mar–Apr and every one lost it in May.
That is a regime, not a parameter effect, and it is why no candidate can be positive in all three
folds. Reading a single family's total is misleading; the calendar is doing the work.

### Out of sample, at the pasted defaults

Nothing was selected, so there was no search winner to test. Instead one configuration fixed in
advance — the pasted Pine defaults, `sig_mult 1.1 / clps_thresh 0.6 / entry_delay 1 / MACD
12/26/9` — was scored on the untouched holdout (2026-06-01 → 2026-08-11). No development result
influenced it, so these numbers carry no selection bias.

| config | trades | win % | RR | PF | gross % | slippage % | net % | cost / gross |
|---|---|---|---|---|---|---|---|---|
| `1m_final_30_primary` | 1577 | 47.62 | 1.059 | 0.963 | +9.85 | 17.66 | **−7.81** | 1.79× |
| `1m_full_session` | 1949 | 48.54 | 0.976 | 0.920 | −51.34 | 21.53 | **−72.88** | — |
| `5m_full_session` | 860 | 46.28 | 1.030 | 0.888 | −38.85 | 9.12 | **−47.97** | — |
| `1m_last_10` | 880 | 46.93 | 1.018 | 0.900 | +2.32 | 10.33 | **−8.01** | 4.45× |

Two things follow. **The signal is genuinely end-of-day specific**: gross return is positive only
in the EOD windows and clearly negative across the full session, so trading all day is worse than
not trading. And **the edge is smaller than the spread in every window**: where gross is positive
it is roughly +1 bp per trade against 1.0–1.5 bp of measured round-trip slippage.

`5m_full_session` is the cautionary case. It looked like the only positive family in development
(+42.65% at these defaults) and inverted to −47.97% out of sample, with its gross going from
+66.08% to −38.85%.

### Ticker by ticker, the edge does not persist

Per-symbol development results do not predict per-symbol holdout results:

| config | corr(dev, holdout) return/trade | sign agreement |
|---|---|---|
| `1m_final_30_primary` | −0.166 | 23/41 |
| `1m_full_session` | +0.073 | 22/41 |
| `5m_full_session` | −0.251 | 17/41 |
| `1m_last_10` | +0.217 | 26/41 |
| pooled | **−0.148** | 88/164 (53.7%) |

Tickers that were positive in development averaged **−4.53 bp** per trade in the holdout; tickers
that were negative averaged **−1.21 bp**. Choosing tickers on development results was worse than
not choosing. The holdout leaders are largely the development losers — INTC −27.9 → +18.1,
LLY −20.7 → +14.4, AMZN −20.1 → +8.3 — while SMH (positive in all four configs in development,
+30.4) came back −9.5 and ORCL +18.5 → −20.3.

`AMD` is the one name positive in both periods (dev +27.7, holdout +36.4). It is not a
single-trade artifact — 62.5% win rate, median trade +0.82%, still +23.4% without its best trade
— but it is a **+3.66σ** outlier inside a family that lost 72.88% overall, and the same name
under `5m_full_session` returned −1.08%. One name at 3.7σ out of 41 tested is what a fat tail
looks like, not what a discovery looks like.

### Hypotheses tested and rejected

- **"The ranking is really a spread-cost ranking."** No: per-ticker net correlates +0.943 with
  gross edge and only −0.523 with slippage. Cost decides whether a name clears zero, but the
  ordering comes from the signal.
- **"Single names beat ETFs because the strategy needs range."** No. Range does relate to gross
  edge (corr +0.426 in the 30-minute family), but at group level single names net −90.70% against
  the ETFs' −45.97%. The five positive semis (AMD, MU, NVDA, AVGO, INTC) were specific names, not
  a category — SMH itself, holding all of them, lost 11.51% over 49 trades.
- **"The last ten minutes are special."** No, in either construction. Arming only in the final
  ten minutes gives +2.32% gross against 10.33% slippage (4.45× the edge). Bucketing the
  30-minute family's holdout trades by entry time makes the final bucket the *worst* of the three:
  15:30–15:40 −1.18 bp, 15:40–15:50 +0.98 bp, 15:50–16:00 −1.04 bp. Minute by minute inside the
  last-ten family the signs simply alternate.
- **The earlier `15:50–16:00` result does not replicate.** On the 10-symbol universe that bucket
  showed 65.88% win / PF 2.213. On 41 symbols it is 48.97% win and −4.04%. It was an artifact of
  the narrow universe.

### A data caveat worth carrying

Six symbols have materially incomplete session coverage and their per-ticker numbers should not
be read at face value: COST 58.1% (100 of 172 sessions), LLY 79.7%, CAT 86.0%, GS 89.0%,
BA 89.5%, DIA 93.6%. Every symbol discussed as a leader above is at 100%, so the conclusions do
not rest on the gapped names.

## A caution about the widened search

More candidates against a fixed holdout raises the chance that the winner is noise. The
protocol's defences — selection on development folds only, a strict all-folds-positive rule, a
locked temporal holdout — are unchanged and still apply, but they bound the risk rather than
remove it. With 204 candidates across four families, expect some family to produce an
attractive-looking holdout number by chance, and treat the leave-one-out concentration table as
the first thing to read rather than the last.
