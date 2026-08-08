# Reconciling the two capture histories

This system captures on two machines. The GCP VM runs `cipher-gex.service`
continuously; this workstation captured intermittently. Neither history was a
superset of the other, and point-in-time open interest cannot be re-fetched from
any vendor, so reconciling them wrong is unrecoverable.

## What the two held

```
              local    VM
2026-07-22       16     16
2026-07-23     1003   1003
2026-07-27     4797   4797
2026-07-28     4742   4742
2026-07-29     3739   3739
2026-07-30     2700   2700
2026-07-31     2689   2689
2026-08-03        0   4838
2026-08-04        0   4901
2026-08-05        0   4807
2026-08-06        0   4261
2026-08-07      955   4259
              20641  42752
```

After reconciliation: **43,707 snapshots over 12 capture days**, 545 tickers.

## The mistake worth recording

The first version of `scripts/pull_vm_captures.sh` compared the two databases by
**per-day snapshot counts**. On 2026-08-07 the VM held 4259 and this machine 955,
which read as strict dominance, so the swap was allowed.

It was not dominance. Both machines captured that day at different minutes, so 955
of the local rows were observations the VM never made. The swap dropped all 955
from the index. Their raw payloads survived on disk only because the extraction
used `tar -k`, and the previous database survived only because the script keeps a
timestamped backup. Without either, 955 point-in-time observations would have
become unfindable while still occupying disk.

Counts answer "who has more". The question being asked is "does the replacement
contain everything the original did", and only identity — `(ticker, captured_at)`
— answers that. The check now compares identities and refuses the swap on any
local-only row, pointing at `scripts/merge_gex_snapshots.py`, which combines the
two histories rather than choosing between them.

## What the database does and does not contain

`gex_snapshots` stores summary levels only: spot, call wall, put wall, gamma flip.
**The per-strike open interest lives exclusively in the raw JSON payloads** under
`data/gex_snapshots/<TICKER>/`. A pulled database without its payload tree is an
index to data the machine does not have, so the 363 MB tree (35 MB compressed,
42,752 files) travels with it.

`raw_json_path` holds absolute paths and the two machines use different roots
(`/home/aarav/Aarav/cipher/cipher-github/cipher-system` on the VM against
`/home/aarav/Aarav/cipher/cipher-system` here), so the pull rewrites the prefix.
Unrewritten, every payload reads as missing while sitting on disk.

## Direction

One way: the VM is authoritative for captured data and this machine pulls. Pushing
upward risks replacing a richer history with a poorer one, and that is the single
mistake in this system that cannot be undone.
