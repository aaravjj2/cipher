"""Research-only close-to-next-open stock and captured-option study.

Stocks use the adjusted regular-session close and the next regular-session
open. Options use the last observed trade-bar close from 15:55-15:59 ET and
the first observed trade-bar open from 09:30-09:34 ET. Alpaca does not provide
historical option NBBO here, so option results are observations, not fill claims.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, time, timezone
import json
import math
from pathlib import Path
import sqlite3
from statistics import median, stdev
from typing import Any, Iterable
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
CORE = Path(__file__).resolve().parent
SYSTEM = CORE.parent
DATA = SYSTEM / "data"
DEFAULT_OUTPUT = DATA / "overnight_close_open_lab"
PARQUETS = (
    DATA / "historical_equities/broad_research_panel_v1/normalized/alpaca_broad_daily_2010_2019_locked_validation_v1.parquet",
    DATA / "historical_equities/broad_research_panel_v1/normalized/alpaca_broad_daily_2020_2022_development_v1.parquet",
    DATA / "historical_equities/broad_2023_panel_v1/normalized/alpaca_broad_daily_2023_cross_universe_development_v1.parquet",
    DATA / "historical_equities/broad_2026_ytd_holdout_v1/normalized/alpaca_broad_daily_2024_2026_ytd_holdout_v1.parquet",
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _price(row: sqlite3.Row, *fields: str) -> float | None:
    for field in fields:
        value = row[field]
        if value is not None and float(value) > 0:
            return float(value)
    return None


def stock_trades(
    ticker: str,
    bars: Iterable[dict[str, Any]],
    *,
    cost_bps_per_side: float = 2.0,
) -> list[dict[str, Any]]:
    """Buy each adjusted daily close and sell at the next observed daily open."""
    ordered = sorted(
        (row for row in bars if float(row.get("close") or 0) > 0 and float(row.get("open") or 0) > 0),
        key=lambda row: str(row["date"]),
    )
    result: list[dict[str, Any]] = []
    round_trip_cost = cost_bps_per_side * 2 / 10_000
    for entry, exit_ in zip(ordered, ordered[1:]):
        close = float(entry["close"])
        open_ = float(exit_["open"])
        gross = open_ / close - 1
        result.append(
            {
                "ticker": ticker,
                "entry_date": str(entry["date"]),
                "exit_date": str(exit_["date"]),
                "entry_price": close,
                "exit_price": open_,
                "gross_return_pct": gross * 100,
                "net_return_pct": (gross - round_trip_cost) * 100,
            }
        )
    return result


def summarize(trades: Iterable[dict[str, Any]], key: str = "net_return_pct") -> dict[str, Any]:
    rows = list(trades)
    returns = [float(row[key]) for row in rows if row.get(key) is not None and math.isfinite(float(row[key]))]
    if not returns:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "average_return_pct": None,
            "median_return_pct": None,
            "fixed_notional_total_return_pct": None,
            "compounded_return_pct": None,
            "max_drawdown_pct": None,
            "profit_factor": None,
            "return_stddev_pct": None,
            "average_95ci_low_pct": None,
            "average_95ci_high_pct": None,
            "p05_return_pct": None,
            "p95_return_pct": None,
        }
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= max(0.0, 1 + value / 100)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1 if peak else -1)
    gains = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    wins = sum(value > 0 for value in returns)
    average = sum(returns) / len(returns)
    deviation = stdev(returns) if len(returns) > 1 else 0.0
    margin = 1.96 * deviation / math.sqrt(len(returns))
    ordered = sorted(returns)
    return {
        "trades": len(returns),
        "wins": wins,
        "losses": len(returns) - wins,
        "win_rate_pct": wins / len(returns) * 100,
        "average_return_pct": average,
        "median_return_pct": median(returns),
        "fixed_notional_total_return_pct": sum(returns),
        "compounded_return_pct": (equity - 1) * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "profit_factor": gains / losses if losses else None,
        "return_stddev_pct": deviation,
        "average_95ci_low_pct": average - margin,
        "average_95ci_high_pct": average + margin,
        "p05_return_pct": ordered[max(0, math.ceil(len(ordered) * 0.05) - 1)],
        "p95_return_pct": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
    }


def load_equity_daily() -> dict[str, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    bars: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in PARQUETS:
        if not path.exists():
            continue
        for row in pq.read_table(path, columns=["timestamp", "ticker", "open", "close"]).to_pylist():
            ticker = str(row["ticker"]).upper()
            day = str(row["timestamp"])[:10]
            bars[ticker][day] = {"date": day, "open": row["open"], "close": row["close"]}

    mu_db = DATA / "historical_equities/alpaca_mu_daily/equity_bars.sqlite"
    if mu_db.exists():
        with sqlite3.connect(f"file:{mu_db}?mode=ro", uri=True) as db:
            for ts, open_, close in db.execute(
                "select timestamp,open,close from bars where symbol='MU' and timeframe='1Day' order by timestamp"
            ):
                day = str(ts)[:10]
                bars["MU"][day] = {"date": day, "open": open_, "close": close}
    return {ticker: list(days.values()) for ticker, days in bars.items()}


def closing_minutes_validation(
    db_path: Path = DATA / "historical_bars.sqlite",
    *,
    cost_bps_per_side: float = 2.0,
) -> dict[str, Any]:
    """Validate the daily-close proxy against locally captured intraday bars."""
    if not db_path.exists():
        return {"status": "unavailable", "reason": "historical_bars_missing", "rankings": []}
    sessions: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        for ticker, stamp, open_, close in db.execute(
            "select symbol,timestamp,open,close from historical_bars order by symbol,timestamp"
        ):
            local = _dt(stamp).astimezone(NY)
            if local.weekday() >= 5:
                continue
            day = local.date().isoformat()
            clock = local.time().replace(tzinfo=None)
            if time(9, 30) <= clock < time(9, 35):
                prior = sessions[ticker][day].get("open")
                if prior is None or local < prior[0]:
                    sessions[ticker][day]["open"] = (local, float(open_))
            if time(15, 55) <= clock < time(16, 0):
                prior = sessions[ticker][day].get("close")
                if prior is None or local > prior[0]:
                    sessions[ticker][day]["close"] = (local, float(close))
    rankings = []
    for ticker, days in sessions.items():
        usable = [
            {"date": day, "open": values["open"][1], "close": values["close"][1]}
            for day, values in sorted(days.items())
            if "open" in values and "close" in values
        ]
        trades = stock_trades(ticker, usable, cost_bps_per_side=cost_bps_per_side)
        if trades:
            rankings.append({"ticker": ticker, **summarize(trades), "start": trades[0]["entry_date"], "end": trades[-1]["exit_date"]})
    rankings.sort(key=lambda row: (row["average_return_pct"], row["trades"]), reverse=True)
    return {"status": "ok", "entry_window_et": "15:55-15:59", "exit_window_et": "09:30-09:34", "rankings": rankings}


def _option_dataset_candidates(db_path: Path, max_dte: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inventory: dict[str, Any] = {"dataset": str(db_path.parent.relative_to(DATA)), "eligible_candidates": 0}
    candidates: list[dict[str, Any]] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
            if not {"contracts", "option_bars", "underlying_bars"}.issubset(tables):
                inventory.update(status="skipped", reason="required_tables_missing")
                return inventory, candidates
            contracts = {
                row["symbol"]: dict(row)
                for row in db.execute(
                    "select symbol,underlying,expiration_date,strike,option_type from contracts"
                )
            }
            sessions: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
            for row in db.execute(
                "select symbol,timestamp,open,close from underlying_bars where timeframe='1Min' order by timestamp"
            ):
                local = _dt(row["timestamp"]).astimezone(NY)
                if local.weekday() >= 5:
                    continue
                clock = local.time().replace(tzinfo=None)
                day = local.date().isoformat()
                if time(9, 30) <= clock < time(9, 35):
                    prior = sessions[row["symbol"]][day].get("open")
                    if prior is None or local < prior[0]:
                        sessions[row["symbol"]][day]["open"] = (local, float(row["open"]))
                if time(15, 55) <= clock < time(16, 0):
                    prior = sessions[row["symbol"]][day].get("close")
                    if prior is None or local > prior[0]:
                        sessions[row["symbol"]][day]["close"] = (local, float(row["close"]))
            target_stamps: set[str] = set()
            for days in sessions.values():
                for day in days:
                    local_day = date.fromisoformat(day)
                    for hour, minutes in ((15, range(55, 60)), (9, range(30, 35))):
                        for minute in minutes:
                            stamp = datetime.combine(local_day, time(hour, minute), NY).astimezone(UTC)
                            target_stamps.add(stamp.isoformat().replace("+00:00", "Z"))
            option_rows: list[sqlite3.Row] = []
            stamps = sorted(target_stamps)
            for offset in range(0, len(stamps), 500):
                chunk = stamps[offset : offset + 500]
                marks = ",".join("?" for _ in chunk)
                option_rows.extend(
                    db.execute(
                        f"select symbol,timestamp,open,close,volume from option_bars where timeframe='1Min' and timestamp in ({marks})",
                        chunk,
                    ).fetchall()
                )
    except sqlite3.DatabaseError as exc:
        inventory.update(status="skipped", reason=f"database_error:{type(exc).__name__}")
        return inventory, candidates

    marks: dict[str, dict[str, dict[str, tuple[datetime, float, float]]]] = defaultdict(lambda: defaultdict(dict))
    for row in option_rows:
        local = _dt(row["timestamp"]).astimezone(NY)
        clock = local.time().replace(tzinfo=None)
        day = local.date().isoformat()
        if time(15, 55) <= clock < time(16, 0):
            price = _price(row, "close", "open")
            side = "entry"
            better = lambda old: old is None or local > old[0]
        elif time(9, 30) <= clock < time(9, 35):
            price = _price(row, "open", "close")
            side = "exit"
            better = lambda old: old is None or local < old[0]
        else:
            continue
        if price is None:
            continue
        old = marks[row["symbol"]][day].get(side)
        if better(old):
            marks[row["symbol"]][day][side] = (local, price, float(row["volume"] or 0))

    for underlying, days in sessions.items():
        ordered_days = sorted(day for day, values in days.items() if "close" in values)
        next_open_days = sorted(day for day, values in days.items() if "open" in values)
        for entry_day in ordered_days:
            exit_day = next((day for day in next_open_days if day > entry_day), None)
            if exit_day is None:
                continue
            spot = float(days[entry_day]["close"][1])
            for symbol, meta in contracts.items():
                if meta["underlying"] != underlying or symbol not in marks:
                    continue
                entry_mark = marks[symbol].get(entry_day, {}).get("entry")
                exit_mark = marks[symbol].get(exit_day, {}).get("exit")
                if not entry_mark:
                    continue
                expiry = date.fromisoformat(str(meta["expiration_date"])[:10])
                dte = (expiry - date.fromisoformat(entry_day)).days
                if expiry < date.fromisoformat(exit_day) or not 1 <= dte <= max_dte:
                    continue
                entry_price = entry_mark[1]
                exit_price = exit_mark[1] if exit_mark else None
                if entry_price < 0.05:
                    continue
                candidates.append(
                    {
                        "underlying": underlying,
                        "option_type": str(meta["option_type"]).lower(),
                        "symbol": symbol,
                        "entry_date": entry_day,
                        "exit_date": exit_day,
                        "expiration": expiry.isoformat(),
                        "dte": dte,
                        "strike": float(meta["strike"]),
                        "underlying_close": spot,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "entry_volume": entry_mark[2],
                        "exit_volume": exit_mark[2] if exit_mark else None,
                        "source_dataset": inventory["dataset"],
                    }
                )
    status = "eligible" if candidates else (
        "no_intraday_underlying_session_bars" if not sessions else
        "no_overnight_eligible_contracts" if option_rows else
        "no_close_window_option_bars"
    )
    inventory.update(
        status=status,
        underlyings=sorted(sessions),
        option_bar_rows_in_windows=len(option_rows),
        eligible_candidates=len(candidates),
    )
    return inventory, candidates


def select_option_observations(all_candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select front-week ATM contracts using entry-time fields only."""
    # The same contract can exist in overlapping archives. Merging sources is
    # lineage cleanup, not contract selection; an exit observed in either archive
    # belongs to the already-selected symbol.
    by_symbol: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in all_candidates:
        key = (row["underlying"], row["entry_date"], row["option_type"], row["symbol"])
        prior = by_symbol.get(key)
        if prior is None or (prior.get("exit_price") is None and row.get("exit_price") is not None):
            by_symbol[key] = row

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in by_symbol.values():
        key = (row["underlying"], row["entry_date"], row["option_type"])
        # Every rank field is known at the closing-time decision.
        rank = (row["dte"], abs(row["strike"] - row["underlying_close"]), row["symbol"])
        prior = grouped.get(key)
        if prior is None or rank < prior["_rank"]:
            grouped[key] = {**row, "_rank": rank}
    selected = [{key: value for key, value in row.items() if key != "_rank"} for row in grouped.values()]
    missing = [row for row in selected if row.get("exit_price") is None]
    return selected, {
        "selected_at_close": len(selected),
        "observed_next_open": len(selected) - len(missing),
        "missing_next_open": len(missing),
        "missing_by_underlying": dict(sorted((ticker, sum(row["underlying"] == ticker for row in missing)) for ticker in {row["underlying"] for row in missing})),
    }


def captured_option_trades(
    root: Path = DATA / "historical_options",
    *,
    max_dte: int = 14,
    friction_pct_per_side: float = 2.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    for db_path in sorted(root.rglob("historical_options.sqlite")):
        row, candidates = _option_dataset_candidates(db_path, max_dte)
        inventory.append(row)
        all_candidates.extend(candidates)

    selected, selection_audit = select_option_observations(all_candidates)
    result = []
    friction = friction_pct_per_side / 100
    for row in selected:
        if row.get("exit_price") is None:
            continue
        entry = float(row["entry_price"])
        exit_ = float(row["exit_price"])
        gross = exit_ / entry - 1
        stressed = exit_ * (1 - friction) / (entry * (1 + friction)) - 1
        result.append(
            {
                **row,
                "gross_return_pct": gross * 100,
                "friction_adjusted_return_pct": max(-100.0, stressed * 100),
            }
        )
    result.sort(key=lambda row: (row["underlying"], row["entry_date"], row["option_type"]))
    return inventory, result, selection_audit


def run(
    output: Path = DEFAULT_OUTPUT,
    *,
    stock_cost_bps_per_side: float = 2.0,
    option_friction_pct_per_side: float = 2.5,
    max_option_dte: int = 14,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    equity = load_equity_daily()
    stock_rows: list[dict[str, Any]] = []
    stock_rankings = []
    for ticker, bars in sorted(equity.items()):
        trades = stock_trades(ticker, bars, cost_bps_per_side=stock_cost_bps_per_side)
        stock_rows.extend(trades)
        if trades:
            stock_rankings.append(
                {
                    "ticker": ticker,
                    **summarize(trades),
                    "start": trades[0]["entry_date"],
                    "end": trades[-1]["exit_date"],
                }
            )
    stock_rankings.sort(key=lambda row: (row["average_return_pct"], row["trades"]), reverse=True)
    stock_period_rankings = []
    periods = (("2016-2019", "2016-01-01", "2019-12-31"), ("2020-2022", "2020-01-01", "2022-12-31"), ("2023", "2023-01-01", "2023-12-31"), ("2024-2026", "2024-01-01", "2026-12-31"))
    for ticker in sorted(equity):
        ticker_rows = [row for row in stock_rows if row["ticker"] == ticker]
        for label, start, end in periods:
            rows = [row for row in ticker_rows if start <= row["entry_date"] <= end]
            if rows:
                stock_period_rankings.append({"ticker": ticker, "period": label, **summarize(rows)})
    stock_cost_sensitivity = []
    for cost in (0.0, 2.0, 5.0, 10.0):
        for ticker, bars in sorted(equity.items()):
            rows = stock_trades(ticker, bars, cost_bps_per_side=cost)
            stock_cost_sensitivity.append({"ticker": ticker, "cost_bps_per_side": cost, **summarize(rows)})

    inventory, option_rows, option_selection_audit = captured_option_trades(
        max_dte=max_option_dte,
        friction_pct_per_side=option_friction_pct_per_side,
    )
    option_rankings = []
    option_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in option_rows:
        option_groups[(row["underlying"], row["option_type"])].append(row)
    for (ticker, option_type), rows in option_groups.items():
        option_rankings.append(
            {
                "ticker": ticker,
                "option_type": option_type,
                **summarize(rows, "friction_adjusted_return_pct"),
                "gross": summarize(rows, "gross_return_pct"),
                "start": rows[0]["entry_date"],
                "end": rows[-1]["exit_date"],
            }
        )
    option_rankings.sort(key=lambda row: (row["average_return_pct"], row["trades"]), reverse=True)
    option_friction_sensitivity = []
    for friction_side in (0.0, 1.0, 2.5, 5.0):
        friction = friction_side / 100
        for (ticker, option_type), rows in sorted(option_groups.items()):
            stressed_rows = [
                {"return": max(-100.0, (float(row["exit_price"]) * (1 - friction) / (float(row["entry_price"]) * (1 + friction)) - 1) * 100)}
                for row in rows
            ]
            option_friction_sensitivity.append({"ticker": ticker, "option_type": option_type, "friction_pct_per_side": friction_side, **summarize(stressed_rows, "return")})

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "strategy": {
            "stocks": "buy adjusted official close; sell next observed regular-session open",
            "options": "buy front-week ATM call/put using last observed 15:55-15:59 ET trade-bar close; sell first observed 09:30-09:34 ET trade-bar open",
            "stock_cost_bps_per_side": stock_cost_bps_per_side,
            "option_friction_pct_per_side": option_friction_pct_per_side,
            "max_option_dte": max_option_dte,
            "sizing": "one equal-notional position per ticker/direction per session; returns are per-trade and not portfolio-overlap adjusted",
            "lookahead": "contract is selected from data observable at entry by nearest eligible expiration then nearest strike to underlying close",
        },
        "stock_rankings": stock_rankings,
        "stock_period_rankings": stock_period_rankings,
        "stock_cost_sensitivity": stock_cost_sensitivity,
        "closing_minutes_validation": closing_minutes_validation(cost_bps_per_side=stock_cost_bps_per_side),
        "option_rankings": option_rankings,
        "option_friction_sensitivity": option_friction_sensitivity,
        "option_dataset_inventory": inventory,
        "option_selection_audit": option_selection_audit,
        "availability": {
            "mu_stock": "available",
            "mu_options": "unavailable_no_historical_mu_option_bars",
            "option_underlyings_tested": sorted({row["underlying"] for row in option_rows}),
        },
        "caveats": [
            "Daily stock close/open bars do not prove fills at the auction print.",
            "Option bars are historical trade OHLCV, not NBBO bid/ask quotes; the friction adjustment is a stress assumption, not reconstructed slippage.",
            "Missing option bars are unknown and are not converted to zero-return trades.",
            "Repeated option archives are unioned and deduplicated to one front-week ATM observation per underlying/date/type.",
            "This is retrospective research, not prospective performance and not an execution recommendation.",
        ],
    }
    _write_outputs(output, report, stock_rows, option_rows)
    return report


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_outputs(output: Path, report: dict[str, Any], stock_rows: list[dict[str, Any]], option_rows: list[dict[str, Any]]) -> None:
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(output / "stock_trades.csv", stock_rows)
    _write_csv(output / "stock_rankings.csv", report["stock_rankings"])
    _write_csv(output / "stock_period_rankings.csv", report["stock_period_rankings"])
    _write_csv(output / "stock_cost_sensitivity.csv", report["stock_cost_sensitivity"])
    _write_csv(output / "option_trades.csv", option_rows)
    flat_option_rankings = [
        {key: value for key, value in row.items() if key != "gross"}
        | {f"gross_{key}": value for key, value in row["gross"].items()}
        for row in report["option_rankings"]
    ]
    _write_csv(output / "option_rankings.csv", flat_option_rankings)
    _write_csv(output / "option_friction_sensitivity.csv", report["option_friction_sensitivity"])
    mu = next((row for row in report["stock_rankings"] if row["ticker"] == "MU"), None)
    minute_mu = next((row for row in report["closing_minutes_validation"]["rankings"] if row["ticker"] == "MU"), None)
    lines = [
        "# Close-to-Next-Open Overnight Study",
        "",
        "Research only. Stock results use adjusted close/open bars; option results use captured trade bars, not historical NBBO.",
        "",
        "## MU",
        "",
        f"- Daily stock: {json.dumps(mu, sort_keys=True) if mu else 'unavailable'}",
        f"- Exact closing-minutes holdout: {json.dumps(minute_mu, sort_keys=True) if minute_mu else 'unavailable'}",
        "- Historical MU option bars: unavailable; no option P&L was inferred.",
        "",
        "## Stock ranking",
        "",
        "| Ticker | N | Win % | Avg net % | Total fixed-notional % | Max DD % | PF |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["stock_rankings"]:
        lines.append(f"| {row['ticker']} | {row['trades']} | {row['win_rate_pct']:.2f} | {row['average_return_pct']:.4f} | {row['fixed_notional_total_return_pct']:.2f} | {row['max_drawdown_pct']:.2f} | {row['profit_factor'] if row['profit_factor'] is not None else '∞'} |")
    lines.extend(["", "## Captured option ranking", "", "| Ticker | Type | N | Win % | Avg stressed % | Total fixed-notional % | Max DD % |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in report["option_rankings"]:
        lines.append(f"| {row['ticker']} | {row['option_type']} | {row['trades']} | {row['win_rate_pct']:.2f} | {row['average_return_pct']:.3f} | {row['fixed_notional_total_return_pct']:.2f} | {row['max_drawdown_pct']:.2f} |")
    lines.extend(["", "## Caveats", ""] + [f"- {item}" for item in report["caveats"]])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
