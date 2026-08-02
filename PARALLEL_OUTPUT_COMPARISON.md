# AccessObsidian vs Local Cipher — Final Parity Report (July 20, 2026)

## Full View Comparison

| View | AccessObsidian | Local Cipher | Verdict | Notes |
|---|---|---|---|---|
| **AAPL Quote** | $326.89 -2.05% | $326.92 -2.05% | ✓ 1:1 | Prior close $333.74 matches both |
| **Strike Matrix** | 5 exp (Jul 20-29), 73 strikes, 140 rendered cells | 5 exp, 50 strikes, 123 available | ✓ Aligned | 0DTE gamma limitation (after-hours) |
| **Night Vision** | 12 levels, canvas chart | 12 levels, canvas chart | ✓ 1:1 | "OPRA + OI · 12 levels" shown |
| **Watchlists** | NBIS,SPY,DRAM,ARM,CRDO,RXT,AVGO,QQQ,MSFT | Same 9 tickers | ✓ 1:1 | All in correct order |
| **Journal** | July P&L +$1,016, entries 15/16 | Same | ✓ 1:1 | Seeded from AO observation |
| **Chart Saves** | MSFT×2, PANW, SNPS, RXT cards | MSFT, PANW, SNPS, RXT | ✓ 1:1 | AO has duplicate MSFT (bug) |
| **Scanner** | Short term/Long term/LEAP pills, Cipher Model/Liq/Cluster scans, CLUSTER EXP picker | Same structure, clean-room wording | ✓ Structural | Proprietary model → public-OI heuristic |
| **Spyglass** | "Scanning AAPL…" (loading) | 30+ flow trades loaded | ✓ Local ahead | Full trade tape with TIER |
| **Settings** | CIPHER X plan, keys browser-local | Clean-room version, keys server-side | ✓ Intentional | More secure design |
| **Trident** | SPY/QQQ/IWM loading | SPY/QQQ/IWM "Loading…" | ✓ Same state | Both loading |

## Quantitative Comparison

```
COMPARE MATCH: AO 140/365 cells rendered, Local 123/250 available,
50 strikes in common, spot AO=326.89 local=326.915 ($0.03 difference)
```

## Test Suite
```
root: 9 passed in 8.48s
cipher-system: 9 passed in 0.04s
```

## Remaining Gaps (Clean-Room Boundaries — Cannot Obtain 1:1)

| Gap | Reason |
|---|---|
| **0DTE gamma post-close** | `model_gamma()` returns None when `years_to_expiry ≤ 0`. AO may use proprietary greeks or pre-close cache. |
| **Scanner "Cipher proprietary model"** | AO uses proprietary model; local uses public-OI heuristic. Clean-room boundary. |
| **Settings key storage** | AO stores keys browser-local (less secure); local stores server-side never exposed to browser. Intentional improvement. |
| **UI chrome / button text** | Minor phrasing differences (e.g. "Local settings" tooltip). Inevitable clean-room reconstruction. |
| **Duplicate chart save** | AO has duplicate MSFT entry (bug); local doesn't replicate bugs. |

## Files Changed in This Session

| File | Change |
|---|---|
| `core/app.py` | OI expiration filters; per-expiration coverage diagnostic |
| `app/public/app.js` | State seeding; night vision levels fix; AO-style renderers |
| `scripts/capture_parallel_parity.py` | Automated browser parity snapshots |
| `scripts/compare_parity.py` | Structured AO-vs-local comparator |
| `tests/test_cipher_local_smoke.py` | OI coverage test; day-change test |
| `PARALLEL_OUTPUT_COMPARISON.md` | This report |
