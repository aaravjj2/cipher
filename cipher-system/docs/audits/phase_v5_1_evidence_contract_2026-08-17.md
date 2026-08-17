# Phase V5.1 Audit — Evidence and Signal Contract

Date: 2026-08-17
Scope: first implementation slice of the canonical product and hackathon roadmap.

## Outcome

Cipher now has one deterministic, read-only evidence/signal identity contract that can travel with market observations, scanner candidates, Night Vision payloads, historical trades, prospective fronttests, paper-portfolio signals, and autopilot confirmation cards.

The implementation is backward-compatible: existing payload fields remain in place, while new records receive `evidence_contract` and, where a decision exists, `signal_record`. No broker client, order endpoint, or live-execution capability was added.

## Implemented boundaries

- `core/evidence_contract.py`: timezone-normalized, content-addressed `EvidenceSnapshot` and `SignalRecord` records.
- `core/scanner.py`: attaches evidence contracts to normal and no-cluster scan results.
- `core/app.py`: attaches contracts to Night Vision matrix and replay responses.
- `core/strategy_backtest.py`: records the evidence snapshot and canonical signal identity on generated trades.
- `core/prospective_fronttests.py` and `core/prospective_fronttest_api.py`: persist and expose canonical signal identity.
- `core/paper_portfolio_api.py`: exposes signal/evidence identity for paper-monitoring auditability.
- `core/autopilot.py`: carries premarket evidence into confirmation cards and creates an accepted signal record on confirmation.
- `web/src/components/panels/PaperPortfolios.tsx`: displays canonical signal and evidence IDs in recent signals.
- `tests/test_evidence_contract.py`: deterministic IDs, validation, decision boundaries, and legacy compatibility coverage.

## Verification

| Check | Result |
|---|---|
| Focused evidence tests | 43 passed |
| Full active Python suite | 959 passed, 2 skipped |
| Python compileall | passed |
| Node syntax checks | passed (`server.mjs`, `launcher.mjs`) |
| Frontend lint/typecheck/build | passed |
| Atomic web publish | passed |
| Core/web services | active after restart |
| Health API | HTTP 200 |
| Paper portfolios API | HTTP 200 |
| Autopilot status API | HTTP 200 |
| Prospective fronttests API | HTTP 200 |

## Runtime observation

The Night Vision live request can exceed the smoke-test timeout when Alpaca matrix data is cold or unavailable. The current UI fails closed with an explicit unavailable/error state; it does not fabricate a chart or treat missing gamma/open interest as zero. This remains a P0 operational follow-up: make provider timeout/cached replay behavior explicit and fast while preserving the data-quality caveat.

Existing historical paper/prospective rows created before this change naturally have no canonical contract. Newly emitted signals and trades will carry the IDs; backfilling old rows should be a separate migration with provenance, not inferred timestamps.

## Safety audit

- `read_only=true` and `execution_capability=false` are part of both contract types.
- No `/v2/orders`, order submission helper, broker trading client, or scheduled live-order runner was introduced.
- Contracts preserve missing-data reasons and coverage states; they do not convert unknown values into zero.
- IDs are deterministic hashes of normalized content and contain no credentials.

## Next implementation slice

1. Add a bounded Night Vision provider path with cache/replay and an explicit unavailable reason.
2. Add contract parity checks to scanner/backtest/prospective/paper/autopilot fixtures.
3. Add a user-facing evidence drawer and a signal timeline linking every displayed setup to its source snapshot.
4. Continue the roadmap's options-flow freshness and research-agent slices only after the runtime data-health gate is green.
