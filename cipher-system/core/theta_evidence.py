"""Local OCR and deliberately strict Theta evidence parsing. No network calls."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "theta-evidence-v2"
ADMIN = re.compile(r"^[^\w]*(?:commands|status|eod report|this month|all.time pnl|menu|summary|historical|report)\b", re.I)


def source(body):
    match = re.search(r"(?ims)^Text:\s*(.*?)(?=\n(?:Reasoning|PnL|Status):|\Z)", body)
    return (match.group(1) if match else body).strip()


def ocr(path: str) -> dict:
    result = {"parser_version": VERSION, "text": "", "sha256": None, "confidence": None, "error": None}
    try:
        image = Path(path).resolve(strict=True)
        if image.stat().st_size > 20_000_000:
            raise ValueError("image_too_large")
        result["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
        # TSV gives word confidence; stdout stays local and only enters SQLite.
        proc = subprocess.run(["tesseract", str(image), "stdout", "--psm", "6", "tsv"],
                              capture_output=True, text=True, timeout=20, check=True)
        rows = [r for r in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t") if r.get("text", "").strip()]
        result["text"] = " ".join(r["text"] for r in rows)
        scores = [float(r["conf"]) for r in rows if float(r["conf"]) >= 0]
        result["confidence"] = min(scores) if scores else None
        if not scores or min(scores) < 85:
            result["error"] = "unclear_ocr"
    except (OSError, ValueError, subprocess.SubprocessError):
        result["error"] = "local_ocr_unavailable"
    return result


def parse(text: str, timestamp: str) -> dict:
    text = source(text)
    if ADMIN.search(text) or not text or re.search(r"\b(?:yesterday|last week|backtest|historical report)\b", text, re.I):
        raise ValueError("administrative_or_historical")
    if re.search(r"\b(?:ratio|roll|adjust|convert)\b|\b[2-9]\s*[xX]\b", text, re.I):
        raise ValueError("ratio_or_adjustment_requires_review")
    day = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b", text)
    if re.search(r"\btoday\b", text, re.I):
        dates.append(day.isoformat())
    parsed_dates = set()
    for d in dates:
        if "/" in d:
            m, dd, y = map(int, d.split("/"))
            d = date(y + 2000 if y < 100 else y, m, dd).isoformat()
        parsed_dates.add(date.fromisoformat(d).isoformat())
    if len(parsed_dates) != 1:
        raise ValueError("explicit_expiration_required")
    expiry = parsed_dates.pop()
    # Each leg must explicitly state side, strike, type and quantity.  No
    # implicit buys, dollar/point guessing, C/P expansion, or ratio truncation.
    pattern = r"\b(Buy|Sell)\s+(?:([A-Z]{1,6})\s+)?(\d+(?:\.\d+)?)\s*(C|P|CALL|PUT)\b\s*(?:qty|quantity|x)\s*(\d+)\b"
    matches = list(re.finditer(pattern, text, re.I))
    if not 1 <= len(matches) <= 4 or len(re.findall(r"\b(?:Buy|Sell)\b", text, re.I)) != len(matches):
        raise ValueError("explicit_legs_and_quantities_required")
    symbols = {m.group(2).upper() for m in matches if m.group(2)}
    leading = re.match(r"^\$?([A-Z]{1,6})\s+(?:Buy|Sell)\b", text, re.I)
    if leading:
        symbols.add(leading.group(1).upper())
    if len(symbols) != 1:
        raise ValueError("unambiguous_ticker_required")
    legs = [{"side": m.group(1).upper(), "strike": float(m.group(3)), "kind": m.group(4)[0].upper(), "quantity": int(m.group(5))} for m in matches]
    if any(l["quantity"] != 1 for l in legs) or len({(l["strike"], l["kind"]) for l in legs}) != len(legs):
        raise ValueError("ratio_or_duplicate_contract")
    prices = re.findall(r"\b(debit|credit)\s*\$?(\d+(?:\.\d+)?)\s*(points?|contract dollars?)\b", text, re.I)
    if len(prices) != 1 or float(prices[0][1]) <= 0:
        raise ValueError("explicit_price_convention_required")
    holding = "same_day" if re.search(r"\b(?:today|same.day|day trade)\b", text, re.I) else "swing" if re.search(r"\bswing\b", text, re.I) else None
    if holding is None:
        raise ValueError("holding_period_required")
    # A stated duration that this version cannot represent stays in review.
    if re.search(r"\b(?:hold|holding)\s+(?:for\s+)?\d+\s*(?:days?|weeks?|hours?)\b", text, re.I):
        raise ValueError("unsupported_holding_period")
    return {"symbol": symbols.pop(), "expiration": expiry, "legs": sorted(legs, key=lambda l: (l["kind"], l["strike"], l["side"])),
            "price_style": prices[0][0].lower(), "signal_price": float(prices[0][1]) / (100 if prices[0][2].lower().startswith("contract") else 1), "holding": holding}


def evidence(message: dict) -> tuple[dict | None, dict | None, str | None]:
    image = ocr(message["media_path"]) if message.get("media_path") else None
    try:
        caption = source(message.get("text") or "")
        if image:
            if image["error"]:
                raise ValueError(image["error"])
            parsed = parse(image["text"], message["date"])
            if caption and caption.lower() not in {"none", "(none)"}:
                if parse(caption, message["date"]) != parsed:
                    raise ValueError("caption_image_conflict")
        else:
            parsed = parse(caption, message["date"])
        return parsed, image, None
    except (ValueError, TypeError) as exc:
        return None, image, str(exc)
