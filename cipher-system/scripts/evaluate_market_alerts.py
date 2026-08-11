#!/usr/bin/env python3
"""Evaluate stored market alert rules server-side and push crossings to Telegram.

`core/alerts.py` stores rules and its docstring says they are "evaluated in the authenticated
browser". That makes an alert useless for its only purpose: it fires only while someone is
already looking at the screen. The rules, the delivery channel
(`scripts/hermes_delivery.send_hermes_message`, already carrying the data-health and cluster
alerts) and the timer pattern all existed separately; this connects them.

Two properties the implementation turns on:

* **Edge-triggered, not level-triggered.** "Notify when SPY crosses 780" must fire on the
  crossing, not every run for as long as the price stays above. State is persisted per rule
  and a rule re-arms once it returns to clear, so a genuine second crossing notifies again.

* **Never fires on stale data.** A quote older than the freshness bound is reported as
  unknown and leaves rule state untouched. Firing "SPY crossed 780" from yesterday's close is
  worse than not firing, and a research tool that does it once cannot be trusted after.

Read-only throughout: quotes come from the local core service, which owns the credentials.
The message states an observation and never suggests an action.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "core", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import alerts as alert_store  # noqa: E402
from hermes_delivery import send_hermes_message  # noqa: E402

DEFAULT_STATE = ROOT / "data" / "alerts" / "market_alert_state.json"
CORE_URL = "http://127.0.0.1:8282"
# A quote older than this is not evidence about now. 10 minutes tolerates a slow poll and a
# thin book without ever letting a stale print trigger a crossing.
MAX_QUOTE_AGE_SECONDS = 600

# Each kind maps to the quote field it reads and the comparison it makes, so adding a kind is
# a table entry rather than another branch.
COMPARISONS: dict[str, tuple[str, str]] = {
    "price_above": ("price_context", "above"),
    "price_below": ("price_context", "below"),
    "day_change_above": ("day_change_pct", "above"),
    "day_change_below": ("day_change_pct", "below"),
}
UNITS = {"price_context": "", "day_change_pct": "%"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        # A corrupt state file must not stop evaluation; the cost is one duplicate
        # notification, which is far better than silently stopping all alerts.
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def fetch_quote(ticker: str, *, base: str = CORE_URL, timeout: float = 15.0) -> dict[str, Any]:
    """Read-only quote from the local core, which holds the provider credentials."""
    url = f"{base}/api/quote?ticker={urllib.parse.quote(ticker)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def quote_age_seconds(quote: dict[str, Any], *, now: datetime | None = None) -> float | None:
    raw = quote.get("as_of")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return ((now or utcnow()) - stamp).total_seconds()


def evaluate_rule(rule: dict[str, Any], quote: dict[str, Any], *, now: datetime | None = None,
                  max_age_seconds: float = MAX_QUOTE_AGE_SECONDS) -> dict[str, Any]:
    """Decide whether a rule is triggered, clear, or unknown.

    "unknown" is a first-class outcome, not an error path: a missing quote, an unparsable
    timestamp, a stale print or an absent field all mean the rule cannot be judged right now,
    and the caller must leave its stored state alone rather than treating unknown as clear
    (which would silently re-arm it and produce a spurious crossing on the next good quote).
    """
    kind = rule.get("kind")
    mapping = COMPARISONS.get(str(kind))
    if mapping is None:
        return {"status": "unknown", "reason": f"unsupported kind {kind!r}"}
    field, direction = mapping

    if quote.get("error"):
        return {"status": "unknown", "reason": quote["error"]}
    age = quote_age_seconds(quote, now=now)
    if age is None:
        return {"status": "unknown", "reason": "quote carries no usable as_of"}
    if age > max_age_seconds:
        return {"status": "unknown", "reason": f"quote is {age:.0f}s old (limit {max_age_seconds:.0f}s)"}

    observed = quote.get(field)
    if not isinstance(observed, (int, float)):
        return {"status": "unknown", "reason": f"quote has no numeric {field}"}

    threshold = float(rule["threshold"])
    crossed = observed > threshold if direction == "above" else observed < threshold
    return {
        "status": "triggered" if crossed else "clear",
        "observed": float(observed),
        "field": field,
        "direction": direction,
        "threshold": threshold,
        "age_seconds": age,
    }


def describe(rule: dict[str, Any], outcome: dict[str, Any]) -> str:
    unit = UNITS.get(outcome["field"], "")
    return (
        f"{rule['ticker']} {outcome['field']} {outcome['observed']:.4g}{unit} is "
        f"{outcome['direction']} your {outcome['threshold']:.4g}{unit} threshold"
    )


def run(*, state_path: Path, target: str, dry_run: bool, db_path: Path,
        now: datetime | None = None) -> dict[str, Any]:
    state = load_state(state_path)
    rules = [r for r in alert_store.list_rules(db_path)["rules"] if r.get("enabled")]
    quotes: dict[str, dict[str, Any]] = {}
    crossings: list[str] = []
    report: list[dict[str, Any]] = []

    for rule in rules:
        ticker = rule["ticker"]
        if ticker not in quotes:
            quotes[ticker] = fetch_quote(ticker)
        outcome = evaluate_rule(rule, quotes[ticker], now=now)
        previous = (state.get(rule["id"]) or {}).get("status")
        entry = {"id": rule["id"], "ticker": ticker, "kind": rule["kind"],
                 "previous": previous, **outcome}

        if outcome["status"] == "unknown":
            # Deliberately leaves stored state untouched: see evaluate_rule's docstring.
            entry["notified"] = False
            report.append(entry)
            continue

        # The edge: notify only on a transition into triggered. A rule with no prior state is
        # treated as a transition, so a rule created while already above its threshold does
        # notify once rather than staying silent until it happens to cross back and forth.
        is_edge = outcome["status"] == "triggered" and previous != "triggered"
        entry["notified"] = is_edge
        if is_edge:
            crossings.append(describe(rule, outcome))
        state[rule["id"]] = {"status": outcome["status"], "checked_at": utcnow().isoformat(timespec="seconds")}
        report.append(entry)

    # Prune state for rules that no longer exist, or the file grows without bound.
    live_ids = {rule["id"] for rule in rules}
    for stale in [key for key in state if key not in live_ids]:
        state.pop(stale)

    delivered = None
    if crossings and not dry_run:
        message = "\n".join([
            "Cipher market alert",
            f"Checked: {utcnow().isoformat(timespec='seconds')}",
            "",
            *(f"- {line}" for line in crossings),
            "",
            "Read-only observation. Cipher places no orders and this is not advice.",
        ])
        delivered = send_hermes_message(message, target=target)

    if not dry_run:
        save_state(state_path, state)

    return {
        "checked": len(rules),
        "crossings": crossings,
        "notified": bool(crossings) and not dry_run,
        "delivery_status": delivered,
        "unknown": sum(1 for row in report if row["status"] == "unknown"),
        "rules": report,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--db", type=Path, default=alert_store.DEFAULT_DB)
    parser.add_argument("--target", default="telegram")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate and print without sending or persisting state.")
    args = parser.parse_args()
    result = run(state_path=args.state, target=args.target, dry_run=args.dry_run, db_path=args.db)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
