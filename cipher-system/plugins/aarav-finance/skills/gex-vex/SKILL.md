---
name: gex-vex
description: Interpret Cipher and supplemental GEX/VEX exposure data with explicit public-OI and missing-data caveats.
---

# GEX/VEX interpretation

Use Cipher matrix or replay data when available. Preserve the canonical
convention:

```text
call_gex =  call_gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = -put_gamma  * put_oi  * 100 * spot**2 * 0.01
net_gex  = call_gex + put_gex
```

Missing gamma or OI is unknown, not zero. Clearly label modeled gamma, proxy OI,
ill-conditioned IV, stale snapshots, incomplete expirations, and provider
analytics. GEX is a public-open-interest heuristic, not verified dealer
positioning.

Describe levels and regimes as conditional market-structure context. Combine
with spot, realized volatility, IV, liquidity, time to expiry, and flow rather
than treating one exposure number as a forecast.
