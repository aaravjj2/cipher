"""Episode tracking for Flash Agentic cards, with target extension.

The behaviour this reproduces, described by the product's own walkthrough of a
Micron trade: the card surfaced a floor bounce at $81 calling for $83; price
reached $83 five minutes later; the model then *extended* the target rather than
closing the card, and kept extending to $88 as momentum held; the card finally
turned off when the structure flipped to bearish.

Our scanner could not do that, because it recomputed both targets from the live
spot on every scan (`t1 = spot ± 0.8%`). A target anchored to the current price
can never be reached — it runs away as fast as price approaches — so
"progress to target" drifted around a fixed point and no card ever completed.

An episode fixes the anchor. It opens when a setup first appears, holds its
target until price actually trades through it, and only then promotes to the next
structural level, recording each promotion. It closes when the structure flips or
the invalidation is breached.

State is persisted because scans are independent processes: without it, every
scan would re-open the same episode and re-anchor the target it was meant to hold.

Research-only. Tracks and describes signals; places no orders.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "agentic_episodes.json"

# An episode nobody has scanned in this long is stale — the market has moved on
# and re-anchoring is more honest than resuming a hours-old target.
STALE_SECONDS = 6 * 3600
# Cap on how far the model will keep chasing. The Micron move extended roughly
# four times; allowing unlimited promotion turns a signal into a trend-follower
# that never admits the move is over.
MAX_EXTENSIONS = 6
# Price must trade this far through a target to count as reached, so a single
# tick brushing the level does not trigger a promotion.
HIT_TOLERANCE = 0.0005

_LOCK = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load():
    try:
        with STORE.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a scan crashing mid-write must not leave a truncated store that
    # silently resets every episode on the next read.
    handle, tmp = tempfile.mkstemp(dir=str(STORE.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        os.replace(tmp, STORE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _reached(direction, spot, target):
    if target is None:
        return False
    if direction == "BEARISH":
        return spot <= target * (1 + HIT_TOLERANCE)
    return spot >= target * (1 - HIT_TOLERANCE)


def _next_level(direction, beyond, levels):
    """Nearest structural level past `beyond` in the trade's direction.

    Extension targets are structural, not another fixed percentage: the point of
    extending is that price is running toward the next wall, and a wall is where
    it is likely to stop.
    """
    candidates = [
        float(price) for price in (levels or ())
        if price is not None
        and (float(price) > beyond if direction != "BEARISH" else float(price) < beyond)
    ]
    if not candidates:
        return None
    return min(candidates) if direction != "BEARISH" else max(candidates)


def update(
    ticker,
    *,
    direction,
    setup,
    spot,
    first_target,
    invalidation=None,
    levels=None,
    structure_flipped=False,
):
    """Advance (or open, or close) this ticker's episode and return it.

    `levels` are candidate structural prices — GEX/VEX walls and session levels —
    used to pick extension targets.
    """
    if not ticker or not spot or spot <= 0:
        return None
    key = f"{ticker.upper()}:{direction or 'NEUTRAL'}"

    with _LOCK:
        store = _load()
        episode = store.get(key)
        now_ts = time.time()

        if episode and (
            episode.get("state") != "active"
            or now_ts - float(episode.get("updated_ts") or 0) > STALE_SECONDS
            or episode.get("setup") != setup
        ):
            episode = None

        if episode is None:
            if first_target is None:
                return None
            episode = {
                "ticker": ticker.upper(),
                "direction": direction,
                "setup": setup,
                "opened_at": _now(),
                "entry_price": round(float(spot), 4),
                "original_target": round(float(first_target), 4),
                "target": round(float(first_target), 4),
                "extensions": [],
                "extension_count": 0,
                "max_favorable": round(float(spot), 4),
                "state": "active",
                "close_reason": None,
                "closed_at": None,
            }

        entry = float(episode["entry_price"])
        # Track the best price seen so the card can report how far the move ran,
        # not just where it is now.
        if direction == "BEARISH":
            episode["max_favorable"] = round(min(float(episode["max_favorable"]), float(spot)), 4)
        else:
            episode["max_favorable"] = round(max(float(episode["max_favorable"]), float(spot)), 4)

        # Structure flip ends the episode — this is what turned the Micron card off.
        if structure_flipped:
            episode["state"] = "completed"
            episode["close_reason"] = "structure flipped"
        elif invalidation is not None and (
            (direction == "BEARISH" and spot >= float(invalidation))
            or (direction != "BEARISH" and spot <= float(invalidation))
        ):
            episode["state"] = "invalidated"
            episode["close_reason"] = "invalidation breached"
        else:
            # Promote through every target price has already cleared. A scan can
            # arrive well after the move, so this catches up rather than advancing
            # one rung per scan.
            while _reached(direction, float(spot), episode.get("target")):
                if episode["extension_count"] >= MAX_EXTENSIONS:
                    episode["state"] = "completed"
                    episode["close_reason"] = f"target reached after {MAX_EXTENSIONS} extensions"
                    break
                hit = float(episode["target"])
                nxt = _next_level(direction, hit, levels)
                if nxt is None:
                    episode["state"] = "completed"
                    episode["close_reason"] = "target reached, no further structure"
                    break
                episode["extensions"].append(
                    {"at": _now(), "from": hit, "to": round(float(nxt), 4), "spot": round(float(spot), 4)}
                )
                episode["extension_count"] += 1
                episode["target"] = round(float(nxt), 4)

        target = float(episode["target"])
        span = target - entry
        episode["progress_pct"] = (
            round(max(0.0, min(100.0, 100.0 * (float(spot) - entry) / span)), 1) if span else None
        )
        episode["move_pct"] = round(100.0 * (float(spot) - entry) / entry, 2)
        episode["updated_at"] = _now()
        episode["updated_ts"] = now_ts

        if episode["state"] == "active":
            store[key] = episode
        else:
            episode["closed_at"] = episode["updated_at"]
            store.pop(key, None)
            store.setdefault("_closed", [])
            store["_closed"] = ([episode] + store["_closed"])[:200]
        _save(store)
        return dict(episode)


def active():
    store = _load()
    return [v for k, v in store.items() if k != "_closed" and isinstance(v, dict)]


def closed(limit=50):
    return (_load().get("_closed") or [])[:limit]
