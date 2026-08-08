#!/usr/bin/env bash
# pull_vm_captures.sh — bring the VM's capture history down to this machine.
#
# There are two Cipher instances and they hold different data. The GCP VM runs
# cipher-gex.service and cipher-tradier.service continuously, so it accrues capture
# days this machine does not: as of 2026-08-08 the VM held 12 days / 42,752 GEX
# snapshots against 8 days / 20,641 here. Point-in-time open interest cannot be
# re-fetched from any vendor, so the VM's extra days are irreplaceable and this
# machine's copy is simply behind.
#
# The direction is deliberate and one-way: the VM is authoritative for captured
# data and this machine pulls. Pushing local captures upward would risk replacing a
# richer history with a poorer one, which is the one mistake in this system that
# cannot be undone.
#
# Three things make the pull safe rather than a blind overwrite:
#
#   * The VM copy is taken with sqlite3 `.backup`, not `cp`. The capture loop
#     writes during market hours and copying a live database can yield a torn file
#     that only fails when you finally need it.
#   * The pulled database is compared against the local one by snapshot IDENTITY
#     -- (ticker, captured_at) -- not by per-day counts. Counts are not enough:
#     both machines captured on 2026-08-07 at different minutes, so the VM's 4259
#     rows against this machine's 955 read as strict dominance while 955 of the
#     local rows were observations the VM never made. The script refuses the swap
#     when local holds any snapshot the VM does not, and points at
#     merge_gex_snapshots.py, which combines the two instead of choosing between
#     them.
#   * The existing local database is kept as a timestamped backup, never deleted.
#
# raw_json_path is rewritten on import. The VM stores absolute paths under
# /home/aarav/Aarav/cipher/cipher-github/cipher-system while this machine's root is
# /home/aarav/Aarav/cipher/cipher-system, so the paths would otherwise resolve to
# nothing and every raw payload would read as missing.
#
# Usage:
#   ./scripts/pull_vm_captures.sh              # check and swap if the VM dominates
#   ./scripts/pull_vm_captures.sh --dry-run    # report the difference, change nothing
#   ./scripts/pull_vm_captures.sh --with-raw   # also rsync the raw payload tree
#
# The raw payload tree is not optional in practice. gex_snapshots stores only the
# summary levels; the per-strike open interest that GEX backtesting needs lives
# ONLY in the raw JSON, so a pulled database without its payloads is an index to
# data this machine does not have.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM_NAME="${CIPHER_VM_NAME:-cipher-main}"
VM_ZONE="${CIPHER_VM_ZONE:-us-central1-a}"
VM_ROOT="${CIPHER_VM_ROOT:-/home/aarav/Aarav/cipher/cipher-github/cipher-system}"
DB_REL="data/gex_history.sqlite"
LOCAL_DB="$ROOT/$DB_REL"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$ROOT/data/.vm_pull"

DRY_RUN=0
WITH_RAW=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --with-raw) WITH_RAW=1 ;;
    --force) FORCE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ssh_vm() { gcloud compute ssh "$VM_NAME" --zone "$VM_ZONE" --tunnel-through-iap --command "$1" 2>/dev/null; }

mkdir -p "$STAGE"

echo "Taking a consistent copy on $VM_NAME…"
ssh_vm "rm -f /tmp/gex_pull.sqlite && sqlite3 '$VM_ROOT/$DB_REL' \".backup /tmp/gex_pull.sqlite\" && ls -l /tmp/gex_pull.sqlite | awk '{print \$5}'" || {
  echo "failed to snapshot the VM database" >&2; exit 1; }

echo "Downloading…"
gcloud compute scp "$VM_NAME:/tmp/gex_pull.sqlite" "$STAGE/gex_pull.sqlite" \
  --zone "$VM_ZONE" --tunnel-through-iap >/dev/null 2>&1 || {
  echo "download failed" >&2; exit 1; }
ssh_vm "rm -f /tmp/gex_pull.sqlite" >/dev/null

if ! sqlite3 "$STAGE/gex_pull.sqlite" "pragma integrity_check;" | grep -q '^ok$'; then
  echo "pulled database failed integrity_check — refusing to use it" >&2
  exit 1
fi

echo
echo "Comparing capture coverage (local vs pulled):"
python3 - "$LOCAL_DB" "$STAGE/gex_pull.sqlite" <<'PY'
import sqlite3, sys
local_path, pulled_path = sys.argv[1], sys.argv[2]

def rows(path):
    """Snapshot identities, not counts.

    Comparing per-day totals is not sufficient and this was found the hard way:
    on 2026-08-07 the VM held 4259 snapshots and this machine 955, so a count
    comparison read as "the VM strictly dominates" and the swap was allowed. But
    the two machines captured at different minutes, so 955 of those local rows
    were observations the VM never made, and the swap dropped every one of them
    from the index while their raw payloads stayed on disk. Identity is the only
    comparison that answers the question actually being asked.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        return {r[0] for r in conn.execute(
            "select ticker || '|' || captured_at from gex_snapshots")}
    finally:
        conn.close()

a, b = rows(local_path), rows(pulled_path)
only_local = a - b

by_day_local, by_day_vm, by_day_only = {}, {}, {}
for key in a:
    d = key.split("|")[1][:10]; by_day_local[d] = by_day_local.get(d, 0) + 1
for key in b:
    d = key.split("|")[1][:10]; by_day_vm[d] = by_day_vm.get(d, 0) + 1
for key in only_local:
    d = key.split("|")[1][:10]; by_day_only[d] = by_day_only.get(d, 0) + 1

for day in sorted(set(by_day_local) | set(by_day_vm)):
    la, lb, lo = by_day_local.get(day, 0), by_day_vm.get(day, 0), by_day_only.get(day, 0)
    flag = f"  <-- {lo} LOCAL-ONLY" if lo else ""
    print(f"  {day}  local={la:>6d}  vm={lb:>6d}{flag}")
print(f"\n  local total={len(a)}  vm total={len(b)}  local-only={len(only_local)}")
sys.exit(3 if only_local else 0)
PY
COVERAGE=$?

if [[ "$COVERAGE" -eq 3 && "$FORCE" -ne 1 ]]; then
  echo
  echo "REFUSING: this machine holds snapshots the VM does not." >&2
  echo "Their raw payloads would survive on disk but drop out of the index, which" >&2
  echo "makes point-in-time open interest you still own impossible to find." >&2
  echo >&2
  echo "The fix is to merge rather than choose:" >&2
  echo "  ./scripts/pull_vm_captures.sh --force" >&2
  echo "  python3 scripts/merge_gex_snapshots.py --from ${DB_REL}.pre-pull-<stamp>" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo
  echo "--dry-run: nothing replaced. Pulled copy left at $STAGE/gex_pull.sqlite"
  exit 0
fi

echo
echo "Rewriting raw_json_path for this machine's root…"
sqlite3 "$STAGE/gex_pull.sqlite" \
  "update gex_snapshots set raw_json_path = replace(raw_json_path, '$VM_ROOT/', '$ROOT/');"
REMAINING=$(sqlite3 "$STAGE/gex_pull.sqlite" \
  "select count(*) from gex_snapshots where raw_json_path like '$VM_ROOT/%';")
echo "  rows still pointing at the VM root: $REMAINING"

if [[ "$WITH_RAW" -eq 1 ]]; then
  echo "Syncing raw payload tree (this is the large one)…"
  gcloud compute scp --recurse --compress \
    "$VM_NAME:$VM_ROOT/data/gex_snapshots" "$ROOT/data/" \
    --zone "$VM_ZONE" --tunnel-through-iap >/dev/null 2>&1 \
    && echo "  raw payloads synced" \
    || echo "  raw payload sync failed (database still swapped)" >&2
fi

if [[ -f "$LOCAL_DB" ]]; then
  cp -p "$LOCAL_DB" "$LOCAL_DB.pre-pull-$STAMP"
  echo "Previous local database kept at $(basename "$LOCAL_DB").pre-pull-$STAMP"
fi
mv "$STAGE/gex_pull.sqlite" "$LOCAL_DB"

echo
sqlite3 "file:$LOCAL_DB?mode=ro" \
  "select 'now: ' || count(*) || ' snapshots over ' || count(distinct substr(captured_at,1,10)) || ' capture days' from gex_snapshots;"
echo "Done."
