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

This proves that local Parquet is a viable cold-query format. It does not authorize deletion of SQLite rows.

## Append-only retention workflow

`scripts/parquet_retention.py` promotes the pilot mechanics into a resumable daily mirror. It accepts only completed UTC days, writes into `data/parquet_archive/tradier_stream_events/date=YYYY-MM-DD/`, atomically publishes the Parquet and audit files, and commits their strong source fingerprint to `retention.sqlite`. A repeated date is idempotent; missing or unledgered artifacts fail closed for manual inspection.

```bash
python3 scripts/parquet_retention.py --date 2026-08-10
python3 scripts/parquet_retention.py --status
```

This workflow intentionally has no pruning command. SQLite stays canonical and untouched, so late-arriving events can be detected before any future policy change. The existing private backup job remains responsible for irreplaceable-data backup and restore verification.
