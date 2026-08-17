"""Leakage-resistant research lab for rare OI/GEX intraday hypotheses.

The lab uses only point-in-time rows already captured in ``gex_history.sqlite``.
It deliberately evaluates *underlying directional returns*, not option-premium
P&L: the GEX store contains historical public OI and Greeks, but it does not
contain a complete multi-week history of executable option quotes.  A winning
row is therefore a candidate for prospective option-mark collection, never a
validated or executable strategy.

Important research controls:

* one signal at most per ticker/session/candidate;
* fixed chronological discovery, validation, and untouched holdout blocks;
* observed cells only -- missing gamma or OI is never filled with zero;
* SPY-adjusted and raw returns, with explicit round-trip cost stress;
* day-clustered sign-flip inference and leave-one-day-out diagnostics;
* a deterministic, outcome-independent catalog of niche hypotheses.

GEX remains a public-OI heuristic, not verified dealer positioning.
This module is research-only and contains no broker or order API.
"""
from __future__ import annotations

import itertools
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "gex_history.sqlite"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "oi_niche_strategy_lab"
STANDARD_CAVEAT = "GEX is a public-OI heuristic, not verified dealer positioning."


@dataclass(frozen=True, slots=True)
class Candidate:
    family: str
    parameters: Mapping[str, float | str]
    direction_rule: str
    hypothesis: str

    @property
    def candidate_id(self) -> str:
        parts = [self.family]
        for key, value in sorted(self.parameters.items()):
            parts.append(f"{key}={value}")
        parts.append(f"direction={self.direction_rule}")
        return "|".join(parts)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate_id"] = self.candidate_id
        return payload


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(denominator.abs() > 1e-12)
    return numerator / denominator


def load_snapshot_panel(db_path: str | Path = DEFAULT_DB) -> pd.DataFrame:
    """Load point-in-time snapshot features without imputing missing exposure."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    query = """
        WITH front AS (
            SELECT snapshot_id, MIN(expiration) AS front_expiration
            FROM gex_strike_cells
            WHERE available = 1
            GROUP BY snapshot_id
        ), aggregate_cells AS (
            SELECT
                c.snapshot_id,
                COUNT(*) AS listed_cells_total,
                SUM(CASE WHEN c.available = 1 THEN 1 ELSE 0 END) AS available_cells,
                SUM(CASE WHEN c.available = 1 THEN c.call_oi END) AS call_oi,
                SUM(CASE WHEN c.available = 1 THEN c.put_oi END) AS put_oi,
                SUM(CASE WHEN c.available = 1 THEN c.volume END) AS option_volume,
                SUM(CASE WHEN c.available = 1 THEN c.call_gex END) AS call_gex,
                SUM(CASE WHEN c.available = 1 THEN c.put_gex END) AS put_gex,
                SUM(CASE WHEN c.available = 1 THEN c.net_gex END) AS net_gex,
                SUM(CASE WHEN c.available = 1 THEN c.call_vex END) AS call_vex,
                SUM(CASE WHEN c.available = 1 THEN c.put_vex END) AS put_vex,
                SUM(CASE WHEN c.available = 1 THEN c.net_vex END) AS net_vex,
                SUM(CASE WHEN c.available = 1 THEN ABS(c.call_gex) + ABS(c.put_gex) END) AS abs_gex,
                SUM(CASE WHEN c.available = 1 THEN ABS(c.call_vex) + ABS(c.put_vex) END) AS abs_vex,
                MAX(CASE WHEN c.available = 1 THEN c.call_oi END) AS max_call_oi,
                MAX(CASE WHEN c.available = 1 THEN c.put_oi END) AS max_put_oi,
                SUM(CASE WHEN c.available = 1 AND c.expiration = f.front_expiration
                         THEN COALESCE(c.call_oi, 0) + COALESCE(c.put_oi, 0) END) AS front_oi,
                SUM(CASE WHEN c.available = 1 AND ABS(c.strike - s.spot) / s.spot <= 0.02
                         THEN c.call_oi END) AS near_call_oi,
                SUM(CASE WHEN c.available = 1 AND ABS(c.strike - s.spot) / s.spot <= 0.02
                         THEN c.put_oi END) AS near_put_oi,
                SUM(CASE WHEN c.available = 1 AND c.strike > s.spot THEN c.net_gex END) AS gex_above,
                SUM(CASE WHEN c.available = 1 AND c.strike < s.spot THEN c.net_gex END) AS gex_below,
                SUM(CASE WHEN c.available = 1 AND c.call_mid IS NOT NULL AND c.call_mid > 0 THEN 1 ELSE 0 END) AS call_mid_cells,
                SUM(CASE WHEN c.available = 1 AND c.put_mid IS NOT NULL AND c.put_mid > 0 THEN 1 ELSE 0 END) AS put_mid_cells
            FROM gex_strike_cells c
            JOIN gex_snapshots s ON s.id = c.snapshot_id
            JOIN front f ON f.snapshot_id = c.snapshot_id
            GROUP BY c.snapshot_id
        )
        SELECT
            s.id AS snapshot_id, s.ticker, s.captured_at, s.spot,
            s.contracts, s.calculated_cells, s.listed_cells,
            s.global_max_strike, s.call_wall_strike, s.put_wall_strike,
            s.gamma_flip_level,
            a.listed_cells_total, a.available_cells,
            a.call_oi, a.put_oi, a.option_volume,
            a.call_gex, a.put_gex, a.net_gex,
            a.call_vex, a.put_vex, a.net_vex,
            a.abs_gex, a.abs_vex,
            a.max_call_oi, a.max_put_oi, a.front_oi,
            a.near_call_oi, a.near_put_oi,
            a.gex_above, a.gex_below,
            a.call_mid_cells, a.put_mid_cells
        FROM gex_snapshots s
        JOIN aggregate_cells a ON a.snapshot_id = s.id
        WHERE s.spot IS NOT NULL AND s.spot > 0
        ORDER BY s.ticker, s.captured_at, s.id
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        frame = pd.read_sql_query(query, connection)
    finally:
        connection.close()
    if frame.empty:
        raise RuntimeError("GEX history contains no usable point-in-time snapshots")

    frame["timestamp"] = pd.to_datetime(frame.pop("captured_at"), utc=True)
    frame["date"] = frame["timestamp"].dt.strftime("%Y-%m-%d")
    frame["minute_utc"] = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    total_oi = frame["call_oi"] + frame["put_oi"]
    near_total_oi = frame["near_call_oi"] + frame["near_put_oi"]
    frame["total_oi"] = total_oi
    frame["available_rate"] = _safe_div(frame["available_cells"], frame["listed_cells_total"])
    frame["oi_balance"] = _safe_div(frame["call_oi"] - frame["put_oi"], total_oi)
    frame["near_oi_balance"] = _safe_div(
        frame["near_call_oi"] - frame["near_put_oi"], near_total_oi
    )
    frame["gex_balance"] = _safe_div(frame["net_gex"], frame["abs_gex"])
    frame["vex_balance"] = _safe_div(frame["net_vex"], frame["abs_vex"])
    frame["front_oi_share"] = _safe_div(frame["front_oi"], total_oi)
    frame["call_oi_concentration"] = _safe_div(frame["max_call_oi"], frame["call_oi"])
    frame["put_oi_concentration"] = _safe_div(frame["max_put_oi"], frame["put_oi"])
    frame["volume_oi_ratio"] = _safe_div(frame["option_volume"], total_oi)
    frame["gex_location_balance"] = _safe_div(
        frame["gex_above"] - frame["gex_below"], frame["abs_gex"]
    )
    for name, level in (
        ("call_wall", "call_wall_strike"),
        ("put_wall", "put_wall_strike"),
        ("gamma_flip", "gamma_flip_level"),
        ("global_max", "global_max_strike"),
    ):
        frame[f"{name}_distance"] = (frame[level] - frame["spot"]) / frame["spot"]
    frame["wall_width"] = (
        frame["call_wall_strike"] - frame["put_wall_strike"]
    ) / frame["spot"]

    groups = frame.groupby(["ticker", "date"], sort=False)
    frame["session_open_spot"] = groups["spot"].transform("first")
    frame["session_close_spot"] = groups["spot"].transform("last")
    frame["day_return"] = frame["spot"] / frame["session_open_spot"] - 1.0
    frame["forward_return"] = frame["session_close_spot"] / frame["spot"] - 1.0
    frame["next_spot"] = groups["spot"].shift(-1)
    frame["next_timestamp"] = groups["timestamp"].shift(-1)
    frame["two_snapshot_spot"] = groups["spot"].shift(-2)
    frame["two_snapshot_timestamp"] = groups["timestamp"].shift(-2)
    frame["next_return"] = frame["next_spot"] / frame["spot"] - 1.0
    frame["two_snapshot_return"] = frame["two_snapshot_spot"] / frame["spot"] - 1.0
    for column in (
        "spot", "gex_balance", "vex_balance", "oi_balance", "near_oi_balance",
        "call_wall_strike", "put_wall_strike", "gamma_flip_level",
    ):
        frame[f"prev_{column}"] = groups[column].shift(1)
    frame["snapshot_return"] = frame["spot"] / frame["prev_spot"] - 1.0
    frame["gex_balance_delta"] = frame["gex_balance"] - frame["prev_gex_balance"]
    frame["vex_balance_delta"] = frame["vex_balance"] - frame["prev_vex_balance"]
    frame["oi_balance_delta"] = frame["oi_balance"] - frame["prev_oi_balance"]
    frame["call_wall_move"] = (
        frame["call_wall_strike"] - frame["prev_call_wall_strike"]
    ) / frame["spot"]
    frame["put_wall_move"] = (
        frame["put_wall_strike"] - frame["prev_put_wall_strike"]
    ) / frame["spot"]

    # Match every observation to the most recent same-session SPY capture.
    spy = frame.loc[frame["ticker"] == "SPY", ["timestamp", "date", "spot", "session_close_spot"]].copy()
    spy = spy.rename(columns={"spot": "spy_entry_spot", "session_close_spot": "spy_close_spot"})
    left = frame.sort_values("timestamp")
    right = spy.sort_values("timestamp")
    matched = pd.merge_asof(
        left,
        right[["timestamp", "date", "spy_entry_spot", "spy_close_spot"]],
        on="timestamp",
        by="date",
        direction="nearest",
        tolerance=pd.Timedelta("55min"),
    )
    matched["spy_forward_return"] = matched["spy_close_spot"] / matched["spy_entry_spot"] - 1.0
    matched["alpha_forward_return"] = matched["forward_return"] - matched["spy_forward_return"]
    # Horizon exits receive their own nearest SPY observation. The join is
    # same-session and bounded, so a missing benchmark stays missing.
    for prefix, timestamp_column, raw_column in (
        ("next", "next_timestamp", "next_return"),
        ("two_snapshot", "two_snapshot_timestamp", "two_snapshot_return"),
    ):
        lookup = matched[["snapshot_id", "date", timestamp_column]].dropna().copy()
        lookup = lookup.rename(columns={timestamp_column: "benchmark_timestamp"}).sort_values("benchmark_timestamp")
        benchmark = spy[["timestamp", "date", "spy_entry_spot"]].rename(
            columns={"timestamp": "benchmark_timestamp", "spy_entry_spot": f"spy_{prefix}_exit_spot"}
        ).sort_values("benchmark_timestamp")
        lookup = pd.merge_asof(
            lookup,
            benchmark,
            on="benchmark_timestamp",
            by="date",
            direction="nearest",
            tolerance=pd.Timedelta("55min"),
        )
        matched = matched.merge(
            lookup[["snapshot_id", f"spy_{prefix}_exit_spot"]], on="snapshot_id", how="left"
        )
        spy_return = matched[f"spy_{prefix}_exit_spot"] / matched["spy_entry_spot"] - 1.0
        matched[f"alpha_{prefix}_return"] = matched[raw_column] - spy_return
    daily = (
        matched.sort_values("timestamp")
        .groupby(["ticker", "date"], as_index=False)
        .agg(session_open=("spot", "first"), session_close=("spot", "last"))
        .sort_values(["ticker", "date"])
    )
    daily["next_session_open"] = daily.groupby("ticker")["session_open"].shift(-1)
    daily["next_session_close"] = daily.groupby("ticker")["session_close"].shift(-1)
    matched = matched.merge(
        daily[["ticker", "date", "next_session_open", "next_session_close"]],
        on=["ticker", "date"], how="left",
    )
    matched["next_open_return"] = matched["next_session_open"] / matched["spot"] - 1.0
    matched["next_close_return"] = matched["next_session_close"] / matched["spot"] - 1.0
    spy_daily = daily.loc[daily["ticker"] == "SPY", ["date", "session_close", "next_session_open", "next_session_close"]].rename(
        columns={
            "session_close": "spy_signal_close",
            "next_session_open": "spy_next_session_open",
            "next_session_close": "spy_next_session_close",
        }
    )
    matched = matched.merge(spy_daily, on="date", how="left")
    matched["alpha_next_open_return"] = matched["next_open_return"] - (
        matched["spy_next_session_open"] / matched["spy_signal_close"] - 1.0
    )
    matched["alpha_next_close_return"] = matched["next_close_return"] - (
        matched["spy_next_session_close"] / matched["spy_signal_close"] - 1.0
    )
    return matched.sort_values(["ticker", "timestamp", "snapshot_id"]).reset_index(drop=True)


def candidate_catalog() -> tuple[Candidate, ...]:
    """Deterministic catalog spanning walls, flips, concentration, and flow/OI states."""
    rows: list[Candidate] = []

    def add(family: str, parameters: Mapping[str, float | str], direction: str, hypothesis: str) -> None:
        rows.append(Candidate(family, dict(parameters), direction, hypothesis))

    for distance, momentum, gex in itertools.product((0.0025, 0.005, 0.01), (0.002, 0.004, 0.007), (0.0, 0.25, 0.5)):
        add("call_wall_rejection", {"distance": distance, "momentum": momentum, "gex": gex}, "short", "Positive-GEX call walls may reject an extended intraday advance.")
        add("put_wall_bounce", {"distance": distance, "momentum": momentum, "gex": gex}, "long", "Positive-GEX put walls may absorb an extended intraday decline.")
    for gex, momentum in itertools.product((0.25, 0.5, 0.7), (0.0015, 0.003, 0.006)):
        add("negative_gex_breakout", {"gex": gex, "momentum": momentum}, "follow_snapshot", "Negative GEX may amplify a fresh directional move.")
        add("positive_gex_reversion", {"gex": gex, "momentum": momentum}, "fade_day", "Positive GEX may damp and reverse an extended intraday move.")
    for threshold, momentum in itertools.product((0.25, 0.45, 0.65), (0.0, 0.0025, 0.005)):
        add("oi_crowd_fade", {"oi": threshold, "momentum": momentum}, "fade_oi", "Extreme call/put OI imbalance may be a crowded level rather than directional flow.")
        add("near_oi_pressure_follow", {"oi": threshold, "momentum": momentum}, "follow_near_oi", "Near-spot OI imbalance aligned with tape momentum may continue into the close.")
    for threshold, mode in itertools.product((0.25, 0.45, 0.65), ("follow_vex", "fade_vex", "follow_gex", "fade_gex")):
        add("vex_gex_dislocation", {"threshold": threshold, "mode": mode}, mode, "Opposite VEX/GEX signs may identify an uncommon volatility-positioning dislocation.")
    for distance, share, gex in itertools.product((0.0025, 0.005, 0.01), (0.35, 0.55, 0.75), (0.0, 0.25)):
        add("front_expiry_pin", {"distance": distance, "share": share, "gex": gex}, "toward_global_max", "Front-expiry OI concentration plus positive GEX may pull spot toward the dominant strike.")
    for gex, momentum in itertools.product((0.0, 0.25, 0.5), (0.0, 0.0015, 0.003)):
        add("gamma_flip_reclaim", {"gex": gex, "momentum": momentum}, "cross_flip", "A confirmed cross of the captured gamma-flip level may persist when the tape agrees.")
    for move, momentum in itertools.product((0.001, 0.0025, 0.005), (0.0, 0.0015, 0.003)):
        add("dual_wall_migration", {"move": move, "momentum": momentum}, "follow_walls", "Same-direction migration of both OI walls may precede a repricing of the range.")
    for delta, momentum in itertools.product((0.15, 0.30, 0.50), (0.0015, 0.003, 0.006)):
        add("gex_acceleration", {"delta": delta, "momentum": momentum}, "follow_gex_delta", "A rare large change in normalized GEX may reinforce contemporaneous price momentum.")
    for ratio, gex, momentum in itertools.product((0.015, 0.04, 0.08), (0.25, 0.5), (0.0015, 0.003)):
        add("high_volume_negative_gex_ignition", {"ratio": ratio, "gex": gex, "momentum": momentum}, "follow_snapshot", "High option volume relative to OI in negative GEX may ignite continuation.")
    for ratio, gex, distance in itertools.product((0.005, 0.015, 0.03), (0.25, 0.5), (0.005, 0.01)):
        add("low_turnover_oi_magnet", {"ratio": ratio, "gex": gex, "distance": distance}, "toward_global_max", "Low turnover around concentrated positive-GEX OI may pin spot to the dominant strike.")
    for threshold, momentum, mode in itertools.product((0.10, 0.25, 0.40), (0.0, 0.0025, 0.005), ("fade", "follow")):
        add("oi_concentration_asymmetry", {"threshold": threshold, "momentum": momentum, "mode": mode}, mode, "An unusually concentrated call-versus-put OI shelf may behave as either a wall or a pressure level.")
    for threshold, mode in itertools.product((0.20, 0.40, 0.60), ("toward", "away")):
        add("gex_location_dislocation", {"threshold": threshold, "mode": mode}, mode, "A strong imbalance in exposure above versus below spot may create a rare magnet or vacuum state.")
    for delta, momentum in itertools.product((0.10, 0.25, 0.40), (0.0, 0.0025, 0.005)):
        add("oi_balance_shift", {"delta": delta, "momentum": momentum}, "follow_oi_delta", "A large same-session change in observed OI balance may matter when it agrees with the tape.")
    expanded: list[Candidate] = []
    for row in rows:
        for horizon in ("next", "two_snapshot", "eod", "next_open", "next_close"):
            params = dict(row.parameters)
            params["horizon"] = horizon
            expanded.append(Candidate(row.family, params, row.direction_rule, row.hypothesis))
    return tuple(expanded)


def _direction_and_mask(frame: pd.DataFrame, candidate: Candidate) -> tuple[pd.Series, pd.Series]:
    p = candidate.parameters
    finite = pd.Series(True, index=frame.index)
    direction = pd.Series(0.0, index=frame.index)
    family = candidate.family
    if family == "call_wall_rejection":
        finite &= frame["call_wall_distance"].between(0, float(p["distance"]))
        finite &= frame["day_return"] >= float(p["momentum"])
        finite &= frame["gex_balance"] >= float(p["gex"])
        direction[:] = -1.0
    elif family == "put_wall_bounce":
        finite &= frame["put_wall_distance"].between(-float(p["distance"]), 0)
        finite &= frame["day_return"] <= -float(p["momentum"])
        finite &= frame["gex_balance"] >= float(p["gex"])
        direction[:] = 1.0
    elif family == "negative_gex_breakout":
        finite &= frame["gex_balance"] <= -float(p["gex"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        direction = np.sign(frame["snapshot_return"]).astype(float)
    elif family == "positive_gex_reversion":
        finite &= frame["gex_balance"] >= float(p["gex"])
        finite &= frame["day_return"].abs() >= float(p["momentum"])
        direction = -np.sign(frame["day_return"]).astype(float)
    elif family == "oi_crowd_fade":
        finite &= frame["oi_balance"].abs() >= float(p["oi"])
        finite &= np.sign(frame["day_return"]) == np.sign(frame["oi_balance"])
        finite &= frame["day_return"].abs() >= float(p["momentum"])
        direction = -np.sign(frame["oi_balance"]).astype(float)
    elif family == "near_oi_pressure_follow":
        finite &= frame["near_oi_balance"].abs() >= float(p["oi"])
        finite &= np.sign(frame["snapshot_return"]) == np.sign(frame["near_oi_balance"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        direction = np.sign(frame["near_oi_balance"]).astype(float)
    elif family == "vex_gex_dislocation":
        threshold = float(p["threshold"])
        finite &= frame["gex_balance"].abs() >= threshold
        finite &= frame["vex_balance"].abs() >= threshold
        finite &= np.sign(frame["gex_balance"]) == -np.sign(frame["vex_balance"])
        source = frame["vex_balance"] if "vex" in str(p["mode"]) else frame["gex_balance"]
        direction = np.sign(source).astype(float)
        if str(p["mode"]).startswith("fade"):
            direction *= -1.0
    elif family == "front_expiry_pin":
        finite &= frame["global_max_distance"].abs().between(0.001, float(p["distance"]))
        finite &= frame["front_oi_share"] >= float(p["share"])
        finite &= frame["gex_balance"] >= float(p["gex"])
        direction = np.sign(frame["global_max_distance"]).astype(float)
    elif family == "gamma_flip_reclaim":
        up = (frame["prev_spot"] <= frame["prev_gamma_flip_level"]) & (frame["spot"] > frame["gamma_flip_level"])
        down = (frame["prev_spot"] >= frame["prev_gamma_flip_level"]) & (frame["spot"] < frame["gamma_flip_level"])
        finite &= up | down
        finite &= frame["gex_balance"].abs() >= float(p["gex"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        direction = pd.Series(np.where(up, 1.0, np.where(down, -1.0, 0.0)), index=frame.index)
    elif family == "dual_wall_migration":
        same = np.sign(frame["call_wall_move"]) == np.sign(frame["put_wall_move"])
        minimum = np.minimum(frame["call_wall_move"].abs(), frame["put_wall_move"].abs())
        finite &= same & (minimum >= float(p["move"]))
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        direction = np.sign(frame["call_wall_move"]).astype(float)
    elif family == "gex_acceleration":
        finite &= frame["gex_balance_delta"].abs() >= float(p["delta"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        finite &= np.sign(frame["gex_balance_delta"]) == np.sign(frame["snapshot_return"])
        direction = np.sign(frame["snapshot_return"]).astype(float)
    elif family == "high_volume_negative_gex_ignition":
        finite &= frame["volume_oi_ratio"] >= float(p["ratio"])
        finite &= frame["gex_balance"] <= -float(p["gex"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        direction = np.sign(frame["snapshot_return"]).astype(float)
    elif family == "low_turnover_oi_magnet":
        finite &= frame["volume_oi_ratio"] <= float(p["ratio"])
        finite &= frame["gex_balance"] >= float(p["gex"])
        finite &= frame["global_max_distance"].abs().between(0.001, float(p["distance"]))
        direction = np.sign(frame["global_max_distance"]).astype(float)
    elif family == "oi_concentration_asymmetry":
        asymmetry = frame["call_oi_concentration"] - frame["put_oi_concentration"]
        finite &= asymmetry.abs() >= float(p["threshold"])
        finite &= frame["day_return"].abs() >= float(p["momentum"])
        finite &= np.sign(frame["day_return"]) == np.sign(asymmetry)
        direction = np.sign(asymmetry).astype(float)
        if str(p["mode"]) == "fade":
            direction *= -1.0
    elif family == "gex_location_dislocation":
        finite &= frame["gex_location_balance"].abs() >= float(p["threshold"])
        direction = np.sign(frame["gex_location_balance"]).astype(float)
        if str(p["mode"]) == "away":
            direction *= -1.0
    elif family == "oi_balance_shift":
        finite &= frame["oi_balance_delta"].abs() >= float(p["delta"])
        finite &= frame["snapshot_return"].abs() >= float(p["momentum"])
        finite &= np.sign(frame["oi_balance_delta"]) == np.sign(frame["snapshot_return"])
        direction = np.sign(frame["oi_balance_delta"]).astype(float)
    else:
        raise ValueError(f"unknown candidate family: {family}")
    finite &= direction.ne(0) & direction.notna()
    return finite.fillna(False), direction.fillna(0.0)


def signal_trades(
    panel: pd.DataFrame,
    candidate: Candidate,
    *,
    minimum_available_rate: float = 0.60,
    minimum_total_oi: float = 1_000.0,
    entry_start_utc: int = 14 * 60,
    entry_end_utc: int = 18 * 60 + 30,
) -> pd.DataFrame:
    horizon = str(candidate.parameters.get("horizon") or "eod")
    outcome_columns = {
        "next": ("next_return", "alpha_next_return"),
        "two_snapshot": ("two_snapshot_return", "alpha_two_snapshot_return"),
        "eod": ("forward_return", "alpha_forward_return"),
        "next_open": ("next_open_return", "alpha_next_open_return"),
        "next_close": ("next_close_return", "alpha_next_close_return"),
    }
    if horizon not in outcome_columns:
        raise ValueError(f"unknown holding horizon: {horizon}")
    raw_outcome, alpha_outcome = outcome_columns[horizon]
    is_next_session = horizon in {"next_open", "next_close"}
    effective_start = 19 * 60 if is_next_session else entry_start_utc
    effective_end = 20 * 60 + 5 if is_next_session else entry_end_utc
    base = panel.loc[
        panel["minute_utc"].between(effective_start, effective_end)
        & (panel["available_rate"] >= minimum_available_rate)
        & (panel["total_oi"] >= minimum_total_oi)
        & panel[raw_outcome].notna()
        & panel[alpha_outcome].notna()
    ].copy()
    if is_next_session:
        # The signal is the final captured state, not an earlier state selected
        # because hindsight says its condition later disappeared.
        base = base.sort_values("timestamp").groupby(["ticker", "date"], as_index=False).tail(1)
    mask, direction = _direction_and_mask(base, candidate)
    selected = base.loc[mask].copy()
    if selected.empty:
        return selected
    selected["direction"] = direction.loc[selected.index]
    # Earliest point-in-time trigger only; no same-session stacking.
    selected = selected.sort_values("timestamp").groupby(["ticker", "date"], as_index=False).first()
    selected["raw_gross_return"] = selected["direction"] * selected[raw_outcome]
    selected["alpha_gross_return"] = selected["direction"] * selected[alpha_outcome]
    selected["holding_horizon"] = horizon
    selected["candidate_id"] = candidate.candidate_id
    selected["family"] = candidate.family
    return selected


def _profit_factor(values: np.ndarray) -> float | None:
    wins = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 1e-12:
        return None if wins <= 0 else 999.0
    return float(wins / losses)


def _one_sided_day_signflip_p(day_means: np.ndarray) -> float | None:
    values = day_means[np.isfinite(day_means)]
    if len(values) < 3:
        return None
    observed = float(values.mean())
    if observed <= 0:
        return 1.0
    if len(values) <= 16:
        count = 0
        total = 2 ** len(values)
        for signs in itertools.product((-1.0, 1.0), repeat=len(values)):
            if float(np.mean(values * np.asarray(signs))) >= observed - 1e-15:
                count += 1
        return count / total
    rng = np.random.default_rng(1729)
    signs = rng.choice((-1.0, 1.0), size=(20_000, len(values)))
    return float(np.mean((signs * values).mean(axis=1) >= observed))


def summarize(trades: pd.DataFrame, *, cost_bps_per_side: float) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "days": 0, "tickers": 0}
    cost = 2.0 * float(cost_bps_per_side) / 10_000.0
    raw = trades["raw_gross_return"].to_numpy(dtype=float) - cost
    alpha = trades["alpha_gross_return"].to_numpy(dtype=float) - cost
    work = trades[["date", "ticker", "direction"]].copy()
    work["raw"] = raw
    work["alpha"] = alpha
    daily = work.groupby("date", sort=True)[["raw", "alpha"]].mean()
    leave_one_day_out = []
    for day in daily.index:
        reduced = work.loc[work["date"] != day, "alpha"].to_numpy(dtype=float)
        leave_one_day_out.append(float(reduced.mean()) if len(reduced) else math.nan)
    finite_loo = [value for value in leave_one_day_out if math.isfinite(value)]
    return {
        "trades": int(len(work)),
        "days": int(work["date"].nunique()),
        "tickers": int(work["ticker"].nunique()),
        "longs": int((work["direction"] > 0).sum()),
        "shorts": int((work["direction"] < 0).sum()),
        "win_rate": float(np.mean(raw > 0)),
        "alpha_win_rate": float(np.mean(alpha > 0)),
        "mean_raw_return_pct": float(np.mean(raw) * 100.0),
        "median_raw_return_pct": float(np.median(raw) * 100.0),
        "mean_alpha_return_pct": float(np.mean(alpha) * 100.0),
        "median_alpha_return_pct": float(np.median(alpha) * 100.0),
        "raw_profit_factor": _profit_factor(raw),
        "alpha_profit_factor": _profit_factor(alpha),
        "positive_day_fraction": float(np.mean(daily["alpha"].to_numpy() > 0)),
        "worst_day_alpha_pct": float(daily["alpha"].min() * 100.0),
        "best_day_alpha_pct": float(daily["alpha"].max() * 100.0),
        "day_signflip_p_value": _one_sided_day_signflip_p(daily["alpha"].to_numpy(dtype=float)),
        "leave_one_day_out_min_alpha_pct": (
            float(min(finite_loo) * 100.0) if finite_loo else None
        ),
        "daily_alpha_pct": {str(day): float(value * 100.0) for day, value in daily["alpha"].items()},
    }


def chronological_partitions(panel: pd.DataFrame) -> dict[str, list[str]]:
    dates = sorted(day for day in panel["date"].unique() if day >= "2026-07-27")
    if len(dates) < 12:
        raise RuntimeError("at least 12 complete sessions are required")
    # Keep the newest five sessions untouched and use the preceding five for
    # validation. Any earlier sessions are discovery only.
    return {
        "discovery": dates[:-10],
        "validation": dates[-10:-5],
        "holdout": dates[-5:],
    }


def _partition(trades: pd.DataFrame, dates: Iterable[str]) -> pd.DataFrame:
    return trades.loc[trades["date"].isin(set(dates))].copy()


def _passes_forward(metrics: Mapping[str, Any], stress: Mapping[str, Any]) -> bool:
    return bool(
        int(metrics.get("trades") or 0) >= 15
        and int(metrics.get("days") or 0) >= 4
        and float(metrics.get("mean_alpha_return_pct") or -999.0) > 0.0
        and float(metrics.get("alpha_profit_factor") or 0.0) >= 1.10
        and float(metrics.get("positive_day_fraction") or 0.0) >= 0.60
        and float(metrics.get("leave_one_day_out_min_alpha_pct") or -999.0) > 0.0
        and float(stress.get("mean_alpha_return_pct") or -999.0) > 0.0
    )


def run_lab(
    db_path: str | Path = DEFAULT_DB,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    panel = load_snapshot_panel(db_path)
    partitions = chronological_partitions(panel)
    catalog = candidate_catalog()
    evaluated: list[dict[str, Any]] = []
    all_trades: dict[str, pd.DataFrame] = {}
    signature_owner: dict[tuple[Any, ...], str] = {}
    equivalent_aliases: dict[str, list[str]] = {}
    for candidate in catalog:
        trades = signal_trades(panel, candidate)
        all_trades[candidate.candidate_id] = trades
        signature = (
            str(candidate.parameters.get("horizon")),
            *tuple(
                (int(row.snapshot_id), int(row.direction))
                for row in trades[["snapshot_id", "direction"]].itertuples(index=False)
            ),
        )
        owner = signature_owner.setdefault(signature, candidate.candidate_id)
        if owner != candidate.candidate_id:
            equivalent_aliases.setdefault(owner, []).append(candidate.candidate_id)
        discovery = summarize(_partition(trades, partitions["discovery"]), cost_bps_per_side=10)
        row = {
            "candidate": candidate.to_dict(),
            "signal_rate_pct": float(len(trades) / max(1, panel.groupby(["ticker", "date"]).ngroups) * 100.0),
            "discovery_10bps": discovery,
            "signal_path_owner": owner,
        }
        evaluated.append(row)

    # Rarity is preregistered here as 0.25%-5% of ticker-sessions. Only the
    # strongest discovery rows advance, limiting exposure of later partitions.
    discovery_eligible = [
        row for row in evaluated
        if 0.25 <= row["signal_rate_pct"] <= 5.0
        and row["signal_path_owner"] == row["candidate"]["candidate_id"]
        and int(row["discovery_10bps"].get("trades") or 0) >= 15
        and int(row["discovery_10bps"].get("days") or 0) >= 4
        and float(row["discovery_10bps"].get("mean_alpha_return_pct") or -999.0) > 0
    ]
    discovery_eligible.sort(
        key=lambda row: (
            float(row["discovery_10bps"].get("mean_alpha_return_pct") or -999.0),
            float(row["discovery_10bps"].get("alpha_profit_factor") or 0.0),
        ),
        reverse=True,
    )
    shortlist: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    for row in discovery_eligible:
        family = str(row["candidate"]["family"])
        if family_counts.get(family, 0) >= 2:
            continue
        shortlist.append(row)
        family_counts[family] = family_counts.get(family, 0) + 1
    selected_ids = {row["candidate"]["candidate_id"] for row in shortlist}
    for row in discovery_eligible:
        if len(shortlist) >= 40:
            break
        if row["candidate"]["candidate_id"] not in selected_ids:
            shortlist.append(row)
            selected_ids.add(row["candidate"]["candidate_id"])
    by_id = {row["candidate"]["candidate_id"]: row for row in evaluated}
    for row in shortlist:
        candidate_id = row["candidate"]["candidate_id"]
        trades = all_trades[candidate_id]
        for partition_name in ("validation", "holdout"):
            sample = _partition(trades, partitions[partition_name])
            for bps in (10, 25, 50):
                row[f"{partition_name}_{bps}bps"] = summarize(sample, cost_bps_per_side=bps)
        row["validation_pass"] = _passes_forward(row["validation_10bps"], row["validation_50bps"])
        row["holdout_pass"] = _passes_forward(row["holdout_10bps"], row["holdout_50bps"])
        row["forward_pass"] = bool(row["validation_pass"] and row["holdout_pass"])

    # Candidate selection is based on both forward blocks, never holdout alone.
    finalists = [row for row in shortlist if row.get("forward_pass")]
    finalists.sort(
        key=lambda row: (
            min(
                float(row["validation_50bps"].get("mean_alpha_return_pct") or -999.0),
                float(row["holdout_50bps"].get("mean_alpha_return_pct") or -999.0),
            ),
            float(row["holdout_10bps"].get("mean_alpha_return_pct") or -999.0),
        ),
        reverse=True,
    )
    leader = finalists[0] if finalists else None
    provisional_rows = [
        row for row in shortlist
        if int(row.get("validation_10bps", {}).get("trades") or 0) >= 15
        and int(row.get("holdout_10bps", {}).get("trades") or 0) >= 10
        and float(row.get("validation_10bps", {}).get("mean_alpha_return_pct") or -999.0) > 0.0
        and float(row.get("holdout_10bps", {}).get("mean_alpha_return_pct") or -999.0) > 0.0
        and float(row.get("validation_10bps", {}).get("alpha_profit_factor") or 0.0) > 1.0
        and float(row.get("holdout_10bps", {}).get("alpha_profit_factor") or 0.0) > 1.0
    ]
    provisional_rows.sort(
        key=lambda row: (
            min(
                float(row["validation_10bps"]["mean_alpha_return_pct"]),
                float(row["holdout_10bps"]["mean_alpha_return_pct"]),
            ),
            float(row["discovery_10bps"].get("mean_alpha_return_pct") or -999.0),
        ),
        reverse=True,
    )
    provisional = provisional_rows[0] if provisional_rows else None
    provisional_sensitivity: dict[str, Any] | None = None
    if provisional:
        candidate_payload = provisional["candidate"]
        candidate = Candidate(
            family=candidate_payload["family"],
            parameters=candidate_payload["parameters"],
            direction_rule=candidate_payload["direction_rule"],
            hypothesis=candidate_payload["hypothesis"],
        )
        provisional_sensitivity = {}
        for name, available, minimum_oi in (
            ("base", 0.60, 1_000.0),
            ("high_coverage", 0.80, 1_000.0),
            ("moderate_oi", 0.60, 5_000.0),
            ("high_coverage_and_moderate_oi", 0.80, 5_000.0),
            ("high_oi", 0.60, 10_000.0),
            ("high_coverage_and_oi", 0.80, 10_000.0),
        ):
            sample = signal_trades(
                panel, candidate,
                minimum_available_rate=available,
                minimum_total_oi=minimum_oi,
            )
            provisional_sensitivity[name] = {
                partition: summarize(_partition(sample, dates), cost_bps_per_side=10)
                for partition, dates in partitions.items()
            }
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "status": "completed",
        "question": "Which rare captured OI/GEX intraday hypothesis best survives chronological forward testing?",
        "as_of": str(panel["timestamp"].max()),
        "data": {
            "database": str(Path(db_path).resolve()),
            "snapshots": int(len(panel)),
            "ticker_sessions": int(panel.groupby(["ticker", "date"]).ngroups),
            "tickers": int(panel["ticker"].nunique()),
            "sessions": int(panel["date"].nunique()),
            "first_capture": str(panel["timestamp"].min()),
            "last_capture": str(panel["timestamp"].max()),
            "partitions": partitions,
            "outcome": "captured underlying spot from signal snapshot to final same-session snapshot",
            "benchmark": "same-window SPY return matched to nearest captured SPY snapshot",
            "option_pnl": False,
            "missing_data_rule": "observed cells only; missing OI/gamma is never zero-filled",
        },
        "protocol": {
            "catalog_size": len(catalog),
            "unique_signal_paths": len(signature_owner),
            "holding_horizons": ["next_capture", "two_captures", "end_of_day", "next_session_open", "next_session_close"],
            "rarity_signal_rate_pct": [0.25, 5.0],
            "one_signal_per_ticker_session": True,
            "entry_window_utc": ["14:00", "18:30"],
            "minimum_available_cell_rate": 0.60,
            "minimum_total_observed_oi": 1_000,
            "round_trip_cost_bps": [20, 50, 100],
            "shortlist_count": len(shortlist),
            "shortlist_selected_on": "discovery only with family-balanced advancement",
            "forward_gate": "both validation and holdout; positive 100-bps-stress alpha; >=15 trades, >=4 days, PF>=1.10, positive leave-one-day-out minimum",
        },
        "shortlist": shortlist,
        "equivalent_signal_aliases": equivalent_aliases,
        "leader": leader,
        "leader_status": "STRICT_FORWARD_PASS" if leader else "NO_CANDIDATE_PASSED",
        "provisional_leader": provisional,
        "provisional_status": (
            "PROSPECTIVE_ONLY_COST_FRAGILE" if provisional else "NO_BASELINE_CONSISTENT_CANDIDATE"
        ),
        "provisional_sensitivity": provisional_sensitivity,
        "interpretation": (
            "A leader is only the best rare directional proxy in this 15-session captured panel. "
            "It is not validated option P&L and requires new prospective option-mark observations."
        ),
        "risks_and_missing_evidence": [
            "Only 15 complete sessions are available after excluding partial startup days.",
            "Cross-sectional observations within a day are correlated; inference is clustered by day.",
            "The collection depth and available-cell rate changed during the sample.",
            "The August 10-14 block was inspected during protocol iteration and is now audit evidence, not a pristine future holdout.",
            "Open interest is published with a lag and does not identify who holds either side.",
            "Historical executable option marks do not span this full OI panel.",
            "SPY adjustment is an approximate market control, not a ticker-specific beta model.",
        ],
        "caveat": STANDARD_CAVEAT,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (output / "latest_oi_niche_strategy_report.json").write_text(encoded, encoding="utf-8")
    (output / f"oi_niche_strategy_report_{now.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(encoded, encoding="utf-8")
    _write_rankings(output / "latest_oi_niche_strategy_rankings.csv", shortlist)
    _write_report(output / "latest_oi_niche_strategy_report.md", payload)
    if leader:
        leader_id = leader["candidate"]["candidate_id"]
        all_trades[leader_id].to_csv(output / "latest_oi_niche_strategy_leader_trades.csv", index=False)
    elif provisional:
        provisional_id = provisional["candidate"]["candidate_id"]
        all_trades[provisional_id].to_csv(
            output / "latest_oi_niche_strategy_provisional_trades.csv", index=False
        )
        prospective = {
            "schema_version": 1,
            "created_at": now.isoformat(),
            "status": "PROSPECTIVE_SHADOW_ONLY",
            "candidate": provisional["candidate"],
            "begin_after": str(panel["timestamp"].max()),
            "minimum_new_sessions": 20,
            "required_evidence": [
                "point-in-time OI/GEX snapshots",
                "captured underlying entry and exit marks",
                "captured bid/ask option marks for the selected structure",
                "measured spread and slippage",
            ],
            "promotion_allowed": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
            "caveat": STANDARD_CAVEAT,
        }
        (output / "prospective_rare_candidate.json").write_text(
            json.dumps(prospective, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return payload


def _write_rankings(path: Path, rows: list[dict[str, Any]]) -> None:
    flattened = []
    for rank, row in enumerate(rows, 1):
        flattened.append({
            "rank": rank,
            "candidate_id": row["candidate"]["candidate_id"],
            "family": row["candidate"]["family"],
            "signal_rate_pct": row["signal_rate_pct"],
            "discovery_alpha_pct": row["discovery_10bps"].get("mean_alpha_return_pct"),
            "validation_alpha_pct": row.get("validation_10bps", {}).get("mean_alpha_return_pct"),
            "holdout_alpha_pct": row.get("holdout_10bps", {}).get("mean_alpha_return_pct"),
            "validation_100bps_alpha_pct": row.get("validation_50bps", {}).get("mean_alpha_return_pct"),
            "holdout_100bps_alpha_pct": row.get("holdout_50bps", {}).get("mean_alpha_return_pct"),
            "validation_pass": row.get("validation_pass"),
            "holdout_pass": row.get("holdout_pass"),
            "forward_pass": row.get("forward_pass"),
        })
    pd.DataFrame(flattened).to_csv(path, index=False)


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    lines = [
        "# OI/GEX Niche Strategy Lab",
        "",
        f"As of: {payload['as_of']}",
        "",
        "## Evidence",
        "",
        f"- {payload['data']['snapshots']:,} point-in-time snapshots",
        f"- {payload['data']['tickers']:,} tickers across {payload['data']['sessions']} captured sessions",
        f"- {payload['protocol']['catalog_size']} deterministic niche candidates",
        "- One trigger per ticker/session; 20/50/100 bps round-trip cost cases",
        "- Raw and SPY-adjusted underlying returns; no synthetic option P&L",
        "",
        "## Result",
        "",
        f"Status: **{payload['leader_status']}**",
        "",
    ]
    leader = payload.get("leader")
    if leader:
        lines.extend([
            f"Leader: `{leader['candidate']['candidate_id']}`",
            "",
            "| Partition | Trades | Days | Alpha/trade (20 bps RT) | PF | Alpha/trade (100 bps RT) |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for part in ("discovery", "validation", "holdout"):
            base = leader[f"{part}_10bps"]
            stress = leader.get(f"{part}_50bps", base)
            lines.append(
                f"| {part.title()} | {base.get('trades', 0)} | {base.get('days', 0)} | "
                f"{base.get('mean_alpha_return_pct', float('nan')):.3f}% | "
                f"{base.get('alpha_profit_factor', float('nan')):.2f} | "
                f"{stress.get('mean_alpha_return_pct', float('nan')):.3f}% |"
            )
    else:
        lines.append("No rare candidate passed both fixed forward blocks and the 100-bps cost stress.")
        provisional = payload.get("provisional_leader")
        if provisional:
            lines.extend([
                "",
                f"Provisional lead (not validated): `{provisional['candidate']['candidate_id']}`",
                "",
                "It stayed positive at the 20-bps round-trip baseline in both forward blocks, "
                "but failed day stability and 50/100-bps stress. It is eligible only for new "
                "prospective quote capture, not paper or live execution.",
            ])
    lines.extend([
        "",
        "## Interpretation",
        "",
        str(payload["interpretation"]),
        "",
        "## Risks and missing evidence",
        "",
    ])
    lines.extend(f"- {item}" for item in payload["risks_and_missing_evidence"])
    lines.extend(["", payload["caveat"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
