# Bounded Build/Test Healing Loop

Cipher can continuously validate source changes and mechanically heal a narrow
class of generated-state failures. The loop is deliberately fail-closed: it
never rewrites application or research logic on its own.

## Validation sequence

Every cycle runs, in order:

1. `git diff --check`
2. Python bytecode compilation for `core/`, `scripts/`, and `tests/`
3. Node syntax validation for the server, launcher, and browser application
4. The complete `pytest -q` repository suite

The loop stops at the first failed step, records bounded diagnostic output, and
starts the healing policy.

## Allowed healing

The loop may only:

- retry the failed deterministic validation command up to three times;
- remove generated `__pycache__`, `.pytest_cache`, `.pyc`, and `.pyo` material;
- rerun the complete validation suite;
- write immutable repair incidents and build-status artifacts.

It may not:

- edit tracked source code;
- install, upgrade, or remove packages;
- alter market data or research evidence;
- change cohorts, origins, symbols, parameters, gates, or thresholds;
- change promotion state;
- run paper or live trading;
- commit, reset, checkout, merge, push, or otherwise mutate Git history.

A source snapshot is taken before validation. If a test or repair changes a
tracked source/configuration file, the cycle is marked
`boundary_violation_blocked` and stops without trying to hide or revert the
change.

## Outcomes

- `passed`: the initial validation suite passed.
- `healed_passed`: a retry or generated-cache cleanup was required, after which
  the complete suite passed.
- `escalated_blocked`: the failure survived the bounded attempts and requires a
  human or coding agent to inspect the failure artifact.
- `boundary_violation_blocked`: source changed during validation or healing.

The watcher records the failed source fingerprint and does not repeatedly rerun
the same unresolved failure. It waits until source files change.

## Commands

Run one cycle:

```bash
.venv-research-py312/bin/python cipher-system/scripts/run_build_healing_loop.py --once
```

Start the source-change watcher and validate immediately:

```bash
.venv-research-py312/bin/python cipher-system/scripts/manage_build_healing_loop.py start \
  --run-on-start --interval-seconds 60
```

Inspect or stop it:

```bash
.venv-research-py312/bin/python cipher-system/scripts/manage_build_healing_loop.py status
.venv-research-py312/bin/python cipher-system/scripts/manage_build_healing_loop.py stop
```

## Artifacts

- Latest cycle:
  `data/governance/build_healing/latest_build_healing_run.json`
- Timestamped cycles:
  `data/governance/build_healing/build_healing_run_*.json`
- Watcher status:
  `data/governance/build_healing/build_healing_loop_status.json`
- Immutable repair incidents:
  `data/repair_incidents/repair_*.json`
- Watcher log:
  `logs/build_healing_loop.log`

## Operational limitation

This is a deterministic build/test and mechanical-repair loop, not an
unattended code-writing agent. Real logic defects are intentionally escalated.
A coding agent can then use the recorded failing command, output tail, source
fingerprint, and Git state to make a reviewed source change, after which the
watcher automatically validates the new fingerprint.
