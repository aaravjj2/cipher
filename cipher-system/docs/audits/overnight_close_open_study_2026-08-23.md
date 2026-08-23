# Close-to-Next-Open Strategy Study — 2026-08-23

## Protocol

The stock protocol buys the adjusted official regular-session close and sells
at the next observed regular-session open. Baseline friction is 2 basis points
per side. The archive contains 39 stocks and ETFs, with the common broad panel
covering January 2016 through August 4, 2026 and MU updated through August 21.

The separate closing-minutes check uses the last locally captured bar from
15:55–15:59 ET and the first bar from 09:30–09:34 ET. It exists to test whether
the daily close is a reasonable proxy for the requested timing.

The option protocol selects the nearest-expiry, nearest-to-spot call and put
using only information present in the closing window. Expiration must survive
the next open and be no more than 14 calendar days away. Entry is the last
observed option trade-bar close from 15:55–15:59 ET; exit is the first observed
trade-bar open from 09:30–09:34 ET. Baseline stress marks entry 2.5% worse and
exit 2.5% worse. These are trade bars, not historical NBBO quotes, and therefore
are observations rather than executable fill claims.

## MU stock result

At the 2 bps-per-side baseline, MU produced:

- 2,673 overnight observations from January 4, 2016 through August 21, 2026
- 1,448 wins and 1,225 losses; 54.17% win rate
- average net return 0.1394% per night
- median net return 0.1279%
- normal 95% interval for the average: 0.0602% to 0.2186%
- profit factor 1.222
- fixed-notional total 372.57%; this equals approximately $37,256.68 when the
  same $10,000 notional is used independently each night
- full-capital path maximum drawdown -61.17%

The exact locally captured closing-minutes MU holdout is much smaller: 19
trades from June 24 through July 22, 2026, 42.11% wins, average +0.1844%, median
-0.7793%, profit factor 1.083, and -20.87% maximum drawdown. It is directionally
positive but too small and internally fragile to validate the daily result.

### MU regime stability

| Period | N | Win rate | Avg net/night | Fixed-notional total | Profit factor |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2016–2019 | 1,006 | 57.16% | +0.1448% | +145.69% | 1.284 |
| 2020–2022 | 756 | 50.79% | -0.0115% | -8.73% | 0.983 |
| 2023 | 250 | 50.00% | +0.0363% | +9.08% | 1.076 |
| 2024–2026 | 661 | 55.07% | +0.3427% | +226.52% | 1.420 |

The edge is not stable in every regime: 2020–2022 was negative.

### MU cost sensitivity

| Cost per side | Avg/night | 95% average interval | Win rate | Profit factor | Fixed-notional total |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 bps | +0.1794% | +0.1002% to +0.2586% | 55.11% | 1.294 | +479.49% |
| 2 bps | +0.1394% | +0.0602% to +0.2186% | 54.17% | 1.222 | +372.57% |
| 5 bps | +0.0794% | +0.0002% to +0.1586% | 52.08% | 1.121 | +212.19% |
| 10 bps | -0.0206% | -0.0998% to +0.0586% | 48.41% | 0.971 | -55.11% |

This makes execution quality central. It is a research candidate at low costs,
not a strategy to run blindly with market-on-close/open slippage.

## Broad stock result

At 2 bps per side, only five of 39 symbols had a positive lower bound on the
normal 95% interval for average overnight return:

| Rank | Ticker | N | Win rate | Avg net/night | 95% interval | Profit factor |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | MU | 2,673 | 54.17% | +0.1394% | +0.0602% to +0.2186% | 1.222 |
| 2 | NVDA | 2,660 | 56.28% | +0.1393% | +0.0673% to +0.2114% | 1.265 |
| 3 | SMH | 2,660 | 54.81% | +0.0729% | +0.0235% to +0.1224% | 1.176 |
| 4 | AMZN | 2,660 | 55.38% | +0.0668% | +0.0127% to +0.1208% | 1.177 |
| 5 | SOXX | 2,660 | 54.96% | +0.0649% | +0.0154% to +0.1143% | 1.154 |

The ranking confirms that the effect is concentrated in high-growth and
semiconductor exposures, not a universal close-to-open premium. AAPL, bonds,
defensive sectors, and several international funds were negative after costs.

## Captured options result

All 37 local historical-option databases were inventoried. Across their union,
4,875 contracts were selected using closing-time information. A next-open print
was observed for 4,728 selections; 147 were explicitly missing and excluded.
Overlapping archives were deduplicated before ranking.

There is no historical MU option-bar archive, so no MU option return was
invented from current OI or chain snapshots. QQQ and IWM targeted archives also
lack the intraday underlying session bars needed for unbiased ATM selection.
Same-day SPY archives contain closing bars but their contracts expire before
the next open and are correctly ineligible.

At the 2.5%-per-side friction stress, the most attractive average results were
MSFT calls (+8.66%, N=41), META calls (+3.20%, N=240), SPY calls (+0.84%, N=31),
and NVDA calls (+0.80%, N=588). None had a positive lower 95% confidence bound.
Their medians were -2.69%, -4.96%, +0.25%, and -3.86% respectively, showing
that large positive outliers drive most of the averages. With 5% friction per
side, only MSFT calls retained a positive average, and its 95% interval still
spanned -20.88% to +27.59%.

The option version therefore fails promotion. Buying unhedged overnight calls
or puts every day is not supported by this archive. Any next test should use a
predeclared MU/NVDA stock filter, bounded premium sizing, and newly captured
MU NBBO-quality close/open quotes prospectively.

## Artifacts

Reproducible JSON, Markdown, ranking CSVs, sensitivity CSVs, and full trade
ledgers are stored in `cipher-system/data/overnight_close_open_lab/`.
