# Forty strategies, one standard

`core/strategy_catalog.py` + `core/strategy_evaluation.py`, run 2026-08-08 on ten
mega-cap symbols: five years of daily bars for the daily families, one year of
15-minute bars for the intraday family, costed with the measured per-symbol
half-spread from `data/execution_costs/spread_profile.json`.

```
TALLY: PASS 1 | FAIL 13 | INSUFFICIENT 6 | NO_TRADES 5 | BLOCKED 15
```

## Why the old numbers could not be used

The five engines behind `/api/strategy`, `/api/price-backtest`,
`/api/edge-backtest`, `/api/intraday-backtest` and `/api/historical-backtest` were
not merely unvalidated. Each was structurally incapable of producing a measurement:

- **No transaction cost.** `grep -ciE 'cost_bps|slippage|commission|fee'` returns
  **0** for `price_backtest.py`, `edge_backtest.py` and `intraday_backtest.py`,
  against **12** for `backtest_engine.py`. Every Sharpe and expectancy they
  reported was gross, on findings that turn on a 1-2bp difference.
- **Chronological truncation.** `strategy_skew_harvest` and `strategy_gap_and_go`
  broke at a literal five trades. That keeps the five *earliest* signals in the
  history and discards the rest regardless of what they did. `gap_and_go` had no
  `max_trades` parameter at all, so the cap could not even be raised.
- **Lookahead by construction.** `strategy_backtest.py:836` fetches TODAY's GEX
  matrix, then trades it over 60 days of past bars (`:869`). All eight GEX
  strategies use open interest that did not exist on the trade dates.

None of the five was reachable from the browser — `app/server.mjs` never proxied
them. Adding those five proxy lines was a five-minute change that would have
shipped 24 lookahead-biased, zero-cost, truncated leaderboards to the UI.

## What one standard changes

A strategy passes only by beating a random-entry control matched trade-for-trade
by symbol and direction, clearing the **best** of 20 random draws rather than their
mean, and then staying positive on a holdout carved off before any fold was
examined. Costs are charged both sides.

The difference shows up in the rows the old engines would have ranked first:

```
strategy                  verdict         n     avg%      PF
edge.rsi2_reversion       PASS          749   0.4977   1.354
edge.breakout_20d         FAIL           82   0.3759   1.237
edge.momentum_vol_filter  FAIL          231   0.4273   1.330
price.gap_fill            INSUFFICIENT   10   1.8566   3.704
price.bollinger_squeeze   INSUFFICIENT    6   1.9282  11.696
```

`price.bollinger_squeeze` reports a profit factor of **11.7** on six trades and
`price.gap_fill` **3.7** on ten. Under the old engines those were the headline
results. Here they are reported INSUFFICIENT and given no verdict, because six
trades cannot separate a strategy from noise in either direction.
`edge.momentum_vol_filter` returns a positive average on 231 trades and still
loses to random entry — which is the single most useful thing the control does.

## Five verdicts, deliberately not four

Collapsing any of these into another is how an unmeasurable strategy acquires a
number:

- **BLOCKED** — the data cannot support an honest measurement. Carries the reason
  and, for the GEX family, the accrual countdown (12 of 60 capture days). Never
  scored.
- **NO_TRADES** — produced no entries on this universe.
- **INSUFFICIENT** — fewer than 30 trades. Not a failure; an absence of evidence.
- **ERROR** — the strategy raised. Distinct from finding nothing.
- **FAIL** — lost to its matched control.

A sixth, **WRONG_TIMEFRAME**, was added after the first run reported all five
intraday strategies as NO_TRADES. They had been handed daily bars. That is the
caller's mistake being charged to the strategy; on 15-minute bars three of them
fail honestly and two genuinely produce nothing.

## The ladder is now climbable

`core/research_platform/promotion.py:111` already routed `FAST_BACKTESTED` to an
engine named `cipher_fast` that did not exist, and `FastGateEvaluator` already
refused to advance a strategy missing a declared `required_quality_check`. The
registry held 2 strategies, 0 experiments, 0 promotion events — built and
unclimbed.

All 40 are now registered with
`required_quality_checks = ["beats_control_range", "control_matched"]`. Checked
against the real gate:

```
edge.rsi2_reversion     PASS      ctrl=True  wf=True   gate=CONDITIONAL_PASS  []
edge.overnight_harvest  FAIL      ctrl=False wf=True   gate=FAIL  [missing_quality_check:beats_control_range]
edge.breakout_20d       FAIL      ctrl=False wf=True   gate=FAIL  [missing_quality_check:beats_control_range]
gex.wall_bounce         BLOCKED   ctrl=None  wf=None   gate=FAIL  [missing_quality_check:beats_control_range]
```

Nothing was invented: `beats_control_range` was promoted from a field in a JSON
report to a condition a state machine already knew how to enforce.

The gate found a real gap while this was being wired. `rsi2_reversion` cleared its
control but failed on `walk_forward_failed`, because the declared threshold
required a walk-forward the evaluation was not producing. `walk_forward` was
generalised to take a `signal_fn` — it had hardcoded `run_backtest`, so it could
only ever see the Obsidian detector — and PASS now means both things.

## What this does not say

One strategy out of 40 clearing a control is roughly what chance allows, and it was
selected from a catalog of 40 on one universe. `edge.rsi2_reversion` is a candidate
for out-of-sample replication on a disjoint symbol set, exactly as was done for the
filter finding in `docs/backtest-findings.md` — not a result. The honest summary is
that the catalog now produces verdicts that can be wrong in a checkable way, which
the previous 24 leaderboards could not.

---

## The one pass did not replicate

`edge.rsi2_reversion` was the single PASS out of 40. It was selected from that
catalog on one universe, which is the setup for a selection artifact, so it was
put through the same out-of-sample test as the filter finding in
`docs/backtest-findings.md`: the identical configuration on ten symbols drawn from
other sectors that it was never measured on.

```
set        verdict     n     avg%      PF    beats_control  walk_forward
original   PASS      749   0.4977   1.354        True           True
disjoint   FAIL      754   0.1947   1.150        False          False
```

Same strategy, same timeframe, same five years, same measured cost, comparable
sample size — and it fails on both counts out of sample. The average return falls
by 61% and it no longer clears a random-entry control.

**The catalog therefore contains zero strategies with an out-of-sample result.**
That is the honest standing: 40 strategies, one standard, and nothing that
survives being looked for somewhere it was not found.

This is worth stating plainly because the intermediate state was misleading in a
specific way. After the first sweep the tool could have reported "1 of 40 passed",
which reads as a discovery. One in forty clearing a control is roughly what chance
allows, and the replication confirms that reading rather than the flattering one.

It also settles what the paper executor should be pointed at, which is nothing.
Forward-testing a strategy with no established edge produces a prospective record
of noise, and a prospective record is harder to discard than a backtest precisely
because it was collected forward.
