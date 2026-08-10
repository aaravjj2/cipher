# Tradier stream Parquet offload pilot

The non-destructive pilot in `scripts/parquet_offload.py` snapshots one completed UTC day from `tradier_stream.sqlite`. It opens SQLite read-only, exports all event columns with Zstandard compression, and refuses to keep the output unless the source and Parquet fingerprints agree.

## 2026-08-10 measured result

| Measurement | Result |
|---|---:|
| Rows | 12,863,717 |
| Event IDs | 95,912,286–108,776,002 |
| Captured interval | 13:30:12–19:59:59 UTC |
| Estimated SQLite partition | 5,557,926,456 bytes |
| Parquet | 351,533,670 bytes |
| Estimated compression ratio | 15.811× |
| SQLite fingerprint query | 225.201 seconds |
| Parquet fingerprint query | 3.744 seconds |
| Fingerprint query speedup | 60.15× |
| Round-trip logical match | yes |
| Source deleted | no |

The SQLite partition size is an explicitly labeled estimate based on the day's share of monotonically increasing event IDs; SQLite does not expose exact bytes by logical date. The Parquet size and query timings are measured directly.

Round-trip verification covered every stored column, including `raw_json`, and matched row count, ID bounds, capture-time bounds, a 64-bit XOR hash, and a summed 128-bit hash. The local audit artifact is `data/parquet_pilots/tradier_stream_events_20260810.audit.json`; generated data remains ignored by Git.

This proves that local Parquet is a viable cold-query format. It does not authorize deletion of SQLite rows. A production retention job still needs content-addressed private backup, restore testing, an atomic partition ledger, and a policy for late-arriving events before pruning can be considered.
