from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .config import ExecutorConfig
from .models import Direction, SignalCard, SkipReason

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def normalize_scanner(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"flashagentic": "flash_agentic", "flash_agentic_beta": "flash_agentic"}
    return aliases.get(text, text)


def normalize_setup(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    text = re.sub(r"#\d+.*$", "", text).strip()
    return " ".join(text.split())


def parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def extract_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cards = payload.get("cards")
    if cards is None:
        cards = payload.get("signals")
    if cards is None:
        cards = payload.get("rows")
    if cards is None and any(k in payload for k in ("ticker", "symbol")):
        cards = [payload]
    if not isinstance(cards, list):
        raise ValueError("payload cards must be a list")
    return [c if isinstance(c, dict) else {"malformed": c} for c in cards]


def validate_card(raw: dict[str, Any], cfg: ExecutorConfig, now: datetime | None = None) -> tuple[SignalCard | None, list[str]]:
    reasons: list[str] = []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scanner = normalize_scanner(raw.get("scanner_type") or raw.get("scanner") or raw.get("type"))
    if scanner not in cfg.scanner.accepted_types:
        reasons.append("unrecognized scanner type")
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper().strip()
    if not TICKER_RE.match(ticker):
        reasons.append("invalid ticker")
    direction_text = str(raw.get("direction") or raw.get("bias") or "").lower().strip()
    if direction_text not in {"bullish", "bearish"}:
        reasons.append("invalid direction")
        direction = Direction.BULLISH
    else:
        direction = Direction(direction_text)
    setup = normalize_setup(raw.get("setup") or raw.get("setup_type") or raw.get("setupType"))
    # Upstream scanner flags are advisory only when absent, but an explicit false
    # is authoritative. This keeps leaderboard/context cards out of the paper book.
    if raw.get("geometry_valid") is False:
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    if raw.get("actionable") is False:
        reasons.append(SkipReason.SKIPPED_NOT_ACTIONABLE.value)
    try:
        captured = parse_ts(raw.get("captured_timestamp") or raw.get("captured_at") or raw.get("timestamp"))
    except Exception:
        captured = now
        reasons.append("invalid timestamp")
    spot = number(raw.get("spot") or raw.get("underlying_price"))
    target = number(raw.get("target"))
    invalidation = number(raw.get("invalidation") or raw.get("stop") or raw.get("invalid"))
    if spot is None or spot <= 0:
        reasons.append("invalid spot")
        spot = 0.0
    if target is None or invalidation is None:
        reasons.append(SkipReason.SKIPPED_MISSING_LEVEL.value)
    elif target <= 0 or invalidation <= 0:
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    elif direction == Direction.BULLISH and not (target > spot and invalidation < spot):
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    elif direction == Direction.BEARISH and not (target < spot and invalidation > spot):
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    if target and spot and abs(target - spot) / spot * 100 > cfg.scanner.maximum_level_distance_pct:
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    if invalidation and spot and abs(invalidation - spot) / spot * 100 > cfg.scanner.maximum_level_distance_pct:
        reasons.append(SkipReason.SKIPPED_INVALID_GEOMETRY.value)
    if (now - captured).total_seconds() > cfg.scanner.maximum_signal_age_seconds:
        reasons.append(SkipReason.SKIPPED_STALE_SIGNAL.value)
    if ticker == "TEST":
        reasons.append(SkipReason.SKIPPED_SYNTHETIC.value)
    if reasons:
        return None, list(dict.fromkeys(reasons))
    return SignalCard(
        ticker=ticker,
        scanner_type=scanner,
        direction=direction,
        setup=setup,
        captured_at=captured,
        spot=spot,
        target=target,
        invalidation=invalidation,
        raw=raw,
        score=number(raw.get("score")),
        rank=int(number(raw.get("rank")) or 0) or None,
    ), []
