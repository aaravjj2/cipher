# Cipher research — 2026-07-15

Run: `run_74efef8bbc1d`
Tickers: QQQ, SPY

## Evidence
### Strike Matrix · SPY
Validation sample based on visible-page capture workflow; not live market data.
Values: `{"spot": 600.12, "gamma_flip": 598, "call_wall": 605, "put_wall": 595}`
Confidence: medium

### Night Vision / X-Ray · SPY
Validation sample to confirm evidence storage.
Values: `{"hot_strikes": [600, 605], "support": 598, "resistance": 605}`
Confidence: medium

### Spyglass / Flow · SPY
No live flow data supplied during validation.
Values: `{"contracts_reviewed": 0, "premium_threshold": "visible filters only"}`
Confidence: low

### Setup Scanner · SPY
No live scan executed during validation.
Values: `{"setups_reviewed": 0}`
Confidence: low

### Synthesis · SPY
Research workflow test only; no recommendation generated.
Values: `{"status": "validation complete"}`
Confidence: high

## Captures
- Strike Matrix: Reference capture — `C:\Aarav\cipher-system\references\cipher-strike_matrix.png`

## Caveat
Research context only. OI-based exposure is a heuristic; quote-side labels are inference, not verified participant intent. No trade instruction is produced.