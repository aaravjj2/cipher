#!/usr/bin/env bash
#
# Build the Next.js frontend and publish it to the directory the server actually serves.
#
# app/server.mjs serves static files out of app/public (see the `join(root, "public", …)`
# path guard). `npm run build` writes to web/out. Nothing connected the two, so running a
# build and reloading the page showed the old UI indefinitely — the build succeeded, the
# server was fine, and the change simply was not in the served tree. That failure mode
# looks exactly like a browser cache problem, which is what makes it expensive: the
# obvious fixes (hard refresh, restart the server) all appear to do nothing.
#
# web/out and app/public are both pure build output. Publishing stages a complete
# sibling tree, verifies it, then atomically exchanges the two directories with
# renameat2. A request therefore sees either the old release or the new one, never
# half of each. The exchanged old tree becomes a timestamped rollback release.
# `--check` reports whether the served tree matches the last build without building or
# copying anything, so "is the served build current?" is answerable at a glance instead of
# being diagnosed through a browser. It exits 1 on drift, which makes it usable as a guard
# before browser verification — confirming a UI change against a stale bundle wastes far more
# time than the check costs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# rsync itself is the comparison: a dry-run with the same flags as the real publish lists
# exactly the files that would change. Reimplementing the diff with checksums or timestamps
# would risk disagreeing with the copy that actually happens.
drift() {
  # Directory entries differing only in attributes (rsync itemizes them as `.d` followed by
  # dots and `t`) are dropped: a directory mtime shifts whenever a file inside it is written,
  # so counting them reports drift for directories whose contents already match.
  rsync -ain --delete "$ROOT/web/out/" "$ROOT/app/public/" 2>/dev/null \
    | grep -vE '^\.d[.t]+[[:space:]]' || true
}

if [[ "${1:-}" == "--check" ]]; then
  if [[ ! -d "$ROOT/web/out" ]]; then
    echo "web/out does not exist — nothing has been built yet. Run this script with no arguments."
    exit 1
  fi
  changes="$(drift)"
  if [[ -z "$changes" ]]; then
    echo "In sync: app/public matches web/out."
    exit 0
  fi
  echo "DRIFT: app/public does not match web/out. $(printf '%s\n' "$changes" | wc -l) path(s) would change:"
  printf '%s\n' "$changes" | head -20
  echo "Run scripts/sync_web_build.sh to publish."
  exit 1
fi

if [[ "${1:-}" == "--rollback" ]]; then
  latest="$(find "$ROOT/app/.releases" -mindepth 1 -maxdepth 1 -type d -name 'previous-*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"
  if [[ -z "$latest" ]]; then
    echo "No rollback release is available."
    exit 1
  fi
  python3 "$ROOT/scripts/atomic_publish.py" "$latest" "$ROOT/app/public"
  echo "Rolled back atomically. The replaced release is now at $latest."
  exit 0
fi

echo "Building frontend…"
npm run build --prefix "$ROOT/web"

releases="$ROOT/app/.releases"
mkdir -p "$releases"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
staged="$(mktemp -d "$ROOT/app/.publish-${stamp}-XXXXXX")"
cleanup() {
  # An EXIT trap inherits its final command's status. The short-circuit form returned 1
  # after a successful publish deliberately moved the staging directory, making the whole
  # wrapper look failed even though `--check` immediately reported an exact match.
  if [[ -d "$staged" ]]; then
    rm -rf -- "$staged"
  fi
}
trap cleanup EXIT
rsync -a --delete "$ROOT/web/out/" "$staged/"
[[ -s "$staged/index.html" ]] || { echo "Staged build has no index.html"; exit 1; }
changes="$(rsync -ain --delete "$ROOT/web/out/" "$staged/" | grep -vE '^\.d[.t]+[[:space:]]' || true)"
[[ -z "$changes" ]] || { echo "Staged manifest does not match web/out"; printf '%s\n' "$changes"; exit 1; }

echo "Publishing verified tree atomically…"
if [[ -d "$ROOT/app/public" ]]; then
  python3 "$ROOT/scripts/atomic_publish.py" "$staged" "$ROOT/app/public"
  previous="$releases/previous-$stamp"
  mv -- "$staged" "$previous"
  staged="$ROOT/app/.publish-consumed"
  find "$releases" -mindepth 1 -maxdepth 1 -type d -name 'previous-*' -printf '%T@ %p\n' \
    | sort -nr | tail -n +4 | cut -d' ' -f2- | xargs -r rm -rf --
else
  mv -- "$staged" "$ROOT/app/public"
  staged="$ROOT/app/.publish-consumed"
fi

echo "Done. Restart app/server.mjs is NOT required (static files are read per request)."
echo "Verify at any time with: scripts/sync_web_build.sh --check"
