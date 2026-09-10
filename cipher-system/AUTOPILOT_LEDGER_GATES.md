# Autopilot ledger implementation gates

- [x] Concurrent entry rechecks position, session and cash limits under BEGIN IMMEDIATE.
- [x] Entry/exit orders, positions, events and daily state commit or roll back together.
- [x] Concurrent close and restart produce one close and one exit order.
- [x] Every realized loss counts toward the session cutoff.
- [x] New York midnight and both daylight-saving transitions use correct UTC boundaries.
- [x] Dataclass payloads remain structured JSON; invalid economics fail before mutation.

Check: `/home/aarav/.venvs/cipher/bin/python -m pytest tests/test_autopilot_ledger_v2.py -q` — 12 passed.
No deployed ledger opened or modified. Existing schema retained without migration.
