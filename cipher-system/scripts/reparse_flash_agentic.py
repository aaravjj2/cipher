#!/usr/bin/env python3
"""Repair captured Flash Agentic rows whose fields came from the wrong card.

capture_accessobsidian_scans.py ended a card on a known status word, so whenever
the next card opened on one it did not know about ("DONE · 282s"), the two ran
together. The field scrape then pulled SPOT/PIVOT/TARGET from the second ticker
while keeping the first ticker's name — an MU row carrying TSLA's prices.

26 of 432 captured rows are affected. They are recoverable without re-capturing
because `raw_card` preserves the original text: re-splitting it at the second
"$TICKER" and re-reading the labelled fields restores the correct values.

This matters beyond tidiness. Any study correlating a card's `edge` against its
own geometry was, on those rows, correlating one card's edge against another
card's prices — noise indistinguishable from "no relationship exists".

Usage:
  python3 scripts/reparse_flash_agentic.py --dry-run
  python3 scripts/reparse_flash_agentic.py
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANS = ROOT / "data" / "accessobsidian_scans"

TICKER_RE = re.compile(r"^\$[A-Z][A-Z0-9.]{0,6}$")
STATUS_RE = re.compile(r"^(DONE|ACTIVE|ARMING|TRIGGERED|COMPLETED)(\s*·\s*\d+s)?$", re.I)

LABELS = {
    "SPOT": "spot",
    "TRIGGER": "trigger",
    "PIVOT": "pivot",
    "FIRST TARGET": "first_target",
    "TARGET": "first_target",
    "PUSH TARGET": "push_target",
    "STRETCH": "stretch",
    "INVALIDATION": "invalidation",
}


def parse_float(text):
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(text)))
    except (TypeError, ValueError):
        return None


def first_card(raw: str) -> list[str] | None:
    """Tokens of the FIRST card only, or None when the row was already clean."""
    parts = [p.strip() for p in str(raw).split(" | ")]
    ticker_positions = [i for i, p in enumerate(parts) if TICKER_RE.fullmatch(p)]
    if len(ticker_positions) < 2:
        return None
    cut = ticker_positions[1]
    tokens = parts[:cut]
    while tokens and STATUS_RE.fullmatch(tokens[-1]):
        tokens.pop()
    return tokens


def fields_from(tokens: list[str]) -> dict:
    """Re-read the labelled numeric fields from one card's tokens."""
    out = {}
    for i, token in enumerate(tokens):
        key = LABELS.get(token.upper())
        if key and i + 1 < len(tokens) and key not in out:
            value = parse_float(tokens[i + 1])
            if value is not None:
                out[key] = value
    m = re.search(r"Edge\s+(\d+(?:\.\d+)?)", " | ".join(tokens))
    if m:
        out["edge"] = parse_float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)/100", " | ".join(tokens))
    if m:
        out["score"] = parse_float(m.group(1))
    m = re.search(r"(\d+)%\s+to target", " | ".join(tokens))
    if m:
        out["target_progress"] = f"{m.group(1)}% to target"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(SCANS.glob("**/flash_agentic.json"))
    touched_files = repaired_rows = 0
    samples = []

    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        rows = data if isinstance(data, list) else (data.get("rows") or data.get("items") or [])
        if not isinstance(rows, list):
            continue

        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            tokens = first_card(row.get("raw_card") or "")
            if tokens is None:
                continue
            fixed = fields_from(tokens)
            diffs = {k: (row.get(k), v) for k, v in fixed.items() if row.get(k) != v}
            if diffs and len(samples) < 6:
                samples.append((path.parent.name, row.get("ticker"), diffs))
            row.update(fixed)
            # Trim the raw text even when no field changed. A row whose first card
            # happened to win the scrape is still storing the next card's text, and
            # raw_card is what later analysis re-reads.
            trimmed = " | ".join(tokens)
            if diffs or row.get("raw_card") != trimmed:
                row["raw_card"] = trimmed
                row["reparsed"] = True
                repaired_rows += 1
                changed = True

        if changed:
            touched_files += 1
            if not args.dry_run:
                path.write_text(json.dumps(data, indent=2))

    print(f"scanned {len(files)} captures")
    print(f"{'would repair' if args.dry_run else 'repaired'} {repaired_rows} rows "
          f"across {touched_files} files")
    for capture, ticker, diffs in samples:
        pretty = ", ".join(f"{k}: {old} -> {new}" for k, (old, new) in diffs.items())
        print(f"  {capture} {ticker}: {pretty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
