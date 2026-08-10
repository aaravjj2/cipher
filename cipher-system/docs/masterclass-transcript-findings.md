# "Flash Agentic and Options Trading Masterclass (2026)" — checked against Cipher

Source: a consolidated transcript (5 recordings, 1 technical doc, 1 exposure-chart
image, filed as `ARCH-NVDA-MU-2026-07-31`) describing four things as tradeable
edges: a Flash Agentic "floor bounce" story on $MU, a Fibonacci-extension
system ("Structural Fib") with a stated take-profit probability table, a
"Golden Vex" bounce on SPY claimed at a 95–98% win rate, and a "Capital
Flywheel" cash-secured-put wheel on mega-caps and their leveraged ETFs (NVDL
named specifically), gated by market cap, a three-cloud EMA trend filter
("Ripster clouds"), and IV > 50%.

Checked 2026-08-10. One of the four was already tested before this transcript
arrived; one was tested fresh today on real data; two are currently blocked or
unbacktestable for reasons stated below, and are not being reported as
negative findings — absence of evidence, not evidence of absence.

## Structural Fib — already tested, already falsified

This is not new: `docs/backtest-findings.md` (under "Structural Fib") already
ran `scripts/backtest_structural_fib.py --symbols NVDA,AAPL --days 365` against
this exact system — the same 1.5% pre-market range filter, the same
trend-based Fibonacci extension mapping, the same 0/0.5/1/2 level scheme. The
transcript's own probability table claims 98% (level 1) and 64% (level 2). The
measured touch rate — the charitable reading, price reaching the level at any
point in the session, no stop — was **80.0%** and **49.6%**. The transcript's
own 1.5% pre-market filter, tested as a filter for which days to trade, did not
select better days: trending days scored *worse* (77.6% vs 80.0% on the first
leg). And the decisive number: a 74.6% *win rate* on the first leg came with a
**negative average return** — wins are small, losses are large. A headline win
rate that high is compatible with losing money, and here it does.

Nothing about this transcript changes that finding. It restates the same
system with a cleaner-sounding probability table than the reality Cipher
already measured against it.

## Capital Flywheel (CSP wheel + Ripster clouds) on NVDL — tested fresh today

This one has real, extensive, previously-unpublished infrastructure behind it:
`core/leveraged_etf_csp_wheel.py` already implements exactly the system taught
— a weekly 5/12, 34/50, 72/89 EMA cloud filter (`weekly_trend_state()`), a
down-day-plus-bullish-clouds entry trigger, an IV band, and a "standard" mode
whose own numbers (25–35 DTE, target 5% return on collateral) match the
transcript's "Monthly Churn" description almost exactly. `tests/
test_leveraged_etf_csp_wheel.py` exercises real assignment/roll mechanics, not
just utilities. NVDL is already in the module's default universe, tagged
`quality_approved=False` by design — the tool refuses to assume fundamentals
it hasn't verified, rather than silently pretending it checked.

**What was run today.** NVDA's >$200B market cap is a true, checkable fact
(not fabricated data), so this run overrode the quality gate honestly for that
one reason and used the transcript's own stated numbers: `--mode standard`,
`--minimum-iv 0.50`, `--required-bullish-clouds 2`, NVDL only, 2026-01-02
through 2026-07-24 (the fullest window covered by the local option archive).

```
events: 0   skips: 16   realized_pnl: $0   total_return: 0.0%
```

**Zero trades.** All 16 candidate days failed with
`no_put_contract_passed_iv_return_liquidity_filters` (one `missing_put_chain`).
Re-run with the IV floor lowered below the transcript's own 50% threshold, to
the module's more permissive built-in default (0.40) — still zero, which rules
out the IV filter itself as the bottleneck. The entry *trigger* is not the
problem either: 16 down-day-plus-bullish-cloud signals fired over seven
months, a normal cadence. The blocker is the mode's own `target_pop=0.75` (a
75% modeled probability-of-profit floor) combined with the 5% return target —
no real NVDL put, on any of those 16 real signal days, offered both at once.

**Isolating it further**: removing only the POP floor (`--relax-pop`, the
module's own diagnostic override — a materially looser rule than anything the
transcript describes) let exactly **one** trade through, out of sixteen
signals. That position was still open at the test's end date, so it has no
closed outcome — one open, un-scored position is not a result in either
direction.

**Honest standing**: the system as taught — mega-cap, two bullish clouds,
IV>50%, standard monthly-churn selection — produced zero trades on NVDL over
this window on Cipher's real option-chain data. The entry signal is real and
fires on schedule; the option-selection constraints as specified together are
close to unsatisfiable on this name in this period. This is not evidence the
underlying idea is wrong — a wider universe (SOXL/TQQQ/TSLL are already in the
module), a longer window, or a materially different POP/return combination
than what was taught could tell a different story, and are natural next runs
if this is picked up again. It is evidence that the specific numbers taught —
on this ticker, in this window — do not clear their own stated bar.

One more thing worth flagging plainly: this module has already been run
dozens of times before today (`data/leveraged_etf_wheel/*` holds ~30 prior
result sets — `standard_2026_iv150_relaxpop`, `advanced_assignment_2026_iv150_
iter2`, and others, several showing modest positive returns in the 1–4% range
over the same seven months) but **none of them were ever checked against a
matched random-entry control**, the discipline every strategy in
`core/strategy_catalog.py` is required to clear. A percentage return with no
baseline is not a verdict — it's the exact gap `beats_control_range` exists to
close, and it hasn't been applied to this module yet.

## Golden Vex bounce on SPY (claimed 95–98% win rate) — blocked, and the name doesn't match what Cipher computes

Two separate problems, not one.

**The terminology doesn't map.** In Cipher, "golden" (`core/scanner.py:314`)
is the strike with the single largest **|GEX|** — a gamma magnet — computed by
`argmax` over the gamma profile. It has nothing to do with vanna/VEX; that's a
different, separately-computed quantity (`core/exposure.py`'s `model_vanna`).
"Golden Vex" as a compound term doesn't correspond to anything Cipher
calculates. Whatever the transcript's source is describing, it is not the
same object as Cipher's "golden" level, and testing Cipher's golden level
against a 95–98%-win-rate SPY claim would be testing the wrong thing even if
the data existed.

**The data doesn't exist yet regardless.** Golden is GEX-derived, so it needs
the same point-in-time open interest as every cluster/GEX strategy in the
catalog — currently at **13 of 60 capture days (21.7%)**, same accrual clock as
everything else gated on this. What partial evidence does exist
(`core/cluster_backtest.py`'s `score_snapshot`, real output in
`data/backtests/cluster_report_20260720T040317Z.json`) measures something
different anyway: an **82% hit rate** for golden-tagged levels, across a broad
equity scan (not SPY-specific), meaning "price touched the level within a
horizon" — not "price bounced off it and reversed," and not the 95–98% claimed.
Even that partial, wrong-metric number falls well short of the claim.

**Standing**: not tested, and not testable rigorously yet — flagged as BLOCKED
for the same reason the rest of the GEX/cluster family is, plus a naming
mismatch that means it wasn't the right test to try to force anyway.

## Flash Agentic "floor bounce" (the $MU story) — anecdotal, no matched-control backtest exists

The transcript's MU trade (floor bounce at $81, target extended to $88, a 7%
move) is a real, correctly-described example of how the live Flash Agentic
scanner behaves — target extension on continued momentum is a documented,
intentional feature (`core/agentic_episodes.py`). But nothing in this codebase
has ever run Flash Agentic's floor-bounce setup through the same matched
random-entry control every catalog strategy is required to clear.
`core/flash_agentic_sim.py` is explicitly "alerting/research infrastructure
only" (its own docstring) — it records live scanner output, it never calls
`core/backtest_engine.py`. The only measured numbers that exist are in
`docs/cipher_signal_complete_observations_2026-08-06.md`: 15 live "Flash"
floor-bounce observations at 73.3%, 5 "Agentic" ones at 80.0% — both far below
`MIN_TRADES_FOR_VERDICT = 30`, the threshold below which this codebase reports
INSUFFICIENT rather than a verdict everywhere else. Treating either number as
a win rate would be exactly the mistake the whole catalog-verdict system
exists to prevent.

**Standing**: INSUFFICIENT by the same rule applied everywhere else in this
codebase — a real, live-observed pattern with a sample size far too small to
be a result, and no matched-control test exists to check it properly.

## Summary

| Strategy | Status | What would change it |
|---|---|---|
| Structural Fib (NVDA/AAPL) | **Falsified** | Nothing pending — tested, high win rate coincides with negative average return |
| Capital Flywheel / CSP wheel (NVDL) | **Zero trades at stated parameters** | A wider universe, longer window, or different POP/return combination than taught — not yet a negative result on the underlying idea, but the taught numbers don't clear on this name/window |
| Golden Vex bounce (SPY) | **Blocked + name mismatch** | Point-in-time OI reaching 60 days (currently 13); also needs the actual described quantity identified, since "golden" ≠ vanna in this codebase |
| Flash Agentic floor bounce ($MU) | **INSUFFICIENT** | A real backtest harness for Flash Agentic against a matched control, and ≥30 observed instances |
