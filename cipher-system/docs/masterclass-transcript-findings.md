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

## Capital Flywheel (CSP wheel + Ripster clouds) on NVDL — tested fresh today, first attempt was misconfigured

This one has real, extensive, previously-unpublished infrastructure behind it:
`core/leveraged_etf_csp_wheel.py` already implements exactly the system taught
— a weekly 5/12, 34/50, 72/89 EMA cloud filter (`weekly_trend_state()`), an IV
band, and a "standard" mode whose own numbers (25–35 DTE, target 5% return on
collateral) match the transcript's "Monthly Churn" description almost
exactly. `tests/test_leveraged_etf_csp_wheel.py` exercises real
assignment/roll mechanics, not just utilities. NVDL is already in the
module's default universe, tagged `quality_approved=False` by design — the
tool refuses to assume fundamentals it hasn't verified, rather than silently
pretending it checked.

**The first run of this test was wrong, and it's worth stating plainly why.**
It used `--mode standard` unmodified, which silently applies two constraints
the transcript never describes: `core/leveraged_etf_csp_wheel.py`'s
`_entry_signal()` (line 1667) hard-gates every day on `down_day_threshold`
*before* it ever checks the cloud state — the module's internal name for the
resulting strategy is literally `"cash_secured_put_down_day"` — and
`--mode standard` also enforces a `target_pop=0.75` floor. The transcript's
own stated rule is "if the clouds are bullish, we sell puts" — no down-day
requirement, no probability-of-profit floor. Testing with both left in
produced zero trades, which was a real result, but not a result about the
strategy the masterclass actually teaches — it was a result about a stricter,
uninvited hybrid of that strategy plus two of Cipher's own unrelated
defaults. Worse, `_open_new_puts()` (line 1701) doesn't even log a skip when
a day fails the down-day gate, so the original run's "16 signals, a normal
cadence" read was built on an undercount — most bullish-cloud days on NVDL
never reached the point where anything gets recorded at all.

**Corrected run.** `--relax-pop` (the module's own diagnostic override, since
the transcript states no POP requirement) plus `--down-day` set to the
smallest magnitude the code accepts (`-0.0001` — the constructor rejects
non-negative values outright, so a true "ignore price direction entirely"
setting doesn't exist as a CLI flag; this is the closest available
approximation, and it is *not* fully faithful to "no down-day requirement at
all" — flagged here rather than glossed over). Same window, same IV>50%, same
2-bullish-cloud requirement, same NVDL, same honest market-cap-based quality
override.

```
events: 1   skips: 65   open_options: 1   realized_pnl: $0   total_return: 0.20%
```

**One real trade, not zero.** 2026-07-13: sold to open 4 contracts of the
NVDL $27 put, 34-day expiry, at **IV 77.0%, POP 67.9%** (below the standard
mode's own 75% floor, which is why relaxing it mattered), **4.12% return on
collateral**, 2 bullish clouds, weekly RSI 54.1 — a signal that matches the
transcript's stated filters closely. It is still open 11 days into a ~34-day
contract as of the test's end date, so **it has no closed outcome** — one
open position is not a win or a loss.

**What's actually blocking a real answer now is data coverage, not the
strategy.** Of the 65 recorded candidates, 51 failed with
`missing_put_chain` — the local historical-options archive
(`data/historical_options/leveraged_etf_wheel/`) appears to have been
downloaded around a monthly decision cadence (matching how someone would
normally run this backtest, where signals are rare under the down-day gate),
not a daily one, so most days simply have no option chain downloaded to
evaluate against once that gate is loosened. Only 14 candidates had a real
chain to check, and 1 of those 14 produced a contract meeting every filter.

**Honest standing: INSUFFICIENT, not falsified.** The original "zero trades"
finding is withdrawn — it measured a stricter strategy than the one taught.
The corrected version finds real activity consistent with the transcript's
stated filters, but one trade with no closed outcome is nowhere near enough
to say the idea works or doesn't. Settling this needs the option archive
widened to daily coverage for NVDL (and ideally SOXL/TQQQ/TSLL, already in
the module's universe) over a longer window — a data-collection task, not a
research question the current local archive can answer either way.

One more thing worth flagging plainly, unaffected by the correction above:
this module has already been run dozens of times before today
(`data/leveraged_etf_wheel/*` holds ~30 prior result sets —
`standard_2026_iv150_relaxpop`, `advanced_assignment_2026_iv150_iter2`, and
others, several showing modest positive returns in the 1–4% range over the
same seven months) but **none of them were ever checked against a matched
random-entry control**, the discipline every strategy in
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
| Capital Flywheel / CSP wheel (NVDL) | **INSUFFICIENT** (first test was misconfigured — corrected to 1 real signal, still open, no scored outcome) | Daily-cadence option-chain coverage in the local archive (51 of 65 candidate days had none downloaded) |
| Golden Vex bounce (SPY) | **Blocked + name mismatch** | Point-in-time OI reaching 60 days (currently 13); also needs the actual described quantity identified, since "golden" ≠ vanna in this codebase |
| Flash Agentic floor bounce ($MU) | **INSUFFICIENT** | A real backtest harness for Flash Agentic against a matched control, and ≥30 observed instances |
