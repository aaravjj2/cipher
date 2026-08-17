---
name: options-analysis
description: Connect fundamental catalysts to read-only options chain, volatility, structure, and payoff analysis.
---

# Options analysis

Use Cipher's Alpaca-backed chain and quote routes first. The local core exposes
chain snapshots, latest quotes/trades, Greeks, IV, volume, OI, bars, flow,
matrix, and research-only structure analysis. Options Chain MCP is an optional
read-only fallback when Cipher data is unavailable; it requires separately
configured Alpaca or Tradier credentials.

For any structure, report expiration, strikes, bid/ask or mark assumptions,
spread width, maximum loss, breakeven, payoff at relevant prices, Greeks, IV,
liquidity, and data freshness. Include expected move and IV/skew context around
earnings when available.

Do not infer trade direction solely from flow or OI. Do not submit orders or
access account/order endpoints. A simulated payoff is not an execution plan.
