from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import sha256_id
from .validation import extract_cards


def normalize_scan_row(raw: dict[str, Any]) -> dict[str, Any]:
    card = dict(raw)
    if not card.get("direction") and card.get("bias"):
        card["direction"] = str(card["bias"]).lower()
    if not any(card.get(key) for key in ("setup", "setup_type", "setupType")) and card.get("setup_family"):
        card["setup"] = str(card["setup_family"]).replace("_", " ")
    if not card.get("target"):
        card["target"] = card.get("first_target") or card.get("push_target") or card.get("stretch")
    return card


def batch_id(payload: dict[str, Any]) -> str:
    explicit = payload.get("batch_id") or payload.get("id")
    return str(explicit) if explicit else sha256_id("batch", payload)


def normalize_batch(payload: dict[str, Any]) -> dict[str, Any]:
    cards = extract_cards(payload)
    scanner_type = payload.get("scanner_type") or payload.get("scanner") or payload.get("scan_type") or payload.get("mode") or payload.get("type")
    captured_at = payload.get("captured_timestamp") or payload.get("captured_at") or payload.get("timestamp")
    enriched_cards: list[dict[str, Any]] = []
    for raw in cards:
        card = normalize_scan_row(raw)
        if scanner_type and not any(card.get(key) for key in ("scanner_type", "scanner", "type")):
            card["scanner_type"] = scanner_type
        if captured_at and not any(card.get(key) for key in ("captured_timestamp", "captured_at", "timestamp")):
            card["captured_at"] = captured_at
        enriched_cards.append(card)
    received_at = datetime.now(timezone.utc).isoformat()
    return {
        "batch_id": batch_id(payload),
        "received_at": received_at,
        "source": payload.get("source") or "access_obsidian_browser",
        "raw": payload,
        "cards": enriched_cards,
    }
