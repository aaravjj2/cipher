#!/usr/bin/env bash
# backup_irreplaceable.sh — protect the data that cannot be re-fetched.
#
# Most of data/ is regenerable: bar caches, chain snapshots, parity reports, the
# normalized Alpaca panel. Re-running a script restores them. A small subset is
# not, and it currently exists in exactly one copy on a WSL2 filesystem:
#
#   data/gex_history.sqlite       point-in-time open interest. THE critical one.
#                                 GEX = gamma x OI, so backtesting cluster or GEX
#                                 strategies needs OI as it stood on the day.
#                                 Replaying today's OI over past prices is
#                                 lookahead bias, so a lost day is lost forever --
#                                 it cannot be back-filled from any vendor.
#   data/accessobsidian_scans/    captures of the real product's own output. The
#                                 reference corpus every parity measurement is
#                                 checked against; past sessions cannot be
#                                 re-captured.
#   data/weight_lab/              labels, fitted weights, activation state.
#   data/paper_trades/            the forward-test ledger.
#   data/agentic_episodes.json    live episode anchors; losing it re-anchors every
#                                 open card's target.
#
# Losing gex_history.sqlite resets three separate accrual clocks at once
# (cluster/GEX backtesting, the flash label corpus, filter-mode sample size), each
# of which is measured in months.
#
# SQLite is copied with `.backup`, not `cp`: the capture cron writes to this file
# during market hours, and copying a live database with cp can produce a torn file
# that only fails when you finally need it.
#
# Usage:
#   ./scripts/backup_irreplaceable.sh            # local snapshot
#   ./scripts/backup_irreplaceable.sh --gcs      # local snapshot + push to GCS
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${CIPHER_BACKUP_ROOT:-$HOME/cipher-backups}"
BUCKET="${CIPHER_BACKUP_BUCKET:-project-eec91607-77a2-4be6-837-cipher-runtime}"
GCS_PREFIX="irreplaceable"
KEEP_LOCAL=14
LOG="$ROOT/logs/backup.log"
LOCK="/tmp/cipher_backup.lock"

DO_GCS=0
for arg in "$@"; do
  case "$arg" in
    --gcs) DO_GCS=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$ROOT/logs" "$BACKUP_ROOT"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: another backup holds the lock"
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"
log "backup start -> $DEST"

fail=0

# ── SQLite: consistent snapshot of a possibly-live database ──────────────────
if [[ -f "$ROOT/data/gex_history.sqlite" ]]; then
  if sqlite3 "$ROOT/data/gex_history.sqlite" ".backup '$DEST/gex_history.sqlite'" 2>>"$LOG"; then
    # Verify the COPY, not the source. A backup nobody has read is a rumour.
    integrity="$(sqlite3 "$DEST/gex_history.sqlite" "pragma integrity_check;" 2>>"$LOG" | head -1)"
    src_rows="$(sqlite3 "$ROOT/data/gex_history.sqlite" "select count(*) from gex_snapshots;" 2>/dev/null)"
    dst_rows="$(sqlite3 "$DEST/gex_history.sqlite" "select count(*) from gex_snapshots;" 2>/dev/null)"
    days="$(sqlite3 "$DEST/gex_history.sqlite" "select count(distinct substr(captured_at,1,10)) from gex_snapshots;" 2>/dev/null)"
    if [[ "$integrity" == "ok" && "$src_rows" == "$dst_rows" ]]; then
      log "  gex_history.sqlite ok — $dst_rows snapshots across $days capture days"
    else
      log "  gex_history.sqlite FAILED verification (integrity=$integrity src=$src_rows dst=$dst_rows)"
      fail=1
    fi
  else
    log "  gex_history.sqlite backup FAILED"
    fail=1
  fi
fi

# ── Directories and loose files ─────────────────────────────────────────────
for rel in accessobsidian_scans weight_lab paper_trades; do
  if [[ -d "$ROOT/data/$rel" ]]; then
    if tar -czf "$DEST/$rel.tar.gz" -C "$ROOT/data" "$rel" 2>>"$LOG"; then
      log "  $rel.tar.gz $(du -h "$DEST/$rel.tar.gz" | cut -f1)"
    else
      log "  $rel FAILED"; fail=1
    fi
  fi
done
for rel in agentic_episodes.json; do
  [[ -f "$ROOT/data/$rel" ]] && cp "$ROOT/data/$rel" "$DEST/" && log "  $rel"
done

# A manifest makes a restore verifiable instead of hopeful.
( cd "$DEST" && sha256sum ./* > MANIFEST.sha256 2>/dev/null )
log "  manifest: $(wc -l < "$DEST/MANIFEST.sha256") entries, total $(du -sh "$DEST" | cut -f1)"

# ── Offsite ─────────────────────────────────────────────────────────────────
if [[ "$DO_GCS" == "1" ]]; then
  if command -v gcloud >/dev/null 2>&1; then
    if gcloud storage cp -r "$DEST" "gs://$BUCKET/$GCS_PREFIX/$STAMP" >>"$LOG" 2>&1; then
      log "  pushed to gs://$BUCKET/$GCS_PREFIX/$STAMP"
    else
      log "  GCS push FAILED (local snapshot is still good)"
      fail=1
    fi
  else
    log "  gcloud not found; skipped offsite push"
  fi
fi

# ── Prune, newest-first, only after a clean run ──────────────────────────────
if [[ "$fail" == "0" ]]; then
  mapfile -t OLD < <(ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)))
  if [[ ${#OLD[@]} -gt 0 ]]; then
    rm -rf "${OLD[@]}"
    log "  pruned ${#OLD[@]} old snapshot(s), keeping $KEEP_LOCAL"
  fi
  log "backup ok"
else
  log "backup completed WITH FAILURES — not pruning"
fi
exit "$fail"
