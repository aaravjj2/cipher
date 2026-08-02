# Cipher research — 2026-07-15

Run: `run_f796e68830f0`
Tickers: QQQ, SPY

## Evidence
### strike_matrix · SPY
Validation observation: Strike Matrix fields are stored as structured evidence; no live market interpretation made.
Values: `{"spot": "demo-visible-check", "mode": "GEX/VEX", "fields_checked": ["gamma flip", "call wall", "put wall", "expiration grid"]}`
Confidence: observed

### night_vision · SPY
Validation observation: Night Vision step can preserve chart context and level notes from visible browser evidence.
Values: `{"views_checked": ["chart", "X-Ray", "timeframe controls", "levels above and below spot"]}`
Confidence: observed

### spyglass · SPY
Validation observation: Spyglass flow can be recorded with explicit uncertainty around quote-side labels.
Values: `{"filters_checked": ["date", "premium tier", "calls/puts", "bid/ask side"], "quote_side_warning": "inference only"}`
Confidence: uncertain

### scanner · SPY
Validation observation: Scanner evidence is stored as setup context only, not as an order instruction.
Values: `{"scanner_modes": ["short term", "long term", "LEAP", "cluster", "liq"]}`
Confidence: observed

### synthesis · SPY
Validation synthesis: matrix, chart, flow, and scanner fields all reached storage and can be exported; this is a mechanical test, not live research.
Values: `{"agreement": "demo complete", "missing_live_data": true}`
Confidence: observed

## Captures
- strike_matrix: existing Cipher Strike Matrix reference — `C:\tmp\cipher-strike_matrix.png`

## Caveat
Research context only. OI-based exposure is a heuristic; quote-side labels are inference, not verified participant intent. No trade instruction is produced.