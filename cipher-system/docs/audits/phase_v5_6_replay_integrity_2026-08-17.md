# Phase V5.6 Audit — scanner-to-Night-Vision replay integrity

Date: 2026-08-17 UTC  
Scope: frozen evidence artifacts and Scanner → Night Vision handoff

## Outcome

Replay loading now validates the requested SHA-256 snapshot ID, the frozen
evidence envelope identity, and the complete normalized matrix. New artifacts
store `matrix_sha256`; legacy artifacts remain replayable only after identity
recomputation and are labelled `legacy_unavailable` for the full-matrix
checksum. A changed strike/cell or mismatched evidence envelope is rejected.

The live replay smoke returned a frozen chart with the same snapshot ID as its
scanner artifact, `exposure_frozen=true`, `session_levels_captured=false`, and
`snapshot_identity=verified`. The sampled existing artifact was correctly
labelled legacy because it predates the checksum field.

## Verification

- Evidence snapshot/replay/tamper tests: 17 passed.
- All 104 existing local evidence artifacts recomputed to their stored snapshot
  IDs with zero mismatches.
- Live `/api/night-vision-replay` smoke: passed; scanner and chart IDs matched.
- Frontend typecheck: passed.
- Frontend Node suite: 54 passed.

## Carry-forward

Session-level bars, expected-move/catalyst/spread evidence, and measured scan
latency remain separate data products. They must be captured and labelled,
not inferred from an exposure snapshot.
