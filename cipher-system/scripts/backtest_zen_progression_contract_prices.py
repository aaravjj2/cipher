#!/usr/bin/env python3
"""Backtest Zen alert/update progression with actual option bars."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_zen_watchlist_profitability import parse_text  # noqa: E402

UTC = timezone.utc


@dataclass(frozen=True)
class StageBacktest:
    key: str
    contract_symbol: str
    stage: str
    current_update_index: int
    next_update_reached: bool
    setup_alert_at: str
    current_update_at: str
    next_update_at: str | None
    expiration_end_at: str
    setup_entry_price: float | None
    current_update_market_price: float | None
    next_update_market_price: float | None
    posted_current_pct: float
    posted_next_pct: float | None
    market_current_pct_from_setup: float | None
    market_next_pct_from_setup: float | None
    realized_sell_now_pct_from_setup: float | None
    realized_hold_to_next_or_stop_pct_from_setup: float | None
    hold_to_next_incremental_pct: float | None
    min_price_until_next_or_expiry: float | None
    max_price_until_next_or_expiry: float | None
    stopped_minus_80_before_next: bool
    hit_plus_30_after_current: bool
    hit_plus_50_after_current: bool
    hit_plus_100_after_current: bool
    bars_until_next_or_expiry: int
    notes: str


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def nearest_mark(
    db: sqlite3.Connection,
    symbol: str,
    when: datetime,
    *,
    after: bool,
    tolerance_minutes: int = 20,
) -> tuple[float | None, str | None]:
    op = ">=" if after else "<="
    order = "asc" if after else "desc"
    rows = db.execute(
        f"""select timestamp, open, high, low, close from option_bars
            where symbol=? and timestamp {op} ?
            order by timestamp {order} limit 40""",
        (symbol, iso(when)),
    ).fetchall()
    best: tuple[float, str, float] | None = None
    for ts, open_, high, low, close in rows:
        mark = close if close not in (None, "") else high
        if mark in (None, ""):
            continue
        delta = abs((parse_dt(ts) - when.astimezone(UTC)).total_seconds()) / 60.0
        if delta > tolerance_minutes:
            continue
        item = (delta, ts, float(mark))
        if best is None or item[0] < best[0]:
            best = item
    if best is None:
        return None, None
    return best[2], best[1]


def bars_between(
    db: sqlite3.Connection,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[str, float, float, float]]:
    rows = db.execute(
        """select timestamp, high, low, close from option_bars
           where symbol=? and timestamp>=? and timestamp<=?
           order by timestamp""",
        (symbol, iso(start), iso(end)),
    ).fetchall()
    out = []
    for ts, high, low, close in rows:
        h = float(high if high is not None else close)
        l = float(low if low is not None else close)
        c = float(close if close is not None else high)
        out.append((ts, h, l, c))
    return out


def simulate_hold(
    bars: list[tuple[str, float, float, float]],
    *,
    setup_entry: float | None,
    current_price: float | None,
    next_price: float | None,
    has_next: bool,
    stop_loss: float,
) -> tuple[float | None, bool]:
    if setup_entry is None or current_price is None:
        return None, False
    stop_price = setup_entry * (1.0 - stop_loss)
    for _ts, _high, low, close in bars:
        if low <= stop_price:
            return pct(stop_price, setup_entry), True
    if has_next and next_price is not None:
        return pct(next_price, setup_entry), False
    if bars:
        return pct(bars[-1][3], setup_entry), False
    return pct(current_price, setup_entry), False


def expiration_end_from_key(key: str) -> datetime:
    expiration = datetime.fromisoformat(key.split()[1]).date()
    return datetime(expiration.year, expiration.month, expiration.day, 20, 15, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--data-root", default=str(ROOT / "data" / "historical_options" / "zen_watchlist_202607"))
    parser.add_argument("--stop-loss", type=float, default=0.80)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    db = sqlite3.connect(data_root / "historical_options.sqlite")
    report_rows = {
        row["key"]: row
        for row in csv.DictReader((data_root / "zen_watchlist_profitability_report.csv").open(newline="", encoding="utf-8"))
    }
    trades, updates = parse_text(Path(args.input))
    alerts = {trade.key: trade.alert_at for trade in trades}
    by_key: dict[str, list[Any]] = {}
    for update in updates:
        by_key.setdefault(update.key, []).append(update)
    for rows in by_key.values():
        rows.sort(key=lambda update: update.update_at)

    output: list[StageBacktest] = []
    for key, update_rows in sorted(by_key.items(), key=lambda item: alerts.get(item[0], item[1][0].update_at)):
        report = report_rows.get(key)
        if not report:
            continue
        symbol = report["contract_symbol"]
        alert_at = alerts.get(key)
        if alert_at is None:
            continue
        expiration_end = expiration_end_from_key(key)
        setup_entry, setup_entry_at = nearest_mark(db, symbol, alert_at, after=True, tolerance_minutes=45)
        for idx, current_update in enumerate(update_rows):
            next_update = update_rows[idx + 1] if idx + 1 < len(update_rows) else None
            current_price, current_price_at = nearest_mark(
                db,
                symbol,
                current_update.update_at,
                after=False,
                tolerance_minutes=45,
            )
            next_price = next_price_at = None
            if next_update is not None:
                next_price, next_price_at = nearest_mark(
                    db,
                    symbol,
                    next_update.update_at,
                    after=False,
                    tolerance_minutes=45,
                )
            stage_end = next_update.update_at if next_update is not None else expiration_end
            bars = bars_between(db, symbol, current_update.update_at, stage_end)
            lows = [row[2] for row in bars]
            highs = [row[1] for row in bars]
            hold_result, stopped = simulate_hold(
                bars,
                setup_entry=setup_entry,
                current_price=current_price,
                next_price=next_price,
                has_next=next_update is not None,
                stop_loss=float(args.stop_loss),
            )
            output.append(
                StageBacktest(
                    key=key,
                    contract_symbol=symbol,
                    stage=f"{idx + 1}_to_{idx + 2}" if next_update is not None else f"{idx + 1}_to_expiry",
                    current_update_index=idx + 1,
                    next_update_reached=next_update is not None,
                    setup_alert_at=alert_at.isoformat(),
                    current_update_at=current_update.update_at.isoformat(),
                    next_update_at=next_update.update_at.isoformat() if next_update else None,
                    expiration_end_at=iso(expiration_end),
                    setup_entry_price=setup_entry,
                    current_update_market_price=current_price,
                    next_update_market_price=next_price,
                    posted_current_pct=current_update.posted_pct,
                    posted_next_pct=next_update.posted_pct if next_update else None,
                    market_current_pct_from_setup=pct(current_price, setup_entry),
                    market_next_pct_from_setup=pct(next_price, setup_entry),
                    realized_sell_now_pct_from_setup=pct(current_price, setup_entry),
                    realized_hold_to_next_or_stop_pct_from_setup=hold_result,
                    hold_to_next_incremental_pct=pct(next_price, current_price) if next_update else None,
                    min_price_until_next_or_expiry=min(lows) if lows else None,
                    max_price_until_next_or_expiry=max(highs) if highs else None,
                    stopped_minus_80_before_next=stopped,
                    hit_plus_30_after_current=bool(current_price and any(row[1] >= current_price * 1.3 for row in bars)),
                    hit_plus_50_after_current=bool(current_price and any(row[1] >= current_price * 1.5 for row in bars)),
                    hit_plus_100_after_current=bool(current_price and any(row[1] >= current_price * 2.0 for row in bars)),
                    bars_until_next_or_expiry=len(bars),
                    notes=(
                        f"Market marks use nearest option bar within 45 minutes. "
                        f"Setup entry mark at {setup_entry_at}; current mark at {current_price_at}; next mark at {next_price_at}."
                    ),
                )
            )

    csv_path = data_root / "zen_progression_contract_price_backtest.csv"
    json_path = data_root / "zen_progression_contract_price_backtest_summary.json"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(output[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in output)

    def summarize(rows: list[StageBacktest]) -> dict[str, Any]:
        sell = [row.realized_sell_now_pct_from_setup for row in rows if row.realized_sell_now_pct_from_setup is not None]
        hold = [row.realized_hold_to_next_or_stop_pct_from_setup for row in rows if row.realized_hold_to_next_or_stop_pct_from_setup is not None]
        return {
            "n": len(rows),
            "next_update_reached": sum(row.next_update_reached for row in rows),
            "next_update_accuracy_pct": (sum(row.next_update_reached for row in rows) / len(rows) * 100.0) if rows else None,
            "sell_now_avg_market_pct_from_setup": sum(sell) / len(sell) if sell else None,
            "hold_next_or_stop_avg_market_pct_from_setup": sum(hold) / len(hold) if hold else None,
            "sell_now_median_market_pct_from_setup": sorted(sell)[len(sell) // 2] if sell else None,
            "hold_next_or_stop_median_market_pct_from_setup": sorted(hold)[len(hold) // 2] if hold else None,
            "stopped_minus_80_count": sum(row.stopped_minus_80_before_next for row in rows),
        }

    stage1 = [row for row in output if row.current_update_index == 1]
    stage2 = [row for row in output if row.current_update_index == 2]
    stage3 = [row for row in output if row.current_update_index == 3]
    payload = {
        "stop_loss_assumption_pct": -float(args.stop_loss) * 100.0,
        "all_stages": summarize(output),
        "after_first_update": summarize(stage1),
        "after_second_update": summarize(stage2),
        "after_third_update": summarize(stage3),
        "paths": {"csv": str(csv_path)},
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({**payload, "summary_json": str(json_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
