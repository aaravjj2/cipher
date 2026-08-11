# Backtest findings — Obsidian EOD detector

Result of running `core/backtest_engine.py` over the detector's signals. Recorded
here because the headline is negative, and negative results are the ones that get
quietly re-discovered six months later.

Reproduce with:

```bash
python scripts/run_obsidian_backtest.py --timeframe 15Min --years 1 --mode "EOD Focus"
python scripts/sweep_obsidian_exits.py  --timeframe 15Min --years 1 --mode "EOD Focus" --invert
```

Universe: 34 liquid optionable names across sectors. Costs 2 bps per side.
Entries fill on the next bar's open; when a bar spans both stop and target the
stop is assumed to fill first.

> **Cost provenance, read this before any number below.** Every verdict from here down to
> "The execution cost was a guess" uses `DEFAULT_COST_BPS = 2.0` — an assumption, not a
> measurement. That section replaces it with per-symbol half-spreads measured from the
> captured quote corpus (median **1.025 bps** per side, so the assumption was pessimistic)
> and re-runs the decisive comparison. The 2026-08-10 refresh below then shows the disjoint
> verdict no longer replicating, for reasons unrelated to cost. `core/execution_cost.py`
> reports provenance per lookup (`measured:median`, `assumed:no-profile`,
> `assumed:symbol-not-captured`, `assumed:insufficient-samples(N)`) and deliberately will
> not auto-load a profile, because a cost lookup that changes answer depending on whether a
> file exists on disk is not something a backtest can be built on.
>
> Sections dated 2026-08-11 concern the leveraged-ETF wheel and are governed by their own
> limits — a 69-date corpus and a 4% cash hurdle — not by this equity cost model.

## Headline

**The detector's setups, traded mechanically as directional entries, do not
produce a profitable rule — in either direction, under any of 36 exit
configurations, on daily or 15-minute bars.**

This is a statement about mechanical trading of the signal. It is not a claim
that the indicator is useless as a discretionary context tool, and it is not a
claim that the port is wrong — the port was validated separately against 332
captured rows from the live product.

## What the numbers say

Profit factor. 1.0 is breakeven; the random-entry control is the bar that
matters, not 1.0.

| Timeframe | Mode | Follow labels | Fade labels | Random control |
|---|---|---|---|---|
| 1Day, 10y | Full Session | 0.957 | — | 0.976 |
| 15Min, 1y | Full Session | 0.784 | 0.776 | 0.770 |
| 15Min, 1y | **EOD Focus** | **0.627** | **0.953** | 0.782 |

Two things fall out of this table.

**Full Session mode carries no directional information at all.** Follow, fade and
random all land within 0.014 of each other over ~18,000 trades. Whatever the
detector fires on outside the closing window is noise.

**EOD Focus mode carries real directional information, pointing the wrong way.**
Over 1,628 trades, following the setup labels won 34.7% while fading them won
44.2% — a 9.5 point gap against a random control sitting at 38.9% in between. The
fade held at 44.2% on a 25% holdout that had no influence on finding it, and the
follow direction lost in all three training folds plus the holdout
(0.628 / 0.594 / 0.612 / 0.643). This is too stable and too large to be noise.

The plain reading: end-of-day momentum events in these names mean-revert. A
"floor bounce" 30 minutes before the close is, on average, followed by weakness,
not strength.

## But fading it is still not a strategy

Fading is better than random and better than following, and still loses money.
`scripts/sweep_obsidian_exits.py` searched 36 exit rules (stop 0.5–1.5 ATR,
target 1.0–3.0 ATR, hold 6–24 bars) on the training folds only:

* Fading the labels: **0 of 36** configs profitable in every training fold. Best
  worst-fold profit factor 0.959.
* Following the labels: **0 of 36**. Best worst-fold profit factor 0.625.

Because nothing cleared the training bar, **the holdout was never scored**.
Scoring it after the search failed would have turned the data reserved to keep
the search honest into a second search.

The gap between "carries directional information" and "is profitable" is
transaction costs plus the stop-before-target assumption. Both are deliberately
conservative and both are correct to keep.

## Bugs this exercise found

* **`MOMENTUM PUSH` is emitted for releases in both directions.** The backtest
  originally mapped setups to long/short by name, so roughly half of 580 daily
  trades were traded backwards. `BarState` now carries `setup_direction` and the
  engine uses it; the name table survives only as a fallback for older states.
  Fixing this moved daily `MOMENTUM PUSH` from −0.033% to −0.241% per trade — the
  mislabelled half had been cancelling out the loss.
* **A 288-bar history limit that did not exist.** An early smoke test reported
  profit factor 1.312 on 288 daily bars and was pure small-sample noise. 288 was
  the window the ad-hoc driver requested, not an Alpaca limit — the feed serves
  ~2,500 daily bars back to 2016. On the full history the same configuration
  gives 0.957.
* **`walk_forward` returned an empty report when history was too short**, which
  reads identically to "the strategy found no trades". It now warns and reduces
  the fold count.

## What is still not testable

GEX and cluster strategies. GEX is gamma × open interest, so it needs
point-in-time OI, and replaying today's OI over past prices is lookahead bias
that will manufacture edge. `data/gex_history.sqlite` holds only a handful of
universe-wide capture days. `scripts/gex_daily_capture.sh` now runs from cron at
15:00 ET on weekdays to accrue snapshots; a few months of those make cluster
strategies testable for the first time. (15:00 rather than nearer the close
because a full 546-ticker pass takes ~27 minutes, and starting at 15:50 would put
the tail of the snapshot after the bell, mixing pre- and post-close spot into one
capture.)

## Filter-mode: the same detector, asked a weaker question

Everything above tests the detector as a **standalone entry trigger** — the hardest
question available, since the signal must beat random on timing, direction and
selection at once. Failing it cannot distinguish "carries no information" from
"carries information that is not an entry trigger", and those call for opposite
decisions.

`core/backtest_engine.py:run_filter` asks the weaker question: given trades you
were taking anyway, does the detector's state just before entry separate the good
ones from the bad? The base is deliberately dumb — a fixed-cadence long entry every
12 bars, no view on price — so any separation is attributable to the filter, not to
the base. Each partition is scored against **its own** matched random control,
because splitting a trade set enough ways always surfaces a flattering slice.

Reproduce: `python3 scripts/run_filter_backtest.py`

10 symbols, 15-minute bars, 1 year, EOD Focus, 6-bar lookback:

```
BASE (no filter)  n=12022 win=37.5% avg=-0.0534% PF=0.758

partition       n   share    win%      avg%  lift(pp)  verdict
bearish       159    1.3%    54.1    0.0471    0.1005  BEATS its own random control
bullish       138    1.1%    44.2   -0.0160    0.0374  within noise
none        11725   97.5%    37.2   -0.0552   -0.0018  within noise
```

**This is the first positive result in this document, and it is not an edge yet.**

What holds up. The effect survives lookback windows of 3, 6 and 12 bars and 8 of 9
control seeds, with lift steady at +0.09 to +0.10pp. The sign replicates in all
four disjoint time slices tried:

| slice | bear n | bear avg | lift | beats control |
|---|---|---|---|---|
| first half | 104 | +0.0587% | +0.11pp | yes |
| second half | 54 | +0.0251% | +0.08pp | no |
| train 75% | 130 | +0.0443% | +0.10pp | yes |
| holdout 25% | **27** | +0.0902% | +0.15pp | no |

It is also **coherent with the standalone finding**: the base is long-only, so
"a bearish signal preceding a long entry that then does better" is fading the
detector — the same direction as the 34.7%-follow versus 44.2%-fade result above.
An incoherent result would be more suspicious than a null one.

What does not. The control clears only where the sample is large (104, 130) and
fails where it is small (54, 27). That is low power, not a contradiction — but a
27-trade holdout supports no verdict at all. The partition is 1.3% of trades and
the effect is +0.09% per trade before any slippage beyond the modelled 2bps. Three
partitions were examined, so one clearing a control is roughly what chance allows.

Honest status: **promising, unestablished.** The useful part is not the number, it
is that the question is now separable — a standalone failure no longer collapses
"no information" and "wrong use of information" into the same verdict. The
sample needed to settle it is more sessions, not more configurations.

## If this is picked up again

Ordered by how much they could change the conclusion:

1. **Test the signal as a filter, not an entry.** Its information may live in
   "don't be long into the close" rather than "short into the close". Filters are
   evaluated by their effect on a base strategy's returns, which this engine does
   not yet support.
2. **Test it against the levels it was designed to be read with.** The live
   product shows these setups next to GEX walls. A collapse *at a gamma wall* is
   a different event from a collapse in open space, and that pairing is untestable
   until the OI history accrues.
3. **Options expression.** The detector's edge, if any, may be in volatility
   rather than direction — a collapse event is a volatility statement. Equity
   entries cannot capture that.

---

# Replication sweep: does the bearish partition survive being looked for elsewhere?

`scripts/sweep_filter_replication.py`, run 2026-08-08. The finding above was one
positive cell on one configuration, which is the shape a false positive takes. The
sweep moves three axes that are independent of it — a symbol set disjoint from the
original ten and drawn from other sectors, four bar timeframes, and both detector
modes — for 32 judgeable partitions.

The original configuration is included as a check on the harness, and reproduces
exactly: 10 symbols, 15Min, EOD Focus, bearish n=159, avg +0.0471%, lift +0.1005pp,
clears its control. The same numbers appear when run from the UI panel. The sweep is
therefore measuring what was reported.

## What replicated

The same configuration on ten **different** symbols also clears its control:
bearish n=153, avg +0.0164%, lift +0.0626pp. That is a genuine out-of-sample
survival on names the original never touched, and it is the strongest evidence this
repository has produced. The effect size roughly halves in the crossing.

Direction is asymmetric and the asymmetry is clean: **4 of 16 bearish partitions
clear their control, and 0 of 16 bullish ones do.** That pattern is harder to
explain by chance than any single cell, and it agrees with the fade result recorded
above.

## What did not

Nothing survives a change of timeframe. 5Min, 30Min and 1Hour all fail on both
symbol sets, in both detector modes. An edge that exists only at 15Min bars and
nowhere on either side of them is more consistent with a lucky bar alignment than
with a mechanism.

Overall 4 of 32 partitions clear, a 12.5% hit rate against the ~5% a control clears
by chance. `P(>=4 | 32, 0.05) = 0.074`, and the tests are **not** independent —
timeframes and detector modes re-slice the same underlying sessions, which inflates
the apparent hit count. So the sweep does not establish significance; it fails to
refute the finding, which is a weaker statement.

Note also that 2 of the 4 clearing partitions have a *negative* average return.
"Beats its control" there means losing less than random entry, not making money.

## The cost test, which is the decisive one

Cost applies equally to the partition and to its matched control, so it cancels in
the comparison: `beats_control_range` stays true at 2, 3, 4 and 5 bps per side, and
`lift_vs_base_pp` does not move at all. Absolute profitability is a different
question, and the modelled 2bps per side is optimistic:

```
set        cost/side     n      avg%   beats
original       2.0     159    0.0471   True
original       3.0     159    0.0271   True
original       4.0     159    0.0071   True
original       5.0     159   -0.0129   True
disjoint       2.0     153    0.0164   True
disjoint       3.0     153   -0.0036   True
disjoint       4.0     153   -0.0236   True
disjoint       5.0     153   -0.0436   True
```

Out of sample the edge is gone at 3bps per side. A one-basis-point change in the
cost assumption erases it. In sample it survives to just under 5bps.

## Verdict

Two claims, and they must not be merged:

- **There is a relative effect, and it replicated out of sample.** The bearish
  partition does better than random entry matched trade-for-trade, on symbols the
  finding was not derived from. This is not nothing.
- **There is no demonstrated tradeable edge.** It exists at one timeframe only, and
  out of sample it is smaller than the difference between a 2bp and a 3bp
  execution assumption.

Status stays **promising, unestablished** — but for a sharper reason than before.
The blocker is no longer sample size alone; it is that the effect is the same order
of magnitude as the execution cost, so no amount of additional equity-entry data
will settle it. Testing it where the size could be larger — at gamma walls once the
OI history accrues, or expressed in options where a collapse is a volatility
statement rather than a directional one — is the only path that changes the answer.

---

# Structural Fib: the published rates do not hold on the symbols it is claimed for

`scripts/backtest_structural_fib.py --symbols NVDA,AAPL --days 365`, run 2026-08-08.
Restricted to NVDA and AAPL because those are the two names the strategy is said to
work on; testing it on ten symbols would have invited the objection that the failure
was a universe problem.

```
                 n   touch%   claimed    win%      avg%
0.5 -> 1       704    80.0%       98%   74.6%   -0.050%
1   -> 2       470    49.6%       64%   44.0%   -0.050%
```

`touch%` is the charitable reading — did price ever reach the level before the close,
no stop, a whole session to wait. Even on that reading, which inflates hit rates
toward certainty, 98% measures 80% and 64% measures 49.6%.

The 1.5% pre-market rule, which is the strategy's own filter for which days to trade,
does not select better days: trending days score **77.6%** on the 0.5->1 leg against
80.0% for all signals. The filter the method is built around is not doing the work
attributed to it.

The more important number is the last column. A 74.6% win rate with a **negative**
average return means wins are small and losses are large — the profile of an
uncapped loss against a capped gain. A headline win rate that high is compatible
with losing money, and here it does.

---

# The execution cost was a guess. Now it is measured, and the guess was pessimistic.

`core/execution_cost.py` + `scripts/build_execution_cost_profile.py`, run 2026-08-08.

Every verdict above turned on `DEFAULT_COST_BPS = 2.0`, a hardcoded assumption. The
section above concluded that the out-of-sample effect "is smaller than the difference
between a 2bp and a 3bp execution assumption" — which made the assumption, not the
data, the deciding input. `data/tradier_stream.sqlite` holds millions of captured
quote events with `bid` and `ask`, so the assumption could simply be checked.

Half-spread in basis points of price, regular hours, 2026-07-22..07-31 (6 capture
days locally; the VM holds more):

```
symbol     samples     p25  median     p75     p95
SPY         424953   0.125   0.125   0.225   0.275
QQQ         458431   0.225   0.275   0.425   0.575
IWM         360641   0.175   0.325   0.325   0.525
AAPL        243883   0.325   0.475   0.725   1.075
NVDA        308808   0.525   0.525   0.775   1.025
AMZN        189221   0.575   0.825   1.075   1.675
GOOGL       197761   0.625   0.875   1.125   1.525
MSFT        147169   0.775   1.025   1.425   2.275
TSLA        150840   1.125   1.475   1.925   2.625
META         95847   0.925   1.525   2.275   3.825
AVGO         89941   1.675   2.475   3.525   5.175
MU          190724   1.725   2.625   3.725   5.525
AMD         136954   2.725   3.725   4.925   6.975
```

Median across symbols is **1.025 bps per side against an assumed 2.0**. The
assumption was conservative for the liquid names, not optimistic. It is
*optimistic* for AMD (3.7), MU (2.6) and AVGO (2.5), so a single global constant was
wrong in both directions at once — which is the argument for a per-symbol lookup
rather than a better constant.

## What this does to the finding

Re-running the two surviving configurations with the measured per-symbol spread:

```
set         cost         n     avg%  lift_pp  beats   base_avg
original    assumed 2bp  159   0.0471  0.1005  True    -0.0534
original    measured     159   0.0667  0.1017  True    -0.0350
disjoint    assumed 2bp  153   0.0164  0.0626  True    -0.0462
disjoint    measured     153   0.0156  0.0630  True    -0.0474
```

On the original ten — all ten measured — the effect is **42% larger** at real
spreads than at the assumed one, and the base strategy loses substantially less.
The lift barely moves (0.1005 → 0.1017), which is the expected behaviour: cost
applies equally to the partition, the base and the control, so it cancels in the
relative comparison and only ever moved the absolute number.

**This does not rescue the strategy.** It removes one objection. The earlier
statement that the edge dies at 3bps per side stands as arithmetic; what the
measurement shows is that 3bps is not the spread these particular names trade at.

## The limit, which is now the binding one

The disjoint set is **1 of 10 measured**. Only AVGO appears in the capture universe,
at 2.475 bps — *above* the assumption. The other nine fall back to the 2.0 constant,
so the out-of-sample row above is still mostly an assumption wearing a measured
label, and it is labelled as such by `equity_half_spread_bps`, which returns its
provenance rather than blending measured and assumed values silently.

The out-of-sample result therefore cannot be settled by this corpus. Settling it
needs the Tradier capture universe widened to cover the disjoint names — which is a
configuration change on a service that is already running, not new research.

Two further limits, stated in the artifact itself: this is one vendor's consolidated
quote feed over a handful of days, and it excludes commissions and market impact. It
bounds the modelled assumption against currently observable spreads. It is not a
cost model for a backtest spanning years, and nothing here should be read as one.

## 2026-08-10 refresh: the disjoint verdict no longer replicates

The profile was rebuilt from the full current corpus before any pruning. It now
contains 23 usable equity symbols, including all ten original and all ten disjoint
names. The median across symbols is 1.375 bps per side versus the 2.0 bps fallback,
but the single constant is still wrong in both directions (AMD 2.975, MU 2.775,
SPY 0.125, QQQ 0.225). The capture spans 12 observed dates; July 22 and 23 are
sparse ramp-up captures, while July 24 and 28 are missing collector weekdays, not
market closures.

The exact documented 15-minute / EOD-Focus configurations were rerun on the
refreshed fixed window, 2025-08-11 through 2026-08-10. Assumed and measured costs
used identical bars and signals:

```
set         cost         n     avg%  lift_pp  beats   control_best  base_avg
original    assumed 2bp  157   0.0460  0.0997  True       -0.0035    -0.0537
original    measured     157   0.0669  0.1003  True        0.0173    -0.0334
disjoint    assumed 2bp  153   0.0106  0.0567  False       0.0131    -0.0461
disjoint    measured     153   0.0140  0.0551  False       0.0165    -0.0411
```

The original set still clears the best matched random draw under both cost modes.
The disjoint set clears neither. That is a changed verdict from the August 8 table,
but it is **not caused by measured spreads**: the fresh assumed-2bp run also fails.
The rolling data window changed, and the claimed disjoint replication did not
survive that update. Measured cost improves absolute returns but does not change a
verdict in this controlled rerun. Until the disjoint result replicates on a locked
window, this remains an original-universe finding rather than out-of-sample evidence.

## Leveraged-ETF wheel: the entry filters cannot be validated on the current archive

2026-08-11. `core/wheel_entry_control.py` adds the matched random-entry control the wheel
runs never had. It holds universe, sizing, contract selection, IV band, POP enforcement,
exit/roll/assignment logic, and expected entry count fixed, and randomizes only *which*
eligible day an entry lands on. Both arms share one eligibility definition
(`_evaluate_gates`), so the matched rate has an honest denominator.

Running it over 2024-02-01..2026-06-01 on the approved universe:

```
signal fired on 274 of 2336 eligible days
per-symbol rate   NVDL 15.2%  SOXL 11.5%  TSLL 12.3%  TQQQ 7.9%
real arm          8 events, 4 closed trades, +1.797% total return, 348 skips
control arms      median 0 events (max 2 across 8 replicates)
verdict           comparison_valid = false
```

**The comparison is refused, and the refusal is the finding.** A naive reading gives
`beat_control_pct = 100`, which is wrong: the control did not lose, it never traded. The
option archives under `data/historical_options` were populated by downloading chains for
the days the signal selected (`decision_selections`, `download_plan_*`), so chain
availability is *correlated with the signal*. Random entry lands on days with no chain and
takes no position. The percentile would be measuring data coverage, not entry timing.
`MIN_CONTROL_ACTIVITY_RATIO = 0.5` now blocks that reading.

A valid control needs option chains downloaded for the control's random dates too. Until
that exists, no run in `data/leveraged_etf_wheel/` supports a claim about the down-day or
weekly-cloud filters.

Two things surfaced alongside it, independent of the control:

- Of 32 runs carrying a `report.json`, **15 produced zero events**. The 17 that traded
  span −4.839% to +3.526% total return over ~2.3 years on 1–38 closed trades.
- The highest trade counts come from the progressively relaxed variants (`iv150`,
  `relaxpop`), i.e. the gates were loosened until positions appeared. The strict variants
  are the ones that produced nothing.

Separately, the wheel could not run at all against current data until this work: it
queries `bars where timeframe='1Day'`, and `data/historical_bars.sqlite` has neither that
table name nor a `timeframe` column, with zero symbol overlap with the wheel universe.
Daily bars for NVDL/TSLL/SOXL/TQQQ were downloaded to
`data/historical_equities/wheel_universe/equity_bars.sqlite` (916–1304 bars each,
2021-06-01..2026-08-10). The 32 existing runs are therefore not reproducible from any
equity store now present, which is its own reason to treat them as historical artifacts.

## Leveraged-ETF wheel: 64 of 66 variants are profitable, and 12 of 66 beat cash

2026-08-11. `data/leveraged_etf_wheel/parameter_lab_2026/` sweeps 66 parameter variants
over 2026-01-02..2026-07-24 (203 days). Its own `report.md` is careful to say it is "a
post-thesis sensitivity analysis, not an independent holdout." The numbers:

```
                total      annualized
  worst        -0.913%        -1.64%
  median       +0.666%        +1.20%
  best         +3.856%        +7.04%

  variants > 0%          : 64 / 66
  variants > 4% annual   : 12 / 66
```

**The 64/66 hit rate is an artifact of the wrong comparison.** Selling cash-secured puts on
leveraged ETFs during a rising 7-month window earns a small positive almost regardless of
parameters, so "is it above zero" separates nothing. Against a 4% cash yield the picture
inverts: 54 of 66 variants lose to a T-bill while carrying assignment risk on 2x-3x
leveraged ETFs, and the median variant returns +1.20% annualized.

`WheelConfig.risk_free_rate` is already `0.04`, and `core/leveraged_etf_csp_wheel.py` uses
it at lines 857 and 906 to Black-Scholes-price the very options being sold. It is never used
as a hurdle. The engine prices its instruments off a 4% rate and then reports returns
against zero.

Two consequences for how the existing runs should be read:

- The headline "best return" in any sweep is selected from 66 draws with no holdout, so
  +7.04% annualized is an upper order statistic, not an expectation. The median is the
  honest central estimate and it is +1.20%.
- This is independent of the missing entry control. Even if the down-day and weekly-cloud
  filters were validated, the strategy as parameterized does not clear cash for most
  settings.

Recommended change, not yet made: report excess return over `risk_free_rate` alongside
`total_return_pct` in `BacktestResult.summary`, so a wheel result cannot be read as a win
without passing the hurdle the engine already assumes. That is a change to a published
metric, so it needs a decision rather than a quiet edit.

Correction to an earlier note in this session: `parameter_lab_2026/report.json` was flagged
as malformed because a survey script looked for a `summary` key. It is not malformed -- it
is a sweep artifact keyed by `variants`/`pop_floor_summary`/`variant_count`. No quarantine
is needed.

## Leveraged-ETF wheel: the entire corpus is 69 decision dates, not 2.3 years

2026-08-11, following the control work above. Joining `decision_selections` to `contracts`
across every dataset in `data/historical_options/`, the option-chain coverage for the wheel
universe is:

```
  NVDL:  16 dates   2026-01-20 .. 2026-07-13
  TSLL:  45 dates   2025-01-02 .. 2026-06-26
  SOXL:  34 dates   2026-01-08 .. 2026-07-24
  TQQQ:  16 dates   2026-01-20 .. 2026-07-23
  union: 69 distinct dates
```

Against that, the signal probe over 2024-02-01..2026-06-01 found **584 eligible days per
symbol** and **274 signal fires**. The real arm produced 8 events and 4 closed trades from
those 274 fires — not because the strategy declined, but because chains exist for a small
fraction of the days it selected.

**This means the real arm is starved too, not only the control.** The earlier note framed the
refused comparison as the control lacking data; that was half the picture. Every wheel run
in `data/leveraged_etf_wheel/` is sampling on the order of 69 decision dates spread across
four symbols, so describing any of them as covering 2024-2026, or a "2.3-year window", is
misleading. The window is the *span* of the sample, not its density.

Combined with the cash-hurdle result above, the defensible summary of the wheel work is:
a strategy sampled at ~69 decision points, whose median parameterization returns +1.20%
annualized against a 4% risk-free rate the engine itself assumes, with entry filters that
have never been compared to random entry. None of those three problems is fixed by running
more variants on the same archive.

What a valid control needs, quantified: chains on a random sample of the ~515 eligible days
per symbol that the signal did *not* pick. That is a download of the same order as the
existing corpus, via `core/historical_options_download.py`, and it is the only way to make
`MIN_CONTROL_ACTIVITY_RATIO` reachable. Until then `wheel_entry_control_study.py` will
correctly keep refusing to publish a percentile.

## Wheel entry control: recommend not buying the data yet

2026-08-11. The plan for this session called for downloading option chains on a random
sample of non-signal dates so `wheel_entry_control_study.py` could produce a valid
percentile. That download is the only way to make `MIN_CONTROL_ACTIVITY_RATIO` reachable and
it remains the correct step *if the strategy is worth validating*. On the evidence gathered
today it is not yet, and the download is deliberately left undone.

The reasoning, in order of how much it costs to establish:

1. The median swept parameterization returns **+1.20% annualized against the 4% risk-free
   rate the engine itself uses to price the options it sells**. Only 12 of 66 variants clear
   that hurdle. Establishing whether entry *timing* contributes to a return that is below
   cash answers a second-order question while the first-order one is unresolved.
2. The corpus is **69 decision dates** across four symbols. A control built on a comparable
   sample would be a test with the same sparsity problem, so the download needed is not a
   top-up — it is roughly the size of the existing corpus again, per arm.
3. The runs with the most trades are the progressively relaxed variants. Loosening gates
   until positions appear, then validating the entry filter, tests the wrong thing.

The cheaper experiment that should come first: take the strategy as parameterized, compute
excess return over `risk_free_rate` (now reported in `BacktestResult.summary` as
`excess_annualized_vs_risk_free_pct`), and establish whether *any* parameterization clears
the hurdle out of sample on a locked window. If none does, the entry-timing question is moot
and the data purchase is avoided entirely. If one does, the control download becomes worth
its cost and the random dates should be drawn only over that surviving configuration.

This is a recommendation to sequence the work, not a verdict on the strategy. The control
harness stays in place and will keep refusing to publish a percentile until the data exists.

## Wheel hurdle question: unanswerable, because nothing can cost the universe

2026-08-11, later the same day. The section above proposed the cheap experiment — does *any*
parameterization clear the 4% hurdle? — as the thing to do before buying control data. It has
now been run against every stored run, and the answer is neither yes nor no. It is that the
question cannot currently be answered, which resolves the sequencing decision anyway.

**The stored runs cannot answer it directly.** `annualized_return_pct` and
`beats_risk_free` are `None` in all 32 `report.json` files: those fields postdate every run
in the directory. So the figures below were recomputed from `total_return_pct`, `start`, and
`end` using the identical formula the engine applies (`(1 + r)^(1/years) - 1`), which makes
them comparable to a re-run but does not make them a re-run.

Of 32 reports, 17 placed a trade at all, 16 of those returned above zero, and 7 clear the 4%
hurdle. That last number is smaller than it looks:

* The 7 collapse to **3 distinct configurations** by config fingerprint. The rest are
  re-runs of the same parameters under different names.
* Five of the 7 clear by **0.10 to 0.35 percentage points** annualized.
* One fingerprint produced **two different results from the same configuration**:
  +3.53% over 66 events and +2.90% over 81 events. The same parameters returning different
  numbers is the unreproducibility problem recorded above, showing up inside the set of runs
  that would otherwise be the best evidence for the strategy.

**And the margins cannot be tested against measured costs.** Every one of the 7 was written
2026-07-28, thirteen days before `data/execution_costs/spread_profile.json` existed
(2026-08-10 22:40), so all of them used the assumed cost. The obvious next move is to re-cost
them — and that is not possible:

    wheel universe:                     NVDL, TSLL, SOXL, TQQQ
    measured option spreads cover:      23 symbols, all mega-caps and index ETFs
    overlap:                            0 of 4

The capture never subscribed to the wheel's universe. `TRADIER_OPTION_UNDERLYINGS` is unset,
so it falls back to `DEFAULT_UNDERLYINGS` in `core/tradier_stream_capture.py:44` — fourteen
mega-caps. `equity_half_spread_bps` therefore returns `assumed:symbol-not-captured` for all
four, exactly as it is designed to.

For scale on what is being assumed away: for symbols that *were* captured, the measured
option half-spread at 1-7 DTE runs a **median of 0.875% to 4.125% of premium**, and at 0DTE
2.6% to 7.1%. Leveraged single-stock and 3x sector ETFs are not plausibly tighter than
mega-caps. A margin of 0.10-0.35 percentage points of annualized return does not survive
that range being unknown, in either direction.

**So the conclusion holds and hardens.** Do not buy the control chains. The blocker is no
longer only that the control arm has no data on non-signal dates; it is that the strategy's
own return cannot be costed at all, so neither arm can be priced. Buying data to compare two
uncostable arms produces a percentile that means nothing.

The enabling step is cheap and forward-looking rather than a purchase: add NVDL, TSLL, SOXL,
and TQQQ to `TRADIER_OPTION_UNDERLYINGS` so a measured spread accrues for the universe the
strategy actually trades. That does nothing for the 2026 backtest window — the profile's own
caveat is explicit that it cannot cost historical periods outside its capture — but it is the
only path to ever answering the hurdle question with a measured number instead of an assumed
one. Until then the honest statement is: **three configurations clear a 4% hurdle under an
assumed cost that has never been checked against this universe, two of them by less than
half a percentage point.**
