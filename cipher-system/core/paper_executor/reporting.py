from __future__ import annotations

from collections import Counter

from .database import PaperExecutorDatabase


def daily_report(db: PaperExecutorDatabase) -> dict:
    cards = db.rows("signal_cards")
    episodes = db.rows("signal_episodes")
    positions = db.rows("paper_positions")
    skipped = Counter(row.get("skip_reason") or "" for row in cards if row.get("skip_reason"))
    closed = [p for p in positions if p.get("status") == "CLOSED"]
    return {
        "cards_received": len(cards),
        "independent_episodes": len(episodes),
        "duplicates_suppressed": sum(max(0, int(e.get("poll_count") or 0) - 1) for e in episodes),
        "skipped_signals_by_reason": dict(skipped),
        "paper_entries": len(positions),
        "closed_positions": len(closed),
        "low_sample_size": len(closed) < 30,
    }
