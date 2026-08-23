# Ponytail Audit — Cipher repo (2026-08-19)

Over-engineering / dead-code audit, run with the `ponytail-audit` methodology
(tags: `delete:` `stdlib:` `native:` `yagni:` `shrink:`). Scope is **complexity
and bloat only** — correctness, security, and performance are explicitly out of
scope. This report **lists findings only; it deletes nothing.**

Audited tree: `cipher-github/` (the active git repo, 1451 tracked files).
External tools used: `dietrichgebert/ponytail` (MIT) and
`tensortrade-org/tensortrade` (Apache-2.0) were cloned to `/tmp` for reference;
neither was vendored into the repo.

## Headline

`net: -~41K lines of Python, -~200 files, -~17MB of vendored data, -0 active deps possible.`

The repo carries a large, self-contained **dead research subsystem** that is no
longer reachable from any runtime entrypoint, plus vendored third-party data and
a duplicated nested project.

---

## Ranked findings

### 1. `delete:` Dead research-lab cluster in `core/` — 13,540 lines (13 files)

Thirteen `*_lab.py` modules are not imported by `app.py`, `scanner.py`, the
paper executor, or any systemd entrypoint. I traced every one transitively: they
are referenced **only** by each other, by one-off `scripts/run_*.py` runners, and
by their own test files — never by the live app.

- `capital_efficient_multi_stock_option_lab.py`
- `earnings_advanced_technique_lab.py`
- `earnings_defined_risk_lab.py`
- `earnings_robinhood_compatible_lab.py`
- `eod_best_strategy_lab.py`
- `eod_option_pattern_lab.py`
- `eod_pattern_lab.py`
- `historical_option_strategy_lab.py`
- `leveraged_etf_wheel_parameter_lab.py`
- `oi_niche_strategy_lab.py`
- `recent_call_combo_strategy_lab.py`
- `ticker_rejection_lab.py`
- `wave_lock_lab.py`

Replacement: nothing (delete). Keep a lab only if it is being actively iterated;
archive the rest to a research branch or an off-repo folder.

### 2. `delete:` One-off research runner scripts in `scripts/` — 20,361 lines (82 files)

82 of the 205 scripts match `run_*`, `audit_*`, `experiment_*`, `compare_*`,
`manage_*`, `register_*`, `recompute_*`, `import_*`. Only ~13 scripts are wired
into systemd. The rest are single-use research drivers that consume the dead labs
above.

Replacement: nothing, or move to an archived-research branch. Keep the ~13
systemd entrypoints and the capture/ops jobs listed in AGENTS.md.

### 3. `delete:` `EOD strategy/` — duplicated nested project, 6,007 lines (14 files)

Every `.py` in `EOD strategy/core/` and `EOD strategy/scripts/` already exists in
the main tree:

- `historical_options_download.py` → also `cipher-system/core/`
- `obsidian_eod.py` → also `cipher-system/core/`
- `equity_history_download.py` → also `cipher-system/core/`
- `run_obsidian_pine_ytd.py` → also `cipher-system/scripts/`
- `experiment_obsidian_eod.py` → also `cipher-system/scripts/`

Replacement: delete the `EOD strategy/` copy; keep the `cipher-system/` versions.

### 4. `delete:` `Stock data/external/Kronos/` — vendored third-party repo, 91 files / ~17MB

A checked-in copy of the Kronos project (LICENSE, README, examples, figures,
`yuce/` JSON+PNG). This is vendor/reference bulk, the same category AGENTS.md
already excludes from search/edit.

Replacement: remove from git and gitignore; keep a clone outside the repo if the
reference is still needed.

### 5. `delete:` Ad-hoc `check_*.py` / `send_*.py` at `cipher-system/` root — 878 lines (19 files)

Five near-duplicate families: `check_chains` ×3, `check_gex` ×4, `check_tradier`
×2, `send_alert` ×3, `send_health_alert` ×2, plus `check_health`, `check_streams`,
`check_time`, `send_live_chains_alert`. These are one-shot debug/alert scripts.

Replacement: delete; alerting already lives in `core/alerts.py` +
`scripts/*_discord*.py` + `scripts/alert_health_monitor.py`.

### 6. `delete:` Committed data exports at repo root — ~3.2MB

`tradier_option_chains_raw.json` (3.1MB), `tradier_option_chains_summary.csv`,
`cipher_session_log.csv`, `cipher_matrix_AAOI_20260720.csv`, `data/`.

Replacement: remove and gitignore; data belongs under `runtime/data/` (already a
symlinked target), not in git.

### 7. `delete:` `research/` — 29 agent-generated session/report `.md` files

`BACKTEST_RESEARCH_REPORT.md`, `COMPREHENSIVE_BACKTEST_ANALYSIS.md`,
`IMPROVEMENT_SESSION_COMPLETE.md`, etc. Ephemeral session logs that overlap.

Replacement: nothing (durable plans already live in `docs/`). Archive off-repo if
provenance matters.

### 8. `delete:` `cipher-system/access-obsidian-complete-audit/` — 126 files

61 JSON, 60 PNG, 1 PDF, 1 DOCX, 1 CSV, 2 MD. A clean-room comparison artifact
bundle.

Replacement: archive outside the repo, or keep only the single writeup that
documents the clean-room provenance decision.

### 9. `yagni:` `.venv-research-py312/` — 3.0GB on disk

Already gitignored (not in git), but it sits inside the workspace and dominates
the disk footprint.

Replacement: keep virtualenvs out of the repo directory (e.g. `~/.venvs/`), and
remove this one once its research deps are archived.

### 10. `shrink:` `core/app.py` — 166KB single-file API (~4K lines)

Not over-engineering per se, but the one structural risk: every panel endpoint
and provider route is one module. Flagging, not cutting.

Replacement: split along the existing `*_api.py` seams (`paper_portfolio_api.py`,
`prospective_fronttest_api.py` already exist) only when that file is next edited.

---

## Reachability signal (directional, not a delete order)

A BFS from the 17 systemd/runtime entrypoints (`app.py`, `run_autopilot.py`,
`run_fronttest_portfolios.py`, `run_prospective_fronttests.py`,
`run_structural_fib_*.py`, `run_market_research_agent.py`, the alert/backup jobs,
`mcp-server/remote_bridge.py`) could not reach **155 of 235 `core/` modules
(~64K LOC)**.

Treat that as an **upper bound**, not a definitive list: it under-counts
reachability for subsystems invoked outside the seeded entrypoints —
`research_platform/` (governance/provenance/prospective plane, driven by
`/usr/local/lib/cipher/run-governance-catalog.sh`), `gex_capture.py` (PowerShell
capture jobs), and the `paper_executor/*` scheduler modules. The **confirmed**
dead surface is findings 1–5 above; the rest needs a per-module confirmation pass
before anything is removed.

---

## tensortrade assessment (second repo)

**What it is:** an open-source RL trading framework — `gymnasium` env +
TensorFlow, action/reward schemes, data feeds, Apache-2.0, v1.0.5-dev, 109
modules / 12.7K LOC. Its own experiments are BTC/USD and show the core problem:
agent beats buy-and-hold at **0%** commission (+$239) but loses at **0.1%**
(-$650) — commission swamps the learned edge.

**Verdict — `yagni:` do not add it.**

- Cipher's in-flight ML work (`core/models/`, `core/training/`,
  `core/ai_synthesizer.py`) uses **lightgbm + sklearn + scipy** (tabular
  gradient-boosting for GEX-pinning / option-alpha) plus an LLM synthesis layer —
  not deep RL.
- tensortrade is single-instrument, not options-aware, and pulls in
  `tensorflow>=2.15.1` + `gymnasium` for a capability nobody has asked for.
- If an RL experiment is ever scoped, a `gymnasium`-only environment (no
  TensorFlow) is the lighter start; a `stable-baselines3`/PPO loop is smaller
  than this framework.

Both repos are clean-room-safe (MIT / Apache-2.0). Do **not** vendor either into
the tree — that would recreate the Kronos bloat finding above.

---

## Boundaries honored

- Read-only audit: **nothing deleted, nothing committed, no orders, no secret
  exposure.** Working tree still contains the prior uncommitted autopilot work.
- Correctness/security/perf findings are deliberately excluded and left to a
  normal review pass.
- The AGENTS.md "stale scaffold" (`api/`, `relay/`, `shared/`, `storage/`,
  `exposure_engine/`, `night_vision/`, `spyglass/`, `execution/`) is **already
  gone from disk** — nothing to cut there.
