#!/usr/bin/env python3
"""End-of-session report for the Options Alpha agent.

PLAYBOOK.md's final step, automated: reads the decision log, runs chain
integrity on every decision seen today, appends the quality statistics, and
prints one plain-text block suitable for pasting into Discord or a journal.
Refusal counts are successes here — the report's job is truth, not wins.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_decision_log import DEFAULT_LOG, chain, positions, tail_rows  # noqa: E402


def _utc_date(row: dict) -> str:
    return str(row.get("ts") or "")[:10]


def build_report(log_path: Path, *, today: str | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date().isoformat()
    rows = [
        row for row in tail_rows(log_path, limit=None)
        if isinstance(row, dict) and _utc_date(row) == today and row.get("event")
    ]
    decisions: dict[str, dict] = {}
    for row in rows:
        did = str(row.get("decision_id"))
        slot = decisions.setdefault(did, {"events": [], "ticker": row.get("ticker")})
        slot["events"].append(row.get("event"))

    outcomes: dict[str, int] = {}
    chains = []
    for decision_id, info in sorted(decisions.items()):
        result = chain(log_path, decision_id)
        chains.append(result)
        terminal = [e for e in info["events"] if e in ("FILLED", "UNFILLED", "BLOCKED")]
        key = terminal[-1] if terminal else "PENDING"
        outcomes[key] = outcomes.get(key, 0) + 1
    anomalies = [c for c in chains if c["anomalies"]]

    book = positions(log_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_date_utc": today,
        "decisions": len(decisions),
        "outcomes": outcomes,
        "open_positions": book["open_positions"],
        "closed_round_turns": book["closed_round_turns"],
        "blocked_reasons": sorted({
            str(row.get("reason")) for row in rows
            if row.get("event") == "BLOCKED" and row.get("reason")
        }),
        "chain_anomalies": [{"decision_id": c["decision_id"], "anomalies": c["anomalies"]}
                            for c in anomalies],
        "paper_only": True,
    }


def render(report: dict) -> str:
    lines = [
        "**Options Alpha — session report**",
        f"`{report['session_date_utc']}` · {report['decisions']} decision(s)",
    ]
    if report["decisions"]:
        pretty = " · ".join(f"{k}: {v}" for k, v in sorted(report["outcomes"].items()))
        lines.append(f"Outcomes — {pretty}")
    else:
        lines.append("No decisions this session (gates saw nothing to evaluate).")
    if report["blocked_reasons"]:
        lines.append("Blocked reasons:")
        lines.extend(f"- `{r}`" for r in report["blocked_reasons"])
    if report["open_positions"]:
        lines.append("Open positions:")
        lines.extend(f"- `{p_['contract_symbol']}` x{p_['quantity']} @ {p_['avg_open_price']}"
                     for p_ in report["open_positions"])
    if report["chain_anomalies"]:
        lines.append("**Chain anomalies (must be investigated):**")
        lines.extend(f"- `{c['decision_id']}`: {', '.join(c['anomalies'])}"
                     for c in report["chain_anomalies"])
    lines.append("_Paper only. Sample-size caveats apply to any performance reading._")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--date", help="UTC date to summarize (default: today)")
    args = parser.parse_args(argv)
    report = build_report(args.log, today=args.date)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
