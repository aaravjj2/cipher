# Execution-cost capture: what is measured and what is still assumed

`core/execution_cost.py` turns `DEFAULT_COST_BPS` from a hardcoded 2.0 into a
per-symbol measurement drawn from `data/tradier_stream.sqlite`. A symbol is only
measured if it is in the Tradier capture universe, so the universe is not a
detail — it decides which findings can be settled and which stay assumptions.

## The capture universe lives on the VM, not in this repository

`cipher-tradier.service` runs `/usr/local/lib/cipher/run-tradier-loop.sh`, which
reads `TRADIER_STREAM_SYMBOLS` from `/etc/cipher/cipher.env`. That file is
populated from Google Secret Manager by `cipher-secrets.service` and is not in git,
so a change there is invisible to anyone reading the source. This document is the
record.

**Changed 2026-08-08.** The VM was requesting only `SPY,QQQ,IWM`. Equity quote
coverage is the union of `--symbols` and `--option-underlyings`, and the latter
defaults to a 14-name mega-cap list, so 14 symbols were being measured — none of
which were the ten used as the out-of-sample set in `docs/backtest-findings.md`.
The out-of-sample verdict was therefore 1 of 10 measured and 9 of 10 assumed.

Now set to:

```
TRADIER_STREAM_SYMBOLS=SPY,QQQ,IWM,NFLX,COST,JPM,XOM,WMT,UNH,LLY,V,MA
```

Union with the option-underlying defaults gives 23 underlyings. Verified with
`--resolve-only`, which bypasses the market-hours guard: **135 stream symbols (23
underlying + 112 option contracts) against a 160 budget** (`TRADIER_MAX_STREAM_SYMBOLS`).
The previous configuration used 126, so this adds nine measured symbols for nine
slots and does not reduce option depth.

The prior file is backed up on the VM at `/etc/cipher/cipher.env.bak-20260808`.

## Why this was the binding constraint

Cost cancels when a strategy is compared against its matched random control, so it
never changed `beats_control_range` or the lift. It only ever moved the absolute
return. That makes the measurement worth exactly one thing: deciding whether a
strategy that clears its control also makes money net of what it costs to trade.

For the original ten the answer is now measured — spreads are roughly half the
assumption and the effect is larger than reported. For the disjoint ten it was not,
and that is the gap this change closes. The first captures under the new universe
land on the next regular session; until then `equity_half_spread_bps` returns
`assumed:symbol-not-captured` for those names, which is the honest label and the
reason the function returns provenance alongside the number.

## What this can never measure

Quoted spread from one vendor's consolidated feed. It excludes commissions and
market impact, and it describes the present, not 2016-2025. A multi-year backtest
cannot be costed from it. It can only be told whether its assumption is optimistic
against currently observable spreads — which, for liquid names, it was not.
