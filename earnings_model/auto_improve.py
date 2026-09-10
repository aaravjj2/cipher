"""Auto-Improvement Loop for Earnings Model.

- Tracks model performance drift on settled positions
- Triggers retraining when holdout metrics degrade
- Maintains profit/loss ledger with actionable insights
- Auto-archives losing strategies, promotes winners
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .paper_portfolio import init_paper_db, get_paper_scorecard, settle_expired_positions
from .model import train_earnings_models, load_trained_models, MODEL_ARTIFACT_PATH

AUTO_IMPROVE_DIR = Path(DATA_DIR) / "auto_improve"
AUTO_IMPROVE_DIR.mkdir(parents=True, exist_ok=True)

DRIFT_LOG = AUTO_IMPROVE_DIR / "drift_log.json"
RETRAIN_LOG = AUTO_IMPROVE_DIR / "retrain_log.json"
PROFIT_LEDGER = AUTO_IMPROVE_DIR / "profit_ledger.json"
WINNER_ARCHIVE = AUTO_IMPROVE_DIR / "winner_archive.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, default=str, indent=2))


def log_drift(metrics: dict) -> None:
    """Log model drift metrics after each settlement cycle."""
    log = _load_json(DRIFT_LOG, [])
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "total_pnl": metrics.get("realized_pnl"),
        "total_trades": metrics.get("total"),
        "settled": metrics.get("settled"),
        "by_cohort": metrics.get("cohorts", []),
    }
    log.append(entry)
    # Keep last 100 entries
    _save_json(DRIFT_LOG, log[-100:])


def update_profit_ledger(settled: list[dict]) -> None:
    """Update running profit/loss ledger with new settlements."""
    ledger = _load_json(PROFIT_LEDGER, {"trades": [], "summary": {}})

    for trade in settled:
        entry = {
            "symbol": trade["symbol"],
            "settled_at": trade.get("settled_at") or datetime.now(timezone.utc).isoformat(),
            "settle_spot": trade.get("settle_spot"),
            "realized_pnl": trade.get("realized_pnl"),
            "realized_pnl_pct": trade.get("realized_pnl_pct"),
            "strategy": "unknown",  # Would need to join with positions table
        }
        ledger["trades"].append(entry)

    # Update summary
    pnls = [t["realized_pnl"] for t in ledger["trades"] if t.get("realized_pnl") is not None]
    ledger["summary"] = {
        "total_trades": len(pnls),
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p <= 0),
        "total_pnl": round(sum(pnls), 2),
        "win_rate_pct": round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 2) if pnls else 0,
        "avg_win": round(sum(p for p in pnls if p > 0) / max(1, sum(1 for p in pnls if p > 0)), 2),
        "avg_loss": round(sum(p for p in pnls if p <= 0) / max(1, sum(1 for p in pnls if p <= 0)), 2),
        "max_win": max(pnls) if pnls else 0,
        "max_loss": min(pnls) if pnls else 0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    _save_json(PROFIT_LEDGER, ledger)


def check_retrain_trigger() -> dict:
    """Check if model retraining is warranted based on drift metrics."""
    log = _load_json(DRIFT_LOG, [])
    if len(log) < 5:  # Need minimum samples
        return {"trigger": False, "reason": "insufficient drift history"}

    recent = log[-5:]
    win_rates = [r.get("win_rate_pct") for r in recent if r.get("win_rate_pct") is not None]

    if not win_rates:
        return {"trigger": False, "reason": "no win rate data"}

    current_avg = sum(win_rates) / len(win_rates)
    # Trigger if win rate drops below 45% or drops 10+ pct from peak
    peak = max(r.get("win_rate_pct", 0) for r in log if r.get("win_rate_pct"))

    triggered = False
    reasons = []

    if current_avg < 45:
        triggered = True
        reasons.append(f"current 5-trade avg win rate {current_avg:.1f}% below 45% threshold")

    if peak - current_avg >= 10:
        triggered = True
        reasons.append(f"win rate dropped {peak - current_avg:.1f} pct from peak {peak:.1f}%")

    return {"trigger": triggered, "reasons": reasons, "current_avg": current_avg, "peak": peak}


def retrain_model() -> dict:
    """Retrain model with all available data up to now."""
    from .model import train_earnings_models

    try:
        # Historical drift may request research, never replace a frozen active
        # cohort or grant promotion from a historical retest.
        result = train_earnings_models(save_artifacts=False)
        if "error" not in result:
            _save_json(RETRAIN_LOG, {
                "retrained_at": datetime.now(timezone.utc).isoformat(),
                "trigger": "auto_improve",
                "result_summary": {k: v for k, v in result.items() if k != "models"},
            })
            return {"success": True, "result": result}
        return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def archive_winners() -> dict:
    """Archive winning strategies/configurations for future reference."""
    ledger = _load_json(PROFIT_LEDGER, {"trades": []})
    winners = [t for t in ledger.get("trades", []) if t.get("realized_pnl", 0) > 0]

    archive = _load_json(WINNER_ARCHIVE, {"winners": []})

    for w in winners:
        # Avoid duplicates
        key = f"{w['symbol']}|{w.get('settled_at', '')[:10]}"
        if not any(a.get("symbol") == w["symbol"] and a.get("settled_at", "").startswith(w.get("settled_at", "")[:10]) for a in archive["winners"]):
            archive["winners"].append({
                "symbol": w["symbol"],
                "date": w.get("settled_at"),
                "pnl": w.get("realized_pnl"),
                "pnl_pct": w.get("realized_pnl_pct"),
                "pattern": "earnings_directional"  # Would enrich with strategy type
            })

    _save_json(WINNER_ARCHIVE, archive)
    return {"archived": len(archive["winners"])}


def run_auto_improve_cycle() -> dict:
    """Full auto-improvement cycle: settle, log, check drift, retrain if needed."""
    # 1. Settle any expired positions
    settle_result = settle_expired_positions()

    # 2. Get scorecard
    conn = sqlite3.connect("/home/aarav/Aarav/cipher/cipher-github/earnings_model/data/earnings.sqlite")
    conn.row_factory = sqlite3.Row
    scorecard = get_paper_scorecard(conn)
    conn.close()

    # 3. Log drift
    log_drift(scorecard)

    # 3. Update profit ledger
    if settle_result.get("settled"):
        update_profit_ledger(settle_result["settled"])

    # 4. Archive winners
    archive_winners()

    # 5. Check retrain trigger
    trigger_check = check_retrain_trigger()
    retrain_result = {"triggered": False}
    if trigger_check.get("trigger"):
        retrain_result = retrain_model()

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settled": settle_result,
        "scorecard": scorecard,
        "drift_check": trigger_check,
        "retrain": retrain_result,
    }


def get_profit_summary() -> dict:
    """Get current profit/loss summary for dashboard."""
    ledger = _load_json(PROFIT_LEDGER, {"trades": [], "summary": {}})
    archive = _load_json(WINNER_ARCHIVE, {"winners": []})
    drift = _load_json(DRIFT_LOG, [])

    return {
        "summary": ledger.get("summary", {}),
        "recent_trades": ledger.get("trades", [])[-20:],
        "winner_count": len(archive.get("winners", [])),
        "recent_drift": drift[-5:] if drift else [],
        "last_cycle": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        result = run_auto_improve_cycle()
        print(json.dumps(result, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "summary":
        print(json.dumps(get_profit_summary(), indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "retrain":
        print(json.dumps(retrain_model(), indent=2, default=str))
    else:
        print("usage: python -m earnings_model.auto_improve [run|summary|retrain]")
