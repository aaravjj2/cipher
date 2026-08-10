"""Historical GEX replay and delta-GEX computation engine.

Reads captured snapshots from gex_history.sqlite and computes:
- Delta-GEX: change in net GEX at each strike between consecutive snapshots
- Delta-GEX momentum: cumulative shift in dealer positioning over time
- GEX regime classification (positive/negative/flip, expanding/contracting)
- Strike-level GEX velocity (rate of change)

All outputs carry the standard caveat: GEX is a public-OI heuristic,
not verified dealer positioning.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "gex_history.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_tickers(db_path: Path = DEFAULT_DB) -> list[str]:
    """Return distinct tickers that have at least one captured snapshot."""
    if not db_path.is_file():
        return []
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT DISTINCT ticker FROM gex_snapshots ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def list_snapshots(
    db_path: Path = DEFAULT_DB,
    ticker: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return snapshot metadata ordered by capture time (newest first)."""
    if not db_path.is_file():
        return []
    query = "SELECT * FROM gex_snapshots"
    params: list = []
    if ticker:
        query += " WHERE ticker = ?"
        params.append(ticker.upper())
    query += " ORDER BY captured_at DESC LIMIT ?"
    params.append(int(limit))
    with _connect(db_path) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_snapshot_cells(
    db_path: Path,
    snapshot_id: int,
    *,
    include_unavailable: bool = False,
) -> list[dict]:
    """Return observed strike cells; visual placeholders are opt-in."""
    if not db_path.is_file():
        return []
    with _connect(db_path) as db:
        availability = "" if include_unavailable else " AND available = 1"
        rows = db.execute(
            f"""
            SELECT expiration, strike, call_gex, put_gex, net_gex,
                   call_vex, put_vex, net_vex,
                   call_oi, put_oi, volume, listed, available
            FROM gex_strike_cells
            WHERE snapshot_id = ?{availability}
            ORDER BY expiration, strike
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _aggregate_by_strike(cells: list[dict]) -> dict[float, dict]:
    """Sum GEX across expirations for each strike."""
    by_strike: dict[float, dict] = {}
    for cell in cells:
        strike = cell["strike"]
        if strike not in by_strike:
            by_strike[strike] = {
                "strike": strike,
                "call_gex": 0.0,
                "put_gex": 0.0,
                "net_gex": 0.0,
                "call_vex": 0.0,
                "put_vex": 0.0,
                "net_vex": 0.0,
                "call_oi": 0.0,
                "put_oi": 0.0,
                "volume": 0.0,
            }
        bucket = by_strike[strike]
        bucket["call_gex"] += cell.get("call_gex") or 0.0
        bucket["put_gex"] += cell.get("put_gex") or 0.0
        bucket["net_gex"] += cell.get("net_gex") or 0.0
        bucket["call_vex"] += cell.get("call_vex") or 0.0
        bucket["put_vex"] += cell.get("put_vex") or 0.0
        bucket["net_vex"] += cell.get("net_vex") or 0.0
        bucket["call_oi"] += cell.get("call_oi") or 0.0
        bucket["put_oi"] += cell.get("put_oi") or 0.0
        bucket["volume"] += cell.get("volume") or 0.0
    return by_strike


def compute_delta_gex(
    db_path: Path,
    ticker: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Compute delta-GEX between consecutive snapshots for a ticker.

    Returns a list of snapshot-pair diffs, newest first.
    Each entry has: captured_at, prev_captured_at, dt_hours,
    total_net_gex, prev_total_net_gex, delta_net_gex,
    strikes (per-strike delta), regime.
    """
    ticker = ticker.upper()
    if not db_path.is_file():
        return []

    snaps = list_snapshots(db_path, ticker=ticker, limit=limit)
    if len(snaps) < 2:
        return []

    # Process from oldest to newest so deltas are forward in time
    snaps_ordered = list(reversed(snaps))
    results = []

    for i in range(1, len(snaps_ordered)):
        prev_snap = snaps_ordered[i - 1]
        curr_snap = snaps_ordered[i]

        prev_cells = get_snapshot_cells(db_path, prev_snap["id"])
        curr_cells = get_snapshot_cells(db_path, curr_snap["id"])

        prev_by_strike = _aggregate_by_strike(prev_cells)
        curr_by_strike = _aggregate_by_strike(curr_cells)

        all_strikes = sorted(set(prev_by_strike.keys()) | set(curr_by_strike.keys()))

        total_net = 0.0
        prev_total_net = 0.0
        strike_deltas = []

        for strike in all_strikes:
            curr_net = curr_by_strike.get(strike, {}).get("net_gex", 0.0)
            prev_net = prev_by_strike.get(strike, {}).get("net_gex", 0.0)
            delta = curr_net - prev_net
            total_net += curr_net
            prev_total_net += prev_net
            strike_deltas.append({
                "strike": strike,
                "net_gex": curr_net,
                "prev_net_gex": prev_net,
                "delta_gex": delta,
                "abs_delta": abs(delta),
            })

        # Parse timestamps for dt
        try:
            t_curr = datetime.fromisoformat(curr_snap["captured_at"].replace("Z", "+00:00"))
            t_prev = datetime.fromisoformat(prev_snap["captured_at"].replace("Z", "+00:00"))
            dt_hours = (t_curr - t_prev).total_seconds() / 3600.0
        except Exception:
            dt_hours = None

        total_delta = total_net - prev_total_net
        regime = classify_regime(total_net, prev_total_net, strike_deltas)

        results.append({
            "ticker": ticker,
            "captured_at": curr_snap["captured_at"],
            "prev_captured_at": prev_snap["captured_at"],
            "dt_hours": round(dt_hours, 2) if dt_hours is not None else None,
            "total_net_gex": total_net,
            "prev_total_net_gex": prev_total_net,
            "delta_net_gex": total_delta,
            "regime": regime,
            "strikes": sorted(strike_deltas, key=lambda s: s["strike"]),
            "top_movers": sorted(
                strike_deltas, key=lambda s: s["abs_delta"], reverse=True
            )[:10],
        })

    # Return newest first
    results.reverse()
    return results


def classify_regime(
    total_net: float,
    prev_total_net: float,
    strike_deltas: list[dict] | None = None,
) -> dict:
    """Classify the current GEX regime.

    Returns: polarity (positive/negative/flip), momentum (expanding/contracting/stable),
    and dispersion (how spread out the deltas are).
    """
    # Polarity
    if total_net > 0 and prev_total_net > 0:
        polarity = "positive"
    elif total_net < 0 and prev_total_net < 0:
        polarity = "negative"
    elif total_net * prev_total_net < 0:
        polarity = "flip"
    else:
        polarity = "neutral"

    # Momentum: based on absolute magnitude change
    delta = total_net - prev_total_net
    abs_prev = abs(prev_total_net) if prev_total_net != 0 else 1.0
    abs_curr = abs(total_net)
    abs_delta = abs_curr - abs_prev
    pct_change = delta / abs_prev
    mag_pct_change = abs_delta / abs_prev

    if mag_pct_change > 0.10:
        momentum = "expanding"
    elif mag_pct_change < -0.10:
        momentum = "contracting"
    else:
        momentum = "stable"

    # Dispersion from strike deltas
    dispersion = "concentrated"
    if strike_deltas:
        big_movers = [s for s in strike_deltas if s["abs_delta"] > abs_prev * 0.05]
        if len(big_movers) > 5:
            dispersion = "broad"
        elif len(big_movers) > 2:
            dispersion = "moderate"

    return {
        "polarity": polarity,
        "momentum": momentum,
        "dispersion": dispersion,
        "pct_change": round(pct_change * 100, 2),
    }


def gex_momentum_score(
    db_path: Path,
    ticker: str,
    *,
    lookback: int = 5,
) -> dict:
    """Compute a GEX momentum score from recent delta-GEX history.

    Positive score = dealers increasingly long gamma (supportive/stabilizing).
    Negative score = dealers increasingly short gamma (destabilizing).
    Score is normalized to [-100, 100].
    """
    deltas = compute_delta_gex(db_path, ticker, limit=lookback + 1)
    if not deltas:
        return {
            "ticker": ticker,
            "score": None,
            "direction": None,
            "confidence": None,
            "n_snapshots": 0,
            "note": "Insufficient snapshot history",
        }

    # Weighted sum: more recent deltas get higher weight
    weights = list(range(1, len(deltas) + 1))
    total_weight = sum(weights)

    weighted_delta = 0.0
    for i, d in enumerate(deltas[:len(weights)]):
        weighted_delta += d["delta_net_gex"] * weights[i]

    normalized = weighted_delta / total_weight

    # Normalize to [-100, 100] using a reference scale
    # Typical total GEX for SPY is ~1e9, so 1e7 is a meaningful shift
    ref_scale = 1e7
    score = max(-100.0, min(100.0, normalized / ref_scale * 100))

    if score > 10:
        direction = "stabilizing"
    elif score < -10:
        direction = "destabilizing"
    else:
        direction = "neutral"

    confidence = min(1.0, len(deltas) / lookback)

    return {
        "ticker": ticker.upper(),
        "score": round(score, 2),
        "direction": direction,
        "confidence": round(confidence, 2),
        "n_snapshots": len(deltas),
        "weighted_delta_gex": normalized,
        "latest_regime": deltas[0].get("regime") if deltas else None,
        "caveat": (
            "GEX momentum from captured snapshots. Public-OI heuristic, "
            "not verified dealer positioning."
        ),
    }


def strike_level_velocity(
    db_path: Path,
    ticker: str,
    strike: float,
    *,
    limit: int = 10,
) -> list[dict]:
    """Track GEX velocity at a specific strike over time."""
    deltas = compute_delta_gex(db_path, ticker, limit=limit + 1)
    result = []
    for d in deltas:
        for s in d["strikes"]:
            if abs(s["strike"] - strike) < 0.01:
                dt = d.get("dt_hours") or 1.0
                velocity = s["delta_gex"] / max(dt, 0.01)
                result.append({
                    "captured_at": d["captured_at"],
                    "strike": strike,
                    "net_gex": s["net_gex"],
                    "delta_gex": s["delta_gex"],
                    "velocity_per_hour": round(velocity, 2),
                })
                break
    return result
