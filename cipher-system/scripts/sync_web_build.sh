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
# web/out and app/public are both pure build output — no hand-authored file lives in
# either — so --delete is safe and removes the stale content-hashed chunks that would
# otherwise accumulate on every build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Building frontend…"
npm run build --prefix "$ROOT/web"

echo "Publishing web/out -> app/public…"
rsync -a --delete "$ROOT/web/out/" "$ROOT/app/public/"

echo "Done. Restart app/server.mjs is NOT required (static files are read per request)."
