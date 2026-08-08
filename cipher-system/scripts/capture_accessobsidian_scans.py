#!/usr/bin/env python3
"""Capture AccessObsidian Setup Scanner outputs through Kimi WebBridge.

This records only the visible UI result text. It does not inspect app internals
or private network payloads.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BRIDGE_URL = "http://127.0.0.1:10086/command"
APP_URL = "https://www.accessobsidian.com/app#CI"
OUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "accessobsidian_scans"
LEDGER_PATH = OUT_ROOT / "quad_ledger.json"
LOCK_PATH = OUT_ROOT / "capture_accessobsidian_scans.lock"


@dataclass(frozen=True)
class ScanMode:
    key: str
    button: str


SCAN_MODES = {
    "cluster": ScanMode("cluster", "Cluster scan"),
    "liq": ScanMode("liq", "Liq scan"),
    "cipher_model": ScanMode("cipher_model", "Cipher Model Scan"),
    "flash": ScanMode("flash", "Flash BETA"),
    "flash_index": ScanMode("flash_index", "Flash Index BETA"),
    "flash_agentic": ScanMode("flash_agentic", "Flash Agentic BETA"),
}


def command(action: str, args: dict[str, Any], session: str, timeout: int = 60) -> dict[str, Any]:
    payload = json.dumps({"action": action, "args": args, "session": session}).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"{action} failed in {session}: {data}")
    return data["data"]


def eval_js(session: str, code: str, timeout: int = 60) -> Any:
    return command("evaluate", {"code": code}, session, timeout=timeout).get("value")


def close_session(session: str) -> None:
    try:
        command("close_session", {}, session, timeout=30)
    except Exception as exc:  # noqa: BLE001 - best-effort browser cleanup.
        print(f"Warning: could not close WebBridge session {session}: {exc}", file=sys.stderr)


def main_text(session: str) -> str:
    code = "(() => { const main=document.querySelector('main')||document.body; return main.innerText||''; })()"
    return str(eval_js(session, code, timeout=60) or "")


def body_text(session: str) -> str:
    code = "(() => document.body?.innerText || '')()"
    return str(eval_js(session, code, timeout=60) or "")


def click_button_by_text(session: str, label: str) -> None:
    escaped = json.dumps(label)
    code = f"""(() => {{
      const wanted = {escaped};
      const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      const buttons = [...document.querySelectorAll('button')];
      const btn = buttons.find((b) => norm(b.innerText) === wanted)
        || buttons.find((b) => norm(b.innerText).includes(wanted));
      if (!btn) return 'missing:' + wanted;
      btn.click();
      return 'clicked:' + wanted;
    }})()"""
    result = eval_js(session, code, timeout=60)
    if not str(result).startswith("clicked:"):
        raise RuntimeError(f"Could not click {label!r} in {session}: {result}")


def wait_for_setup_scanner(session: str, timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        text = main_text(session)
        if "Setup Scanner" in text and "Cluster scan" in text and "Liq scan" in text:
            return
        time.sleep(1)
    raise TimeoutError(f"Setup Scanner controls did not render in {session}")


def wait_for_app_shell(session: str, timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        text = body_text(session)
        if "Strike Matrix" in text or "Setup Scanner" in text:
            return
        time.sleep(1)
    raise TimeoutError(f"App shell did not render in {session}")


def wait_for_scan_results(session: str, timeout_seconds: int = 900, mode: str = "") -> str:
    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        text = main_text(session)
        last = text
        if "Scanning the universe" not in text:
            if re.search(r"(?m)^#1$", text):
                return text
            if mode in {"flash", "flash_index", "flash_agentic"} and "CIPHER READ" in text and re.search(r"(?m)^\$[A-Z][A-Z0-9.]{0,6}$", text):
                return text
        time.sleep(10)
    raise TimeoutError(f"Scan did not finish in {session}; last text tail:\n{last[-1200:]}")


def parse_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "").rstrip("%"))
    except (AttributeError, ValueError):
        return None


def parse_levels(lines: list[str], start: int) -> tuple[list[dict[str, float]], int]:
    levels: list[dict[str, float]] = []
    idx = start
    while idx < len(lines) and not re.fullmatch(r"#\d+", lines[idx]):
        if "·" in lines[idx]:
            weight, strike = [part.strip() for part in lines[idx].split("·", 1)]
            weight_float = parse_float(weight)
            strike_float = parse_float(strike)
            if weight_float is not None and strike_float is not None:
                levels.append({"weight": weight_float, "strike": strike_float})
        idx += 1
    return levels, idx


def parse_cluster(text: str) -> list[dict[str, Any]]:
    lines = [line.strip().replace("\u00a0", " ") for line in text.splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        if re.fullmatch(r"#\d+", lines[idx]) and idx + 10 < len(lines):
            row = {
                "rank": int(lines[idx][1:]),
                "ticker": lines[idx + 1].lstrip("$"),
                "setup": lines[idx + 2],
                "dte": lines[idx + 3],
                "spot": None,
                "cluster_target": None,
                "strength": None,
                "levels": [],
            }
            j = idx + 4
            while j < len(lines) and not re.fullmatch(r"#\d+", lines[j]):
                if lines[j] == "SPOT" and j + 1 < len(lines):
                    row["spot"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                if lines[j] == "CLUSTER TARGET" and j + 1 < len(lines):
                    row["cluster_target"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                if lines[j] == "STRENGTH" and j + 1 < len(lines):
                    row["strength"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                j += 1
            row["levels"], idx = parse_levels(lines, idx + 10)
            rows.append(row)
        else:
            idx += 1
    return rows


def parse_liq(text: str) -> list[dict[str, Any]]:
    lines = [line.strip().replace("\u00a0", " ") for line in text.splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        if re.fullmatch(r"#\d+", lines[idx]) and idx + 10 < len(lines):
            row = {
                "rank": int(lines[idx][1:]),
                "ticker": lines[idx + 1].lstrip("$"),
                "setup": lines[idx + 2],
                "dte": lines[idx + 3],
                "spot": None,
                "target": None,
                "target_pct_away": None,
                "runway_clarity_pct": None,
                "levels": [],
            }
            j = idx + 4
            while j < len(lines) and not re.fullmatch(r"#\d+", lines[j]):
                if lines[j] == "SPOT" and j + 1 < len(lines):
                    row["spot"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                if lines[j] == "TARGET" and j + 1 < len(lines):
                    target_match = re.match(r"([\d.,]+) \(([-\d.]+)% away\)", lines[j + 1])
                    if target_match:
                        row["target"] = parse_float(target_match.group(1))
                        row["target_pct_away"] = parse_float(target_match.group(2))
                    else:
                        row["target"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                if lines[j] == "RUNWAY CLARITY" and j + 1 < len(lines):
                    row["runway_clarity_pct"] = parse_float(lines[j + 1])
                    j += 2
                    continue
                j += 1
            row["levels"], idx = parse_levels(lines, idx + 10)
            rows.append(row)
        else:
            idx += 1
    return rows


def parse_cipher_model(text: str) -> list[dict[str, Any]]:
    lines = [line.strip().replace("\u00a0", " ") for line in text.splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        if re.fullmatch(r"#\d+", lines[idx]) and idx + 3 < len(lines):
            row = {
                "rank": int(lines[idx][1:]),
                "ticker": lines[idx + 1].lstrip("$"),
                "bias": lines[idx + 2],
                "score": None,
                "major_supports": [],
                "major_resistances": [],
                "pull_target": None,
                "vacuum_targets": [],
                "cipher_read": "",
            }
            j = idx + 3
            if j < len(lines) and re.fullmatch(r"\d+(?:\.\d+)?/100", lines[j]):
                row["score"] = parse_float(lines[j].split("/", 1)[0])
                j += 1
            read_lines: list[str] = []
            while j < len(lines) and not re.fullmatch(r"#\d+", lines[j]):
                if lines[j] == "MAJOR SUPPORTS" and j + 1 < len(lines):
                    row["major_supports"] = [part.strip() for part in lines[j + 1].split(",") if part.strip()]
                    j += 2
                    continue
                if lines[j] == "MAJOR RESISTANCES" and j + 1 < len(lines):
                    row["major_resistances"] = [part.strip() for part in lines[j + 1].split(",") if part.strip()]
                    j += 2
                    continue
                if lines[j] == "PULL TARGET" and j + 1 < len(lines):
                    row["pull_target"] = lines[j + 1]
                    j += 2
                    continue
                if lines[j] == "VACUUM TARGETS" and j + 1 < len(lines):
                    row["vacuum_targets"] = [part.strip() for part in lines[j + 1].split(",") if part.strip()]
                    j += 2
                    continue
                if lines[j] == "CIPHER READ":
                    j += 1
                    while j < len(lines) and not re.fullmatch(r"#\d+", lines[j]):
                        read_lines.append(lines[j])
                        j += 1
                    continue
                j += 1
            row["cipher_read"] = " ".join(read_lines)
            rows.append(row)
            idx = j
        else:
            idx += 1
    return rows


def parse_flash(text: str) -> list[dict[str, Any]]:
    lines = [line.strip().replace("\u00a0", " ") for line in text.splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    idx = 0
    regime_labels = {"TREND", "PIN", "MIXED", "VACUUM", "CHOP", "SQUEEZE"}
    while idx < len(lines):
        ranked = re.fullmatch(r"#\d+", lines[idx])
        status_start = (
            lines[idx].upper() in {"ACTIVE", "ARMING", "TRIGGERED", "COMPLETED"}
            and idx + 3 < len(lines)
            and re.fullmatch(r"\$[A-Z][A-Z0-9.]{0,6}", lines[idx + 1])
            and lines[idx + 2].upper() in {"BULLISH", "BEARISH", "NEUTRAL"}
        )
        if not ranked and not status_start:
            idx += 1
            continue
        row_lines = []
        j = idx + (1 if ranked else 0)
        if status_start:
            row_lines.append(lines[idx])
            j = idx + 1
        seen_ticker = False
        while j < len(lines):
            if j != idx and re.fullmatch(r"#\d+", lines[j]):
                break
            if j != idx and lines[j].upper() in {"ACTIVE", "ARMING", "TRIGGERED", "COMPLETED"} and j + 3 < len(lines) and re.fullmatch(r"\$[A-Z][A-Z0-9.]{0,6}", lines[j + 1]):
                break
            # A second "$TICKER" means the next card has started. The status-word
            # check above misses it whenever the following card opens on a state
            # this parser did not know about — "DONE · 282s" being the one that bit:
            # 26 of 432 captured rows swallowed the next card, and the field scrape
            # below then took SPOT/PIVOT/TARGET from the wrong ticker while keeping
            # the first one's name. Every card carries exactly one $TICKER, so that
            # is the reliable boundary.
            if re.fullmatch(r"\$[A-Z][A-Z0-9.]{0,6}", lines[j]):
                if seen_ticker:
                    break
                seen_ticker = True
            row_lines.append(lines[j])
            j += 1
        # Drop a trailing status marker belonging to the next card ("DONE · 282s").
        while row_lines and re.fullmatch(
            r"(DONE|ACTIVE|ARMING|TRIGGERED|COMPLETED)(\s*·\s*\d+s)?", row_lines[-1], re.I
        ):
            row_lines.pop()
        raw = " | ".join(row_lines)
        ticker = ""
        for line in row_lines:
            if re.fullmatch(r"\$?[A-Z][A-Z0-9.]{0,6}", line):
                ticker = line.lstrip("$")
                break
        bias = next((line for line in row_lines if line.upper() in {"BULLISH", "BEARISH", "NEUTRAL"}), "")
        score = None
        for line in row_lines:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)/100", line)
            if m:
                score = parse_float(m.group(1))
                break
        row: dict[str, Any] = {
            "rank": int(lines[idx][1:]) if ranked else len(rows) + 1,
            "state": row_lines[0] if row_lines and row_lines[0].upper() in {"ACTIVE", "ARMING", "TRIGGERED", "COMPLETED"} else "",
            "ticker": ticker,
            "bias": bias,
            "score": score,
            "edge": None,
            "setup": "",
            "setup_family": "",
            "regime": "",
            "target_progress": "",
            "latest_event": "",
            "surface_event": "",
            "event_timeline": [],
            "cipher_read": "",
            "gamma_regime": "",
            "vwap_state": "",
            "tape_state": "",
            "dte": "",
            "spot": None,
            "trigger": None,
            "pivot": None,
            "first_target": None,
            "push_target": None,
            "stretch": None,
            "invalidation": None,
            "runway_clarity_pct": None,
            "raw_card": raw,
        }
        label_map = {
            "SPOT": "spot",
            "TRIGGER": "trigger",
            "PIVOT": "pivot",
            "FIRST TARGET": "first_target",
            "PUSH TARGET": "push_target",
            "TARGET": "first_target",
            "STRETCH": "stretch",
            "INVALIDATION": "invalidation",
            "RUNWAY CLARITY": "runway_clarity_pct",
        }
        read_lines: list[str] = []
        for k, line in enumerate(row_lines):
            upper = line.upper()
            if re.fullmatch(r"\d+DTE", upper):
                row["dte"] = upper
            edge_match = re.fullmatch(r"EDGE\s+(\d+(?:\.\d+)?)", upper)
            if edge_match:
                row["edge"] = parse_float(edge_match.group(1))
            if upper in regime_labels:
                row["regime"] = line
            if re.fullmatch(r"\d+(?:\.\d+)?%\s+TO\s+TARGET", upper):
                row["target_progress"] = line
            if (
                upper in {"ARMING", "TRIGGERED", "COMPLETED", "FLOOR BOUNCE", "CEILING REJECTION", "REJECTION REVERSAL"}
                or "PUSH#" in upper
                or "BREAKOUT#" in upper
                or "BREAKDOWN#" in upper
                or "REVERSAL#" in upper
                or "CONTINUATION#" in upper
                or "BOUNCE#" in upper
                or "REJECTION#" in upper
            ):
                row["setup"] = line
                setup_name = re.sub(r"#\d+.*$", "", upper).strip()
                row["setup_family"] = re.sub(r"[^a-z0-9]+", "_", setup_name.lower()).strip("_")
            event_match = re.fullmatch(r"(\d+[smh])\s+(.+)", line, flags=re.IGNORECASE)
            if event_match:
                event_text = event_match.group(2).strip()
                row["event_timeline"].append({"age": event_match.group(1), "event": event_text})
                if "SURFACED" in event_text.upper():
                    row["surface_event"] = event_text
                if event_text.upper().startswith("NOW "):
                    row["latest_event"] = event_text[4:].strip()
                elif not row["latest_event"]:
                    row["latest_event"] = event_text
            if upper == "CIPHER READ":
                read_lines = row_lines[k + 1:]
                break
            key = label_map.get(upper)
            if key and k + 1 < len(row_lines):
                value = row_lines[k + 1]
                if key == "runway_clarity_pct":
                    row[key] = parse_float(value)
                else:
                    row[key] = parse_float(value.split(" ", 1)[0])
        row["cipher_read"] = " ".join(read_lines)
        read_upper = row["cipher_read"].upper()
        gamma_match = re.search(r"((?:NEGATIVE|POSITIVE|LONG|SHORT|MIXED)-GAMMA\s+[^,.]+)", read_upper)
        if gamma_match:
            row["gamma_regime"] = gamma_match.group(1).lower()
        vwap_match = re.search(r"PRICE\s+(ABOVE|BELOW|AT)\s+VWAP", read_upper)
        if vwap_match:
            row["vwap_state"] = f"price {vwap_match.group(1).lower()} VWAP"
        tape_match = re.search(r"(CALL|PUT)\s+(BUYING|SELLING)\s+ON\s+THE\s+TAPE", read_upper)
        if tape_match:
            row["tape_state"] = f"{tape_match.group(1).lower()} {tape_match.group(2).lower()}"
        rows.append(row)
        idx = j
    return rows


def parse_rows(mode: str, text: str) -> list[dict[str, Any]]:
    if mode == "cluster":
        return parse_cluster(text)
    if mode == "liq":
        return parse_liq(text)
    if mode == "cipher_model":
        return parse_cipher_model(text)
    if mode in {"flash", "flash_index", "flash_agentic"}:
        return parse_flash(text)
    raise ValueError(mode)


def canonical_direction(value: Any, setup: str = "") -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "BULL": "BULLISH",
        "LONG": "BULLISH",
        "UPSIDE": "BULLISH",
        "BEAR": "BEARISH",
        "SHORT": "BEARISH",
        "DOWNSIDE": "BEARISH",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return normalized
    setup_upper = setup.upper()
    if "UPSIDE" in setup_upper:
        return "BULLISH"
    if "DOWNSIDE" in setup_upper:
        return "BEARISH"
    return normalized


def setup_family(value: Any) -> str:
    setup = str(value or "").strip().upper()
    setup = re.sub(r"#\d+.*$", "", setup)
    setup = re.sub(r"\b(?:ACTIVE|SURFACED|ARMING|TRIGGERED|COMPLETED|INVALIDATED)\b", "", setup)
    return re.sub(r"[^a-z0-9]+", "_", setup.lower()).strip("_")


def numeric_level(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d*\.?\d+", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def enrich_rows(mode: str, captured_at: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    intraday_mode = mode in {"flash", "flash_index", "flash_agentic"}
    for card_index, original in enumerate(rows):
        row = dict(original)
        setup = str(row.get("setup") or row.get("setup_type") or "").strip()
        direction = canonical_direction(row.get("bias") or row.get("direction"), setup)
        spot = numeric_level(row.get("spot"))
        pivot = numeric_level(row.get("pivot"))
        if pivot is None:
            pivot = numeric_level(row.get("trigger"))
        target = numeric_level(row.get("first_target"))
        if target is None:
            target = numeric_level(row.get("target"))
        if target is None:
            target = numeric_level(row.get("cluster_target"))
        if target is None:
            target = numeric_level(row.get("pull_target"))
        invalidation = numeric_level(row.get("invalidation"))
        score = numeric_level(row.get("score"))
        strength = numeric_level(row.get("strength"))
        validation_errors: list[str] = []
        validation_warnings: list[str] = []

        if score is not None and not 0 <= score <= 100:
            if mode == "cluster" and strength is None and score > 100:
                strength = score
                score = None
                validation_warnings.append("out_of_range_cluster_score_moved_to_strength")
            else:
                validation_errors.append("score_out_of_range_0_100")

        if direction in {"BULLISH", "BEARISH"} and spot is not None:
            if target is not None:
                if direction == "BULLISH" and target <= spot:
                    validation_errors.append("bullish_target_not_above_spot")
                if direction == "BEARISH" and target >= spot:
                    validation_errors.append("bearish_target_not_below_spot")
                if abs(target - spot) / spot > 0.12:
                    validation_errors.append("target_more_than_12pct_from_spot")
            elif intraday_mode:
                validation_warnings.append("missing_target")
            if invalidation is not None:
                if direction == "BULLISH" and invalidation >= spot:
                    validation_errors.append("bullish_invalidation_not_below_spot")
                if direction == "BEARISH" and invalidation <= spot:
                    validation_errors.append("bearish_invalidation_not_above_spot")
                if abs(invalidation - spot) / spot > 0.12:
                    validation_errors.append("invalidation_more_than_12pct_from_spot")
            elif intraday_mode:
                validation_warnings.append("missing_invalidation")

        structural_level = pivot if pivot is not None else target
        signature_material = "|".join(
            [
                "accessobsidian",
                mode,
                str(row.get("ticker") or "").upper(),
                direction,
                setup_family(setup),
                f"{structural_level:.4f}" if structural_level is not None else "",
            ]
        )
        signal_signature = hashlib.sha256(signature_material.encode("utf-8")).hexdigest()[:24]
        actionable = bool(
            not validation_errors
            and direction in {"BULLISH", "BEARISH"}
            and spot is not None
            and target is not None
            and invalidation is not None
        )
        row.update(
            {
                "source": "accessobsidian",
                "scan_type": mode,
                "captured_at": captured_at,
                "card_index": card_index,
                "direction": direction,
                "setup_type": setup.upper(),
                "setup_family": setup_family(setup),
                "score": score,
                "strength": strength,
                "spot": spot,
                "pivot": pivot,
                "target": target,
                "invalidation": invalidation,
                "signal_signature": signal_signature,
                "geometry_valid": not validation_errors,
                "actionable": actionable,
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
            }
        )
        enriched.append(row)
    return enriched


def write_outputs(run_dir: Path, mode: str, captured_at: str, text: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    prefix = run_dir / mode
    txt_path = prefix.with_suffix(".txt")
    json_path = prefix.with_suffix(".json")
    csv_path = prefix.with_suffix(".csv")
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(
        json.dumps({"captured_at": captured_at, "mode": mode, "rows": rows}, indent=2),
        encoding="utf-8",
    )
    keys = sorted({key for row in rows for key in row})
    if "rank" in keys:
        keys.remove("rank")
        keys.insert(0, "rank")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()})
    return {"txt": str(txt_path), "json": str(json_path), "csv": str(csv_path)}


def load_quad_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.is_file():
        return {"entries": {}}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"entries": {}}


def update_quad_ledger(captured_at: str, cluster_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ledger = load_quad_ledger()
    entries = ledger.setdefault("entries", {})
    new_entries: list[dict[str, Any]] = []
    current_quads: list[dict[str, Any]] = []
    for row in cluster_rows:
        setup = str(row.get("setup") or "").upper()
        if "QUAD" not in setup:
            continue
        ticker = str(row.get("ticker") or "").upper()
        direction = "UPSIDE" if "UPSIDE" in setup else ("DOWNSIDE" if "DOWNSIDE" in setup else "")
        key = f"{ticker}|{direction}"
        record = {
            "ticker": ticker,
            "direction": direction,
            "setup": row.get("setup"),
            "first_seen_at": captured_at,
            "last_seen_at": captured_at,
            "seen_count": 1,
            "latest_rank": row.get("rank"),
            "latest_dte": row.get("dte"),
            "latest_spot": row.get("spot"),
            "latest_cluster_target": row.get("cluster_target"),
            "latest_strength": row.get("strength"),
            "latest_levels": row.get("levels") or [],
        }
        existing = entries.get(key)
        if existing:
            record["first_seen_at"] = existing.get("first_seen_at") or captured_at
            record["seen_count"] = int(existing.get("seen_count") or 0) + 1
        else:
            new_entries.append(record.copy())
        entries[key] = record
        current_quads.append(record.copy())
    ledger["updated_at"] = captured_at
    ledger["entries"] = entries
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return {
        "ledger_path": str(LEDGER_PATH),
        "current_quad_count": len(current_quads),
        "new_quad_count": len(new_entries),
        "current_quads": current_quads,
        "new_quad_entries": new_entries,
    }


def write_quad_outputs(run_dir: Path, quad_summary: dict[str, Any]) -> dict[str, str]:
    json_path = run_dir / "new_quad_entries.json"
    csv_path = run_dir / "new_quad_entries.csv"
    json_path.write_text(json.dumps(quad_summary, indent=2), encoding="utf-8")
    rows = quad_summary.get("new_quad_entries") or []
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "ticker",
            "direction",
            "setup",
            "first_seen_at",
            "latest_rank",
            "latest_dte",
            "latest_spot",
            "latest_cluster_target",
            "latest_strength",
            "latest_levels",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **{key: row.get(key) for key in fieldnames if key != "latest_levels"},
                "latest_levels": json.dumps(row.get("latest_levels") or []),
            })
    return {"json": str(json_path), "csv": str(csv_path)}


def build_overlap_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode = {result.get("mode"): result for result in results}
    cluster_rows = by_mode.get("cluster", {}).get("top") or []
    liq_rows = by_mode.get("liq", {}).get("top") or []
    model_rows = by_mode.get("cipher_model", {}).get("top") or []
    liq_by_ticker = {str(row.get("ticker")).upper(): row for row in liq_rows}
    model_by_ticker = {str(row.get("ticker")).upper(): row for row in model_rows}
    overlaps = []
    for row in cluster_rows:
        ticker = str(row.get("ticker") or "").upper()
        overlap = {"ticker": ticker, "cluster": row}
        if ticker in liq_by_ticker:
            overlap["liq"] = liq_by_ticker[ticker]
        if ticker in model_by_ticker:
            overlap["cipher_model"] = model_by_ticker[ticker]
        if len(overlap) > 2:
            overlaps.append(overlap)
    return {"cluster_top_overlap_count": len(overlaps), "cluster_top_overlaps": overlaps}


def compact_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for result in results:
        compacted.append({key: value for key, value in result.items() if key != "rows"})
    return compacted


def acquire_lock() -> Any | None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"pid={os.getpid()} started_at={datetime.now().astimezone().isoformat(timespec='seconds')}\n")
    handle.flush()
    return handle


def run_scan(scan_mode: ScanMode, run_dir: Path, captured_at: str, timeout_seconds: int) -> dict[str, Any]:
    session = f"ao-{scan_mode.key}-{captured_at.replace(':', '').replace('-', '').replace('+', '')}"
    try:
        command("navigate", {"url": APP_URL, "newTab": True, "group_title": f"AO {scan_mode.key}"}, session, timeout=90)
        wait_for_app_shell(session)
        click_button_by_text(session, "Setup Scanner")
        wait_for_setup_scanner(session)
        click_button_by_text(session, scan_mode.button)
        text = wait_for_scan_results(session, timeout_seconds=timeout_seconds, mode=scan_mode.key)
        rows = enrich_rows(scan_mode.key, captured_at, parse_rows(scan_mode.key, text))
        paths = write_outputs(run_dir, scan_mode.key, captured_at, text, rows)
        return {"mode": scan_mode.key, "row_count": len(rows), "rows": rows, "top": rows[:10], "paths": paths}
    finally:
        close_session(session)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="cluster,liq,cipher_model")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--serial", action="store_true", help="Run scans one after another instead of three tabs in parallel.")
    parser.add_argument("--no-lock", action="store_true", help="Allow overlapping scanner captures.")
    args = parser.parse_args()

    lock_handle = None
    if not args.no_lock:
        lock_handle = acquire_lock()
        if lock_handle is None:
            skipped = {
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "skipped": True,
                "reason": f"Another scanner capture is already running; lock held at {LOCK_PATH}",
            }
            print(json.dumps(skipped, indent=2))
            return 0

    selected = [SCAN_MODES[key.strip()] for key in args.modes.split(",") if key.strip()]
    try:
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        run_dir = OUT_ROOT / datetime.now().astimezone().strftime("%Y-%m-%d") / stamp
        run_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if args.serial:
            for mode in selected:
                try:
                    results.append(run_scan(mode, run_dir, captured_at, args.timeout_seconds))
                except Exception as exc:  # noqa: BLE001 - CLI should report all scan failures.
                    errors.append({"mode": mode.key, "error": str(exc)})
        else:
            with ThreadPoolExecutor(max_workers=len(selected)) as executor:
                futures = {executor.submit(run_scan, mode, run_dir, captured_at, args.timeout_seconds): mode for mode in selected}
                for future in as_completed(futures):
                    mode = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:  # noqa: BLE001 - CLI should report all scan failures.
                        errors.append({"mode": mode.key, "error": str(exc)})

        cluster_rows: list[dict[str, Any]] = []
        for result in results:
            if result.get("mode") == "cluster":
                cluster_rows = result.get("rows") or []
                break
        quad_summary = update_quad_ledger(captured_at, cluster_rows) if cluster_rows else {
            "ledger_path": str(LEDGER_PATH),
            "current_quad_count": 0,
            "new_quad_count": 0,
            "current_quads": [],
            "new_quad_entries": [],
        }
        quad_paths = write_quad_outputs(run_dir, quad_summary)
        overlap_summary = build_overlap_summary(results)

        summary = {
            "captured_at": captured_at,
            "run_dir": str(run_dir),
            "results": compact_results(results),
            "quad_summary": quad_summary,
            "quad_paths": quad_paths,
            "overlap_summary": overlap_summary,
            "errors": errors,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 1 if errors else 0
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print(f"Kimi WebBridge is not reachable: {exc}", file=sys.stderr)
        raise SystemExit(2)
