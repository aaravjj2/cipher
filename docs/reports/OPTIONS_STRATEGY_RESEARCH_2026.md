# Cipher Options Strategy Research — Literature Review and Primary Candidate

**Research date:** 2026-07-26  
**Scope:** Strategy selection for the strict point-in-time options backtester  
**Status:** Research hypothesis only; no options edge has been validated by Cipher

## Executive conclusion

The strongest practical candidate for Cipher is **conditional, fully collateralized broad-index put writing when 30-day implied volatility is materially above trailing realized volatility**.

This is not a recommendation to sell puts continuously. The literature supports a persistent variance/crash-insurance premium, but it also shows severe left-tail exposure, microstructure sensitivity, and evidence that the premium can shrink. Cipher should therefore test two variants:

1. **Academic benchmark replication:** monthly cash-secured broad-index put writing.
2. **Safety adaptation:** a defined-risk put vertical activated by the identical volatility-premium signal.

The benchmark establishes whether the historical premium exists in the acquired dataset. The vertical tests whether tail loss can be capped without surrendering the edge after spreads and fees. The safety adaptation is a new project hypothesis, not a result established by the cited papers.

## Ranked strategy families

| Rank | Strategy family | Evidence | Practicality | Main issue | Cipher decision |
|---:|---|---|---|---|---|
| 1 | Conditional index put-write using IV/RV | Strong, repeated | High | Crash loss; premium may compress | Primary strict-backtest candidate |
| 2 | Cross-sectional option momentum | Strong peer-reviewed evidence | Medium-low | Requires broad historical option surface and turnover | Secondary research arm |
| 3 | Cross-sectional IV-versus-RV relative value | Strong foundational evidence | Low | Single-name gaps, microstructure and survivorship | Institutional-data arm |
| 4 | Weekly index put-write | Good benchmark evidence | High | Concentrated gamma and weekly crash exposure | Benchmark only until intraday data |
| 5 | Defined-risk iron condor | Moderate benchmark evidence | Medium | Weak call-side premium; four-leg costs | Comparator, not default winner |
| 6 | Covered call | Long benchmark history | High | Caps equity upside; often an income transformation rather than alpha | Benchmark only |
| 7 | Dispersion/correlation risk | Strong theoretical evidence | Very low | Realistic frictions can erase returns | Reject for current project |
| 8 | Unconditional long options | Strong evidence as insurance | High | Persistent premium drag | Hedge sleeve, not return engine |

## Major papers and what they imply

### 1. Carr and Wu — “Variance Risk Premiums”

The paper formalizes a model-free variance-risk-premium measure using option portfolios and documents variance premia across major indexes and individual stocks. This establishes the economic foundation for selling volatility only when option-implied variance is sufficiently above expected realized variance.

**Cipher implication:** the entry signal must be based on observed option prices and point-in-time realized-volatility information. A stock-price proxy is insufficient.

### 2. Goyal and Saretto — “Cross-section of Option Returns and Volatility”

The authors sort stocks using the difference between trailing historical volatility and one-month at-the-money implied volatility. Long relatively cheap volatility and short relatively expensive volatility generate significant returns across straddles and delta-hedged options.

**Cipher implication:** implied-versus-realized volatility is better supported than a generic high-IV rule. It should be the primary state variable.

### 3. Bollerslev, Tauchen, and Zhou — “Expected Stock Returns and Variance Risk Premia”

The variance premium predicts aggregate market returns and varies over time.

**Cipher implication:** the premium is conditional, not constant. Permanent option selling is an unnecessarily weak specification.

### 4. Chambers, Foy, Liebner, and Lu — “Index Option Returns: Still Puzzling”

Using a sample including major volatility regimes, the study finds that index put returns are difficult to reconcile with standard option-pricing models and that crash protection often carries a substantial premium.

**Cipher implication:** broad-index puts are the cleanest place to test the crash-insurance premium, but the seller is explicitly accepting crash risk.

### 5. Bondarenko — “Historical Performance of Put-Writing Strategies”

The Cboe monthly PUT and weekly WPUT benchmarks historically produced competitive returns with lower volatility and drawdown than the S&P 500 over the studied periods. The weekly benchmark collected premium more frequently but concentrates short-gamma risk into short expirations.

**Cipher implication:** replicate monthly PUT first. WPUT requires intraday-quality data and stronger tail-event execution modeling.

### 6. Malkiel, Rinaudo, and Saha — “Option Writing: Using VIX to Improve Returns”

The paper reports that option-writing performance improves when selling is conditioned on elevated implied volatility rather than performed continuously.

**Cipher implication:** add a volatility-regime gate, but do not optimize a VIX cutoff on the full sample. Thresholds must be selected inside walk-forward training folds.

### 7. Driessen, Maenhout, and Vilkov — “The Price of Correlation Risk”

Dispersion appears attractive before costs, but the paper concludes that realistic trading frictions prevent easy exploitation.

**Cipher implication:** reject dispersion for the current project. It needs many synchronized component options, continuous hedging, and institutional execution.

### 8. Heston, Jones, Khorram, Li, and Mo — “Option Momentum”

Monthly option portfolio returns show continuation over long formation horizons, robust to delta hedging and alternate contracts.

**Cipher implication:** this is the strongest non-VRP secondary candidate. It requires a broad survivorship-safe universe, monthly historical surfaces, corporate-action handling, and robust transaction-cost controls.

### 9. Duarte, Jones, and Wang — “Very Noisy Option Prices and Inference Regarding the Volatility Risk Premium”

The paper demonstrates that option-return conclusions are highly sensitive to microstructure treatment and finds negative delta-hedged option returns after addressing biases.

**Cipher implication:** quote filtering, executable bid/ask fills, liquidity controls, and duplicate/stale-quote rejection are mandatory. Midpoint-only research is not sufficient.

### 10. Dew-Becker and Giglio — “Risk Preferences Implied by Synthetic Options”

The authors report that the gap between traded and synthetic option returns has narrowed in recent years, consistent with a shrinking variance-risk premium as hedging and intermediation improve.

**Cipher implication:** historical success does not guarantee a current premium. Results must be broken out by decade and recent subperiod, not only pooled.

### 11. New 2025–2026 research

Recent work on common factors, seasonal option momentum, and the implied/realized volatility ratio strengthens the case that option expected returns are state-dependent and cross-sectional. These papers are valuable research extensions but should not override the more established evidence until replicated independently.

## Primary strategy specification

### Strategy name

`conditional_index_put_write_iv_over_rv`

### Research universe

- Primary benchmark: SPX cash-settled European options.
- Lower-notional alternative: XSP.
- ETF comparator: SPY, with American exercise and dividend-assignment modeling.
- Do not pool index and single-stock results.

### Signal inputs

All inputs must be known at the decision timestamp:

- 30-day at-the-money implied volatility from observed option quotes.
- Trailing 30-day realized volatility from underlying returns.
- IV/RV ratio.
- VIX percentile using history available only through the decision timestamp.
- Front/second-month VIX term-structure ratio.
- Underlying 200-day trend state.
- Candidate contract DTE, delta, quote spread, volume and open interest.

### Initial hypothesis gates

These are starting hypotheses, not optimized production values:

- IV/RV ratio at least 1.15.
- VIX percentile between 60% and 95%.
- Exclude severe VIX backwardation, initially front/second ratio above 1.05.
- Underlying above its trailing 200-day average.
- Expiration between 28 and 45 calendar days.
- Short-put absolute delta between 0.15 and 0.30.
- Observed bid/ask spread no more than 12% of midpoint.

### Required strategy arms

1. **Unconditional PUT replication** — monthly ATM cash-secured put.
2. **IV/RV-only PUT** — tests the foundational volatility-premium signal.
3. **VIX-only conditional PUT** — isolates the regime-filter contribution.
4. **Combined conditional PUT** — IV/RV plus volatility and trend gates.
5. **Defined-risk vertical** — same signal, short 15–30 delta put and long farther-OTM wing.
6. **No-trade benchmark** — collateral earns the contemporaneous Treasury rate.

### Execution requirements

- Enter and exit using side-aware observed bid/ask quotes.
- Include commissions, exchange fees, regulatory fees and slippage.
- Reject stale, crossed, zero or synthetic quotes.
- Reserve full cash collateral for the benchmark and maximum-loss collateral for verticals.
- Mark open positions using executable liquidation value.
- Model expiration and SPY early-assignment risk.
- Do not use current open interest to select historical contracts unless historical OI is available.

### Validation requirements

- At least one major crash regime, one prolonged bear market and multiple low-volatility years.
- Anchored walk-forward validation with an embargo around fold boundaries.
- Parameter selection inside training folds only.
- Reality Check / SPA or another multiple-testing correction.
- Block bootstrap by calendar period and underlying.
- Tail diagnostics: expected shortfall, worst expiry cycle, gap loss, skewness and drawdown duration.
- Cost sensitivity at base, 2× and stressed bid/ask assumptions.
- Recent-period analysis to test whether the VRP has weakened.
- Comparison with S&P 500 total return, Treasury collateral and Cboe-style PUT replication.

## Rejection criteria

The primary strategy is rejected if any of the following occurs:

- Net out-of-sample alpha is non-positive after costs.
- Performance is concentrated in one crisis or one early historical subperiod.
- A single expiry cycle explains more than a material share of total P&L.
- Defined-risk adaptation loses the entire apparent edge after four-leg costs.
- Results depend on midpoint fills, future-known open interest or current-universe membership.
- Tail loss exceeds the predeclared risk budget even when average performance is positive.

## Final verdict

**Best research candidate:** conditional monthly broad-index put writing based on a positive implied-versus-realized volatility premium.

**Best deployable structure:** not yet known. The strict backtest must decide between the cash-secured benchmark and the defined-risk vertical.

**Current evidence status:** literature-supported hypothesis, not a Cipher-validated strategy.

## Reference set

- Carr, Peter, and Liuren Wu. “Variance Risk Premiums.” *Review of Financial Studies*, 2009. DOI: 10.1093/rfs/hhn038.
- Goyal, Amit, and Alessio Saretto. “Cross-section of Option Returns and Volatility.” *Journal of Financial Economics*, 2009. DOI: 10.1016/j.jfineco.2009.01.001.
- Bollerslev, Tim, George Tauchen, and Hao Zhou. “Expected Stock Returns and Variance Risk Premia.” *Review of Financial Studies*, 2009. DOI: 10.1093/rfs/hhp008.
- Driessen, Joost, Pascal Maenhout, and Grigory Vilkov. “The Price of Correlation Risk.” *Journal of Finance*, 2009. DOI: 10.1111/j.1540-6261.2009.01467.x.
- Chambers, Donald R., Matthew Foy, Jeffrey Liebner, and Qin Lu. “Index Option Returns: Still Puzzling.” *Review of Financial Studies*, 2014. DOI: 10.1093/rfs/hhu020.
- Bondarenko, Oleg. “Historical Performance of Put-Writing Strategies.” 2019.
- Malkiel, Burton G., Alex Rinaudo, and Atanu Saha. “Option Writing: Using VIX to Improve Returns.” *Journal of Derivatives*, 2018.
- Heston, Steven L., Christopher S. Jones, Mehdi Khorram, Shiyang Li, and Haitao Mo. “Option Momentum.” *Journal of Finance*, 2023. DOI: 10.1111/jofi.13279.
- Dew-Becker, Ian, and Stefano Giglio. “Risk Preferences Implied by Synthetic Options.” NBER Working Paper 31833, 2023.
- Duarte, Jefferson, Christopher S. Jones, and Junbo L. Wang. “Very Noisy Option Prices and Inference Regarding the Volatility Risk Premium.” *Journal of Finance*, 2024. DOI: 10.1111/jofi.13365.
- Horenstein, Alex, Aurelio Vasquez, and Xiao Xiao. “Common Factors in Equity Option Returns.” *Review of Financial Studies*, 2026. DOI: 10.1093/rfs/hhaf060.
- Heston, Steven L., Christopher S. Jones, and Haitao Mo. “The Variance Premium and Seasonal Momentum in Option Returns.” *Review of Financial Studies*, 2026. DOI: 10.1093/rfs/hhag057.
