"""Point-in-time wall rejection research for selected underlyings.

This module deliberately separates the slow options-positioning state from the
fast price confirmation:

* the most recent captured call/put wall is carried forward only until the next
  capture (never backward-filled);
* an observed 5-minute candle must touch the wall and close back inside it;
* a hypothetical entry occurs at the next 5-minute open;
* bracket outcomes are evaluated conservatively (stop wins if stop and target
  are both touched in the same bar);
* at most one signal is retained per ticker/session/candidate.

The result is underlying-price research.  GEX remains a public-OI heuristic,
not verified dealer positioning, and this file has no broker/order capability.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .oi_niche_strategy_lab import DEFAULT_DB, STANDARD_CAVEAT, load_snapshot_panel


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "ticker_rejection_lab"
TARGET_TICKERS = ("MU", "TSLA")


@dataclass(frozen=True, slots=True)
class RejectionCandidate:
    approach_pct: float
    reclaim_pct: float
    impulse_pct: float
    minimum_wick_fraction: float
    filter_profile: str
    maximum_holding_bars: int
    reward_risk: float

    @property
    def candidate_id(self) -> str:
        return (
            f"wall_rejection|approach={self.approach_pct}|reclaim={self.reclaim_pct}"
            f"|impulse={self.impulse_pct}|wick={self.minimum_wick_fraction}"
            f"|filter={self.filter_profile}|hold={self.maximum_holding_bars}"
            f"|rr={self.reward_risk}"
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidate_id"] = self.candidate_id
        return value


def candidate_catalog() -> tuple[RejectionCandidate, ...]:
    """Fixed, outcome-independent filter catalog.

    Profiles are nested diagnostic hypotheses, not claims that more filters are
    automatically better.  The grid is intentionally compact enough to expose
    multiplicity rather than hiding it behind a single hand-picked result.
    """
    profiles = (
        "price_only",
        "positive_gex",
        "stable_wall",
        "crowded_wall",
        "vex_aligned",
        "relative_volume",
        "stacked",
    )
    return tuple(
        RejectionCandidate(*values)
        for values in itertools.product(
            (0.001, 0.0025, 0.005),       # touch can stop this far short
            (0.0, 0.001),                 # close back inside the wall
            (0.003, 0.006),               # prior move from the session open
            (0.35, 0.50),                 # rejection wick / candle range
            profiles,
            (6, 12, 78),                  # 30m, 60m, or through RTH close
            (1.0, 1.5, 2.0),
        )
    )


def _normalise_bars(rows: Iterable[Mapping[str, Any]], ticker: str) -> pd.DataFrame:
    renamed = []
    for row in rows:
        renamed.append({
            "ticker": ticker,
            "timestamp": row.get("time") or row.get("t") or row.get("timestamp"),
            "open": row.get("open", row.get("o")),
            "high": row.get("high", row.get("h")),
            "low": row.get("low", row.get("l")),
            "close": row.get("close", row.get("c")),
            "volume": row.get("volume", row.get("v")),
        })
    frame = pd.DataFrame(renamed)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame["date"] = frame["timestamp"].dt.strftime("%Y-%m-%d")
    frame["minute_utc"] = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    # US RTH in the captured July/August sample is 13:30-20:00 UTC. Alpaca bar
    # timestamps denote the beginning of each interval.
    frame = frame.loc[frame["minute_utc"].between(13 * 60 + 30, 19 * 60 + 55)].copy()
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    group = frame.groupby("date", sort=False)
    frame["session_open"] = group["open"].transform("first")
    prior_median = group["volume"].transform(lambda values: values.shift(1).rolling(20, min_periods=5).median())
    frame["relative_volume"] = frame["volume"] / prior_median.where(prior_median > 0)
    candle_range = (frame["high"] - frame["low"]).where(lambda values: values > 0)
    frame["upper_wick_fraction"] = (frame["high"] - frame[["open", "close"]].max(axis=1)) / candle_range
    frame["lower_wick_fraction"] = (frame[["open", "close"]].min(axis=1) - frame["low"]) / candle_range
    frame["bar_number"] = group.cumcount()
    frame["session_bars"] = group["close"].transform("size")
    return frame.reset_index(drop=True)


def prepare_panel(
    gex_panel: pd.DataFrame,
    bars_by_ticker: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    maximum_snapshot_age_minutes: int = 75,
) -> pd.DataFrame:
    """Join 5-minute bars to the latest already-known GEX/OI snapshot."""
    bar_frames = [_normalise_bars(rows, ticker.upper()) for ticker, rows in bars_by_ticker.items()]
    bars = pd.concat([frame for frame in bar_frames if not frame.empty], ignore_index=True)
    if bars.empty:
        raise RuntimeError("no usable regular-session 5-minute bars")
    feature_columns = [
        "snapshot_id", "ticker", "date", "timestamp", "call_wall_strike",
        "put_wall_strike", "gex_balance", "vex_balance", "near_oi_balance",
        "available_rate", "total_oi", "call_wall_move", "put_wall_move",
    ]
    features = gex_panel.loc[gex_panel["ticker"].isin(bars["ticker"].unique()), feature_columns].copy()
    features = features.rename(columns={"timestamp": "snapshot_timestamp"})
    joined: list[pd.DataFrame] = []
    for ticker in sorted(bars["ticker"].unique()):
        left = bars.loc[bars["ticker"] == ticker].sort_values("timestamp")
        right = features.loc[features["ticker"] == ticker].sort_values("snapshot_timestamp")
        if right.empty:
            continue
        merged = pd.merge_asof(
            left.drop(columns=["ticker"]),
            right.drop(columns=["ticker", "date"]),
            left_on="timestamp",
            right_on="snapshot_timestamp",
            direction="backward",
            tolerance=pd.Timedelta(minutes=maximum_snapshot_age_minutes),
        )
        merged["ticker"] = ticker
        # Never let Friday's or yesterday's wall leak into a new session.
        merged = merged.loc[
            merged["snapshot_timestamp"].notna()
            & (merged["snapshot_timestamp"].dt.strftime("%Y-%m-%d") == merged["date"])
        ]
        joined.append(merged)
    if not joined:
        raise RuntimeError("no bars matched a same-session point-in-time wall snapshot")
    return pd.concat(joined, ignore_index=True).sort_values(["ticker", "timestamp"]).reset_index(drop=True)


def _profile_mask(frame: pd.DataFrame, direction: pd.Series, profile: str) -> pd.Series:
    base = pd.Series(True, index=frame.index)
    positive_gex = frame["gex_balance"] >= 0.25
    stable = pd.Series(
        np.where(direction < 0, frame["call_wall_move"].abs(), frame["put_wall_move"].abs()),
        index=frame.index,
    ) <= 0.0025
    crowded = direction * frame["near_oi_balance"] <= -0.25
    vex = direction * frame["vex_balance"] >= 0.10
    volume = frame["relative_volume"] >= 1.20
    if profile == "price_only":
        return base
    if profile == "positive_gex":
        return positive_gex
    if profile == "stable_wall":
        return positive_gex & stable
    if profile == "crowded_wall":
        return positive_gex & crowded
    if profile == "vex_aligned":
        return positive_gex & vex
    if profile == "relative_volume":
        return positive_gex & volume
    if profile == "stacked":
        return positive_gex & stable & crowded & vex & volume
    raise ValueError(f"unknown filter profile: {profile}")


def signal_trades(panel: pd.DataFrame, candidate: RejectionCandidate) -> pd.DataFrame:
    """Create causal next-bar bracket simulations for one candidate."""
    usable = panel.loc[
        (panel["available_rate"] >= 0.60)
        & (panel["total_oi"] >= 1_000)
        & panel["minute_utc"].between(14 * 60, 18 * 60 + 30)
    ].copy()
    wall_call = usable["call_wall_strike"]
    wall_put = usable["put_wall_strike"]
    call = (
        (usable["high"] >= wall_call * (1.0 - candidate.approach_pct))
        & (usable["high"] <= wall_call * 1.0075)
        & (usable["close"] <= wall_call * (1.0 - candidate.reclaim_pct))
        & (usable["close"] < usable["open"])
        & (usable["high"] / usable["session_open"] - 1.0 >= candidate.impulse_pct)
        & (usable["upper_wick_fraction"] >= candidate.minimum_wick_fraction)
    )
    put = (
        (usable["low"] <= wall_put * (1.0 + candidate.approach_pct))
        & (usable["low"] >= wall_put * 0.9925)
        & (usable["close"] >= wall_put * (1.0 + candidate.reclaim_pct))
        & (usable["close"] > usable["open"])
        & (usable["low"] / usable["session_open"] - 1.0 <= -candidate.impulse_pct)
        & (usable["lower_wick_fraction"] >= candidate.minimum_wick_fraction)
    )
    direction = pd.Series(np.where(call, -1, np.where(put, 1, 0)), index=usable.index)
    mask = (call | put) & _profile_mask(usable, direction, candidate.filter_profile)
    selected = usable.loc[mask].copy()
    if selected.empty:
        return selected
    selected["direction"] = direction.loc[selected.index].astype(int)
    selected["setup"] = np.where(selected["direction"] < 0, "call_wall_rejection", "put_wall_bounce")
    selected = selected.sort_values("timestamp").groupby(["ticker", "date"], as_index=False).first()

    records: list[dict[str, Any]] = []
    lookup = panel.set_index(["ticker", "date"])
    for signal in selected.itertuples(index=False):
        session = lookup.loc[(signal.ticker, signal.date)]
        if isinstance(session, pd.Series):
            continue
        session = session.sort_values("timestamp").reset_index(drop=True)
        indices = session.index[session["timestamp"] == signal.timestamp].tolist()
        if not indices or indices[0] + 1 >= len(session):
            continue
        signal_index = indices[0]
        entry_index = signal_index + 1
        entry = float(session.loc[entry_index, "open"])
        direction_value = int(signal.direction)
        wall = float(signal.call_wall_strike if direction_value < 0 else signal.put_wall_strike)
        stop = wall * (1.0025 if direction_value < 0 else 0.9975)
        risk = direction_value * (entry - stop)
        if not math.isfinite(risk) or risk <= 0:
            continue
        target = entry + direction_value * candidate.reward_risk * risk
        final_index = min(len(session) - 1, signal_index + candidate.maximum_holding_bars)
        exit_price = float(session.loc[final_index, "close"])
        exit_reason = "time"
        exit_index = final_index
        for index in range(entry_index, final_index + 1):
            row = session.loc[index]
            stop_hit = row["high"] >= stop if direction_value < 0 else row["low"] <= stop
            target_hit = row["low"] <= target if direction_value < 0 else row["high"] >= target
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", index
                break
            if target_hit:
                exit_price, exit_reason, exit_index = target, "target", index
                break
        payload = signal._asdict()
        payload.update({
            "candidate_id": candidate.candidate_id,
            "signal_timestamp": pd.Timestamp(signal.timestamp),
            "entry_timestamp": pd.Timestamp(session.loc[entry_index, "timestamp"]),
            "exit_timestamp": pd.Timestamp(session.loc[exit_index, "timestamp"]),
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "gross_return": direction_value * (exit_price / entry - 1.0),
        })
        records.append(payload)
    return pd.DataFrame(records)


def _profit_factor(values: np.ndarray) -> float | None:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 1e-12:
        return 999.0 if gains > 0 else None
    return float(gains / losses)


def summarize(trades: pd.DataFrame, *, cost_bps_per_side: float = 10.0) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "days": 0}
    returns = trades["gross_return"].to_numpy(float) - 2 * cost_bps_per_side / 10_000
    return {
        "trades": int(len(trades)),
        "days": int(trades["date"].nunique()),
        "longs": int((trades["direction"] > 0).sum()),
        "shorts": int((trades["direction"] < 0).sum()),
        "wins": int((returns > 0).sum()),
        "win_rate": float(np.mean(returns > 0)),
        "mean_return_pct": float(np.mean(returns) * 100),
        "median_return_pct": float(np.median(returns) * 100),
        "compound_return_pct": float((np.prod(1 + returns) - 1) * 100),
        "profit_factor": _profit_factor(returns),
        "targets": int((trades["exit_reason"] == "target").sum()),
        "stops": int((trades["exit_reason"] == "stop").sum()),
        "time_exits": int((trades["exit_reason"] == "time").sum()),
        "worst_trade_pct": float(returns.min() * 100),
        "best_trade_pct": float(returns.max() * 100),
    }


def chronological_partitions(panel: pd.DataFrame) -> dict[str, list[str]]:
    dates = sorted(day for day in panel["date"].unique() if day >= "2026-07-27")
    if len(dates) < 15:
        raise RuntimeError("15 complete sessions are required for fixed 5/5/5 partitions")
    return {"discovery": dates[:5], "validation": dates[5:10], "audit": dates[10:15]}


def _json_value(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def run_lab(
    bars_by_ticker: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    db_path: str | Path = DEFAULT_DB,
    output_directory: str | Path = DEFAULT_OUTPUT,
    bar_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    gex = load_snapshot_panel(db_path)
    panel = prepare_panel(gex, bars_by_ticker)
    partitions = chronological_partitions(panel)
    complete_dates = {day for dates in partitions.values() for day in dates}
    # July 23 has only two GEX observations and is excluded from every headline
    # statistic, not merely from the chronological blocks.
    panel = panel.loc[panel["date"].isin(complete_dates)].copy()
    catalog = candidate_catalog()
    rows: list[dict[str, Any]] = []
    trade_map: dict[str, pd.DataFrame] = {}
    signature_owner: dict[tuple[Any, ...], str] = {}
    for candidate in catalog:
        trades = signal_trades(panel, candidate)
        trade_map[candidate.candidate_id] = trades
        signature = tuple(
            (
                str(row.ticker), str(row.signal_timestamp), int(row.direction),
                str(row.exit_timestamp), round(float(row.exit_price), 6),
            )
            for row in trades.itertuples(index=False)
        )
        owner = signature_owner.setdefault(signature, candidate.candidate_id)
        result: dict[str, Any] = {
            "candidate": candidate.to_dict(),
            "signal_path_owner": owner,
            "all": summarize(trades),
            "by_ticker": {},
        }
        for ticker in sorted(bars_by_ticker):
            ticker_trades = trades.loc[trades["ticker"] == ticker] if not trades.empty else trades
            result["by_ticker"][ticker] = {
                "all": summarize(ticker_trades),
                **{
                    name: summarize(ticker_trades.loc[ticker_trades["date"].isin(dates)])
                    for name, dates in partitions.items()
                },
            }
        result.update({
            name: summarize(trades.loc[trades["date"].isin(dates)])
            for name, dates in partitions.items()
        })
        rows.append(result)

    unique_rows = [row for row in rows if row["signal_path_owner"] == row["candidate"]["candidate_id"]]
    # This gate is deliberately demanding relative to the tiny sample. It is
    # expected to return no winner until more sessions have accumulated.
    strict = [
        row for row in unique_rows
        if all(int(row[name].get("days") or 0) >= 3 for name in partitions)
        and all(float(row[name].get("mean_return_pct") or -999) > 0 for name in partitions)
        and all(float(row[name].get("profit_factor") or 0) >= 1.10 for name in partitions)
        and float(summarize(trade_map[row["candidate"]["candidate_id"]], cost_bps_per_side=25).get("mean_return_pct") or -999) > 0
    ]
    strict.sort(key=lambda row: min(row[name]["mean_return_pct"] for name in partitions), reverse=True)
    descriptive = [row for row in unique_rows if int(row["all"].get("trades") or 0) >= 4]
    descriptive.sort(key=lambda row: float(row["all"].get("mean_return_pct") or -999), reverse=True)
    top = descriptive[:20]
    ticker_leaders: dict[str, list[dict[str, Any]]] = {}
    for ticker in sorted(bars_by_ticker):
        eligible = [
            row for row in unique_rows
            if int(row["by_ticker"][ticker]["all"].get("trades") or 0) >= 4
        ]
        eligible.sort(
            key=lambda row: float(row["by_ticker"][ticker]["all"].get("mean_return_pct") or -999),
            reverse=True,
        )
        ticker_leaders[ticker] = eligible[:10]
    selected_ids = {row["candidate"]["candidate_id"] for row in top}
    selected_ids.update(
        row["candidate"]["candidate_id"]
        for ticker_rows in ticker_leaders.values()
        for row in ticker_rows
    )
    trade_records = {
        candidate_id: [
            {key: _json_value(value) for key, value in record.items()}
            for record in trade_map[candidate_id].to_dict("records")
        ]
        for candidate_id in selected_ids
    }
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "question": "Do confirmed call/put wall rejection reversals survive ticker-specific testing on MU and TSLA?",
        "as_of": str(panel["timestamp"].max()),
        "status": "STRICT_PASS" if strict else "NO_STRICT_PASS",
        "strict_leader": strict[0] if strict else None,
        "descriptive_leaders": top,
        "ticker_descriptive_leaders": ticker_leaders,
        "descriptive_trade_records": trade_records,
        "data": {
            "gex_database": str(Path(db_path).resolve()),
            "bar_sources": dict(bar_sources or {}),
            "tickers": sorted(bars_by_ticker),
            "matched_5m_bars": int(len(panel)),
            "sessions": int(panel["date"].nunique()),
            "partitions": partitions,
            "snapshot_max_age_minutes": 75,
        },
        "protocol": {
            "catalog_size": len(catalog),
            "unique_signal_paths": len(signature_owner),
            "entry": "next 5-minute bar open after touch-and-reclaim confirmation",
            "stop": "wall plus/minus 0.25%; conservative stop-first same-bar ambiguity",
            "costs": "20 bps round trip in headline metrics; strict gate also stresses 50 bps",
            "one_signal_per_ticker_day_candidate": True,
            "selection": "fixed 5/5/5 chronology; strict leader must be positive with PF>=1.10 and >=3 days in every block",
        },
        "risks_and_missing_evidence": [
            "Only 15 complete sessions exist, so ticker-level inference is severely underpowered.",
            "Many parameter combinations create identical signal paths; unique paths are counted explicitly.",
            "Hourly-ish GEX captures mean a wall can change between captures; joins expire after 75 minutes.",
            "Open interest is lagged public positioning and does not identify dealer direction.",
            "Underlying returns do not establish executable option P/L; observed bid/ask confirmation is separate.",
            "The audit block is historical evidence inspected during research, not pristine future evidence.",
        ],
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "caveat": STANDARD_CAVEAT,
    }
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (output / "latest_ticker_rejection_report.json").write_text(encoded, encoding="utf-8")
    (output / f"ticker_rejection_report_{now.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(encoded, encoding="utf-8")
    return payload
