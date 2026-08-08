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
    """Tokens of the FIRST card only, or None when there is no card text.

    Returns tokens for single-card rows too, not just merged ones. The merge repair
    and the field backfill share this path: a row can be perfectly well split and
    still be missing runway_clarity_pct, because that was a parser-mapping bug
    rather than a card-boundary bug.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(" | ")]
    ticker_positions = [i for i, p in enumerate(parts) if TICKER_RE.fullmatch(p)]
    tokens = parts[:ticker_positions[1]] if len(ticker_positions) >= 2 else parts
    while tokens and STATUS_RE.fullmatch(tokens[-1]):
        tokens.pop()
    return tokens or None


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
    joined = " | ".join(tokens)
    m = re.search(r"(\d+)%\s+to target", joined)
    if m:
        out["target_progress"] = f"{m.group(1)}% to target"

    # RUNWAY prints as "clean (100% clear)" or "wall 980 (64% clear, 4 levels)".
    # The capture parser only mapped the label "RUNWAY CLARITY", which the agentic
    # card never emits, so runway_clarity_pct was null on all 432 archived rows —
    # and it is the label side of runway_clarity_norm, the largest coefficient in
    # the fitted flash head. 431 of 432 are recoverable from raw_card.
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*clear", joined, re.IGNORECASE)
    if pct:
        out["runway_clarity_pct"] = parse_float(pct.group(1))
    wall = re.search(r"wall\s+([\d.]+)", joined, re.IGNORECASE)
    if wall:
        out["runway_wall_strike"] = parse_float(wall.group(1))
    levels = re.search(r"(\d+)\s+levels?", joined, re.IGNORECASE)
    if levels:
        out["runway_levels"] = parse_float(levels.group(1))

    # PIVOT prints as "1000 (ceiling, nearing)" — the kind and proximity are
    # separate signals from the strike itself.
    piv = re.search(r"PIVOT \| [\d.]+ \((\w+),\s*(\w+)\)", joined, re.IGNORECASE)
    if piv:
        out["pivot_kind"] = piv.group(1).lower()
        out["trigger_proximity"] = piv.group(2).lower()
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
