# Cipher Core (read-only)

Local market-data + GEX/VEX/flow service for the Cipher Research app.

## Run

```bash
CIPHER_CORE_PORT=8282 python3 core/app.py
```

Health: `http://127.0.0.1:8282/health`

## Routes

| Path | Purpose |
|------|---------|
| `/health` | Credential status (no secrets) |
| `/api/quote` | Underlying quote (SIP preferred) |
| `/api/matrix` | Strike × expiration GEX/VEX surface |
| `/api/heatmap` | Columnar heatmap contract |
| `/api/night-vision` | Matrix + ranked exposure levels |
| `/api/bars` | OHLCV candles |
| `/api/flow` | Spyglass tape from latest option trades |
| `/api/stream` | SSE live quote / matrix / flow |

GEX uses `gamma × OI × 100 × spot² × 0.01` with puts signed negative. Missing Greek/OI is unknown, never zero.
