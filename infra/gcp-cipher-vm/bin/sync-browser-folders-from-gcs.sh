#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="project-eec91607-77a2-4be6-837"
SOURCE="gs://cipher-browser-ingest-aarav/browser-ingest/windows-mirror/device-windows"
DESTINATION="/home/aarav/Aarav/cipher/cipher-system/data/browser_ingest/raw_windows/device-windows"
TIMEZONE="America/New_York"
START_HHMM=0925
END_HHMM=1620

weekday="$(TZ="$TIMEZONE" date +%u)"
current_hhmm="$(TZ="$TIMEZONE" date +%H%M)"

# Weekdays only, with a pre-open start and post-close delivery buffer.
if (( 10#$weekday > 5 || 10#$current_hhmm < 10#$START_HHMM || 10#$current_hhmm > 10#$END_HHMM )); then
  exit 0
fi

mkdir -p "$DESTINATION"
exec 9>"$DESTINATION/.sync.lock"
if ! flock -n 9; then
  exit 0
fi

gcloud storage rsync \
  "$SOURCE" \
  "$DESTINATION" \
  --recursive \
  --project="$PROJECT" \
  --quiet

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$DESTINATION/.last-successful-sync"
