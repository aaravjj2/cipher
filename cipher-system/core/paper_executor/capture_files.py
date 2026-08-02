from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_capture_time(payload: dict[str, Any], path: Path) -> datetime | None:
    text = str(payload.get("captured_at") or payload.get("timestamp") or "")
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    parts = path.name.split("_")
    if len(parts) >= 2:
        try:
            return datetime.strptime(parts[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("seen_files", [])) if isinstance(data, dict) else set()


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"seen_files": sorted(seen)}, indent=2), encoding="utf-8")
    tmp.replace(path)


def iter_capture_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in ("ready", "uploaded"):
        folder = root / name
        if folder.exists():
            paths.extend(folder.glob("*.json"))
    return sorted(paths, key=lambda p: p.stat().st_mtime)


def read_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def post_payload(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return 200 <= response.status < 300, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)


def ingest_once(root: Path, state_path: Path, url: str, max_age_seconds: int, include_uploaded: bool = False) -> dict[str, Any]:
    seen = load_seen(state_path)
    now = datetime.now(timezone.utc)
    scanned = posted = skipped_old = skipped_seen = failed = 0
    errors: list[dict[str, str]] = []
    folders = ("ready", "uploaded") if include_uploaded else ("ready",)
    files: list[Path] = []
    for folder_name in folders:
        folder = root / folder_name
        if folder.exists():
            files.extend(folder.glob("*.json"))
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        key = str(path.resolve())
        scanned += 1
        if key in seen:
            skipped_seen += 1
            continue
        try:
            payload = read_payload(path)
            captured = parse_capture_time(payload, path)
            if captured and (now - captured).total_seconds() > max_age_seconds:
                skipped_old += 1
                seen.add(key)
                continue
            payload = dict(payload)
            payload.setdefault("source", "accessobsidian_capture_file")
            payload.setdefault("capture_file", str(path))
            ok, detail = post_payload(url, payload)
            if ok:
                posted += 1
                seen.add(key)
            else:
                failed += 1
                errors.append({"file": str(path), "error": detail[:500]})
        except Exception as exc:
            failed += 1
            errors.append({"file": str(path), "error": str(exc)})
    save_seen(state_path, seen)
    return {
        "scanned": scanned,
        "posted": posted,
        "skipped_seen": skipped_seen,
        "skipped_old": skipped_old,
        "failed": failed,
        "errors": errors[:10],
    }


def summarize(root: Path) -> dict[str, Any]:
    by_scan: Counter[str] = Counter()
    by_setup: Counter[str] = Counter()
    by_ticker: Counter[str] = Counter()
    by_key: Counter[str] = Counter()
    score_sum: defaultdict[str, float] = defaultdict(float)
    score_count: Counter[str] = Counter()
    files = cards = actionable = 0
    first: str | None = None
    last: str | None = None
    for path in iter_capture_files(root):
        try:
            payload = read_payload(path)
        except Exception:
            continue
        files += 1
        scan = str(payload.get("scan_type") or payload.get("scanner_type") or "unknown")
        captured = parse_capture_time(payload, path)
        if captured:
            text = captured.isoformat()
            first = text if first is None or text < first else first
            last = text if last is None or text > last else last
        by_scan[scan] += 1
        for card in payload.get("cards") or []:
            if not isinstance(card, dict):
                continue
            cards += 1
            ticker = str(card.get("ticker") or "").upper()
            setup = str(card.get("setup_type") or card.get("setup") or "unknown").lower()
            direction = str(card.get("direction") or "").lower()
            score = card.get("score")
            by_ticker[ticker] += 1
            by_setup[setup] += 1
            key = "|".join([scan, setup, direction or "none"])
            by_key[key] += 1
            if direction in {"bullish", "bearish"} and card.get("target") and card.get("invalidation"):
                actionable += 1
            if isinstance(score, (int, float)):
                score_sum[key] += float(score)
                score_count[key] += 1
    top_patterns = []
    for key, count in by_key.most_common(25):
        top_patterns.append({
            "pattern": key,
            "count": count,
            "average_score": round(score_sum[key] / score_count[key], 2) if score_count[key] else None,
        })
    return {
        "capture_root": str(root),
        "files": files,
        "cards": cards,
        "actionable_cards": actionable,
        "first_capture_at": first,
        "last_capture_at": last,
        "files_by_scan": dict(by_scan.most_common()),
        "top_tickers": dict(by_ticker.most_common(20)),
        "top_setups": dict(by_setup.most_common(20)),
        "top_patterns": top_patterns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest or summarize Cipher capture JSON files.")
    parser.add_argument("--root", default=r"C:\Aarav\cipher-system\CipherCapture")
    parser.add_argument("--state", default=None)
    parser.add_argument("--url", default="http://127.0.0.1:8787/api/scanner-ingest")
    parser.add_argument("--max-age-seconds", type=int, default=5)
    parser.add_argument("--include-uploaded", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    state = Path(args.state) if args.state else root / "state" / "capture_file_ingest_state.json"
    if args.summarize:
        print(json.dumps(summarize(root), indent=2))
        return 0
    while True:
        print(json.dumps(ingest_once(root, state, args.url, args.max_age_seconds, args.include_uploaded), indent=2))
        if not args.watch:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
