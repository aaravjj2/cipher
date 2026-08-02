#!/usr/bin/env python3
"""Evaluate frozen price-only forecasts as cross-sectional rankings.

This research-only runner never reads volume and never creates a trade, weight,
or promotion decision.  It evaluates same-origin ticker cohorts only, so a
forecast is compared with contemporaneous realized returns rather than with an
unrelated ticker/date observation.  The full volume-reconciled market gate is
unchanged and remains required outside this narrow price-forecast study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.forecast_ranking import (  # noqa: E402
    cohort_ticker_sets,
    deterministic_random_scores,
    equal_weight_rank_ensemble,
    future_return,
    kendall,
    pairwise_accuracy,
    spearman,
    top_minus_bottom,
)
from scripts.run_kronos_real_verification import (  # noqa: E402
    forecast_terminal_ensemble,
    load_regular_session_daily_bars,
)

CATALOG = ROOT / "data" / "market_catalog.duckdb"
QUALITY_DIR = ROOT / "data" / "market_quality"
DEVELOPMENT_SOURCE = QUALITY_DIR / "expanded_price_only_results_20260802T1830Z.json"

# This contract is intentionally written before any Holdout B model inference.
# It is not an execution universe and must not be used for sizing or trading.
HOLDOUT_B_CONTRACT = {
    "period": "2016-04..2016-08",
    "tickers": ["AAPL", "FB", "IWM", "SPY", "XIV", "MSFT", "GDX", "VXX", "GILD"],
    "origins": ["2016-05-20", "2016-07-18"],
    "horizons": [5, 20],
    "context_sessions": 32,
    "selection_rule": "fixed before Holdout B forecast inference; include a case only when its complete-session, price-continuity-eligible context and outcome are available",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def daily_closes(ticker: str, start: str, end: str) -> list[dict]:
    """Read close-only, exact-391-bar sessions from the immutable local catalog."""
    import duckdb

    query = """
        with regular as (
          select date(timezone('America/New_York', timestamp)) as trading_day,
                 timezone('America/New_York', timestamp) as local_timestamp, close
          from cipher_market.ohlcv_1m
          where ticker = ? and date(timezone('America/New_York', timestamp)) between ? and ?
            and cast(timezone('America/New_York', timestamp) as time) between time '09:30:00' and time '16:00:00'
        ), numbered as (
          select *, row_number() over (partition by trading_day order by local_timestamp desc) as closing_row
          from regular
        )
        select trading_day, count(*) as bars, max(close) filter (where closing_row = 1) as close
        from numbered group by trading_day order by trading_day
    """
    with duckdb.connect(str(CATALOG), read_only=True) as db:
        rows = db.execute(query, [ticker, start, end]).fetchall()
    return [{"date": day.isoformat(), "close": float(close)} for day, bars, close in rows if int(bars) == 391]


def _baseline_scores(case: dict) -> dict[str, float | None]:
    """Fixed price-only baselines, with unavailable lookbacks recorded as null."""
    bars = daily_closes(case["ticker"], case["context_start"], case["origin"])
    by_date = {row["date"]: index for index, row in enumerate(bars)}
    index = by_date.get(case["origin"])
    if index is None:
        return {name: None for name in ("momentum_5", "momentum_20", "momentum_60", "reversal_5", "reversal_20", "ewma_trend", "linear_trend")}
    closes = [row["close"] for row in bars[: index + 1]]

    def momentum(lookback: int) -> float | None:
        return None if len(closes) <= lookback else future_return(closes[-lookback - 1], closes[-1])

    def ewma() -> float | None:
        if len(closes) < 2:
            return None
        returns = [future_return(closes[i - 1], closes[i]) for i in range(1, len(closes))]
        value = returns[0]
        for item in returns[1:]:
            value = 0.2 * item + 0.8 * value
        return value

    def linear() -> float | None:
        if len(closes) < 2:
            return None
        x_bar = (len(closes) - 1) / 2
        y_bar = mean(closes)
        denominator = sum((i - x_bar) ** 2 for i in range(len(closes)))
        slope = sum((i - x_bar) * (value - y_bar) for i, value in enumerate(closes)) / denominator
        return slope / closes[-1]

    m5, m20, m60 = momentum(5), momentum(20), momentum(60)
    return {
        "momentum_5": m5, "momentum_20": m20, "momentum_60": m60,
        "reversal_5": None if m5 is None else -m5,
        "reversal_20": None if m20 is None else -m20,
        "ewma_trend": ewma(), "linear_trend": linear(),
    }


def _score_rows(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        origin_close = float(case["naive_forecast"])
        baseline = _baseline_scores(case)
        row = dict(case)
        row["realized_return"] = future_return(origin_close, float(case["target"]))
        row["scores"] = {
            "kronos": future_return(origin_close, float(case["kronos_point"])),
            "timesfm": future_return(origin_close, float(case["timesfm_point"])),
            **baseline,
        }
        rows.append(row)
    for _, cohort in _cohorts(rows).items():
        random_scores = deterministic_random_scores([row["case_id"] for row in cohort])
        ensemble_scores = equal_weight_rank_ensemble(
            [row["scores"]["kronos"] for row in cohort],
            [row["scores"]["timesfm"] for row in cohort],
        )
        for row, score, ensemble in zip(cohort, random_scores, ensemble_scores):
            row["scores"]["deterministic_random"] = score
            row["scores"]["persistence"] = 0.0
            row["scores"]["equal_weight_rank_ensemble"] = ensemble
    return rows


def _cohorts(rows: list[dict]) -> dict[tuple[str, int], list[dict]]:
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["origin"], int(row["horizon_sessions"]))].append(row)
    return dict(sorted(grouped.items()))


def _metrics(rows: list[dict], score_name: str) -> dict | None:
    scores = [row["scores"].get(score_name) for row in rows]
    if any(score is None for score in scores):
        return None
    returns = [float(row["realized_return"]) for row in rows]
    return {
        "spearman_ic": spearman(scores, returns),
        "kendall_tau": kendall(scores, returns),
        "pairwise_accuracy": pairwise_accuracy(scores, returns),
        "top_minus_bottom_quartile_return": top_minus_bottom(scores, returns, buckets=4),
        "top_quartile_mean_return": _top_quartile(scores, returns),
        "n": len(rows),
    }


def _top_quartile(scores: list[float], returns: list[float]) -> float | None:
    if len(scores) < 4:
        return None
    size = max(1, len(scores) // 4)
    return mean(value for _, value in sorted(zip(scores, returns))[-size:])


def evaluate(cases: list[dict]) -> dict:
    scored = _score_rows(cases)
    cohorts = _cohorts(scored)
    score_names = list(scored[0]["scores"]) if scored else []
    per_cohort = []
    aggregate: dict[str, dict] = {}
    for score_name in score_names:
        valid = [_metrics(group, score_name) for group in cohorts.values()]
        valid = [item for item in valid if item is not None]

        def average_metric(key: str) -> float | None:
            values = [item[key] for item in valid if item[key] is not None]
            return mean(values) if values else None

        aggregate[score_name] = {
            "eligible_cohorts": len(valid),
            "mean_spearman_ic": average_metric("spearman_ic"),
            "mean_kendall_tau": average_metric("kendall_tau"),
            "mean_pairwise_accuracy": average_metric("pairwise_accuracy"),
            "mean_top_minus_bottom_quartile_return": average_metric("top_minus_bottom_quartile_return"),
        }
    for (origin, horizon), cohort in cohorts.items():
        per_cohort.append({
            "origin": origin, "horizon_sessions": horizon,
            "tickers": sorted(row["ticker"] for row in cohort), "n": len(cohort),
            "metrics": {name: _metrics(cohort, name) for name in score_names},
        })
    return {"cases": scored, "cohorts": per_cohort, "aggregate": aggregate}


def development_payload() -> dict:
    source = json.loads(DEVELOPMENT_SOURCE.read_text(encoding="utf-8"))
    result = evaluate(source["cases"])
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "cross-sectional ranking diagnostics on pre-existing development forecasts only",
        "gate": {"allowed_use": "price_forecast_research_only_no_volume_features", "volume_used": False, "full_volume_gate_changed": False},
        "source": {"path": str(DEVELOPMENT_SOURCE), "sha256": sha256(DEVELOPMENT_SOURCE), "case_count": len(source["cases"])},
        "holdout_a_accessed": False,
        "holdout_b_contract": HOLDOUT_B_CONTRACT,
        "baseline_contract": {
            "models": ["kronos", "timesfm", "equal_weight_rank_ensemble"],
            "baselines": ["persistence", "deterministic_random", "momentum_5", "momentum_20", "momentum_60", "reversal_5", "reversal_20", "ewma_trend", "linear_trend"],
            "random_seed": 42,
            "unavailable_lookback_policy": "record null; do not shorten a registered lookback",
        },
        "cohort_ticker_sets": {f"{origin}-h{horizon}": sorted(tickers) for (origin, horizon), tickers in cohort_ticker_sets(result["cases"]).items()},
        "results": result,
        "promotion_eligible": False,
        "live_execution": False,
    }


def holdout_b_cases() -> list[dict]:
    """Validate the pre-registered Holdout B cases before any model is loaded."""
    cases = []
    for ticker in HOLDOUT_B_CONTRACT["tickers"]:
        bars = load_regular_session_daily_bars(ticker=ticker, start="2016-04-01", end="2016-08-31")
        index = {row["date"]: position for position, row in enumerate(bars)}
        for origin in HOLDOUT_B_CONTRACT["origins"]:
            origin_index = index.get(origin)
            if origin_index is None:
                continue
            for horizon in HOLDOUT_B_CONTRACT["horizons"]:
                context = bars[origin_index - 31 : origin_index + 1]
                realized = bars[origin_index + 1 : origin_index + 1 + horizon]
                sequence = context + realized
                if len(context) != 32 or len(realized) != horizon:
                    continue
                if any(sequence[i]["close"] / sequence[i - 1]["close"] <= .5 or sequence[i]["close"] / sequence[i - 1]["close"] >= 2.0 for i in range(1, len(sequence))):
                    continue
                cases.append({
                    "case_id": f"{ticker}-{origin}-h{horizon}", "ticker": ticker,
                    "origin": origin, "context_start": context[0]["date"],
                    "outcome_start": realized[0]["date"], "outcome_end": realized[-1]["date"],
                    "horizon_sessions": horizon, "origin_close": context[-1]["close"],
                    "target": realized[-1]["close"],
                })
    return cases


def holdout_b_preregistration() -> dict:
    cases = holdout_b_cases()
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "frozen Holdout B forecast-ranking case roster before model inference",
        "contract": HOLDOUT_B_CONTRACT, "validated_cases": cases,
        "gate": {"allowed_use": "price_forecast_research_only_no_volume_features", "volume_used": False, "full_volume_gate_changed": False},
        "holdout_a_accessed": False, "development_source": {"path": str(DEVELOPMENT_SOURCE), "sha256": sha256(DEVELOPMENT_SOURCE)},
        "primary_comparison": "Kronos and TimesFM rank scores versus momentum_20; development showed neither model superiority",
        "promotion_eligible": False, "live_execution": False,
    }


def _load_timesfm_model(max_context: int, max_horizon: int):
    import numpy as np
    from timesfm import ForecastConfig, TimesFM_2p5_200M_torch
    model = TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    model.compile(ForecastConfig(max_context=max_context, max_horizon=max_horizon))
    return model, np


def run_holdout_b(cases: list[dict]) -> list[dict]:
    """Run registered public models once over the already frozen Holdout B roster."""
    from core.kronos_research import load_predictor

    timesfm_model, np = _load_timesfm_model(32, 20)
    kronos = load_predictor(device="cpu", max_context=32)
    out = []
    for case in cases:
        bars = load_regular_session_daily_bars(ticker=case["ticker"], start="2016-04-01", end="2016-08-31")
        origin_index = {row["date"]: position for position, row in enumerate(bars)}[case["origin"]]
        context = bars[origin_index - 31 : origin_index + 1]
        realized = bars[origin_index + 1 : origin_index + 1 + case["horizon_sessions"]]
        point, _ = timesfm_model.forecast(horizon=case["horizon_sessions"], inputs=[np.asarray([row["close"] for row in context], dtype=np.float32)])
        kronos_point, _ = forecast_terminal_ensemble(
            kronos, context, realized=realized, horizon=case["horizon_sessions"],
            temperature=1.0, top_p=0.9, sample_count=20, seed=42, price_only=True,
        )
        out.append({
            **case, "naive_forecast": case["origin_close"], "timesfm_point": float(point[0][-1]),
            "kronos_point": float(kronos_point),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen price-only cross-sectional forecast ranks.")
    parser.add_argument("--out", type=Path, help="optional explicit output path")
    parser.add_argument("--prepare-holdout-b", action="store_true", help="write the immutable Holdout B roster without loading models")
    parser.add_argument("--run-holdout-b", action="store_true", help="run the frozen Holdout B roster after it has been prepared")
    parser.add_argument("--reevaluate-holdout", type=Path, help="recompute diagnostics from an immutable raw Holdout B artifact without model inference")
    args = parser.parse_args(argv)
    if not DEVELOPMENT_SOURCE.is_file() or not CATALOG.is_file():
        raise SystemExit("required frozen development source or local catalog is missing")
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    if args.prepare_holdout_b:
        payload = holdout_b_preregistration()
        out = args.out or QUALITY_DIR / f"cross_sectional_ranking_holdout_b_preregistration_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(out), "validated_cases": len(payload["validated_cases"]), "models_loaded": False}, indent=2))
        return 0
    if args.reevaluate_holdout:
        previous = json.loads(args.reevaluate_holdout.read_text(encoding="utf-8"))
        raw_cases = previous.get("raw_cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise SystemExit("the supplied Holdout B artifact has no raw cases")
        payload = {**previous, "created_at": datetime.now(timezone.utc).isoformat(), "recomputed_from": str(args.reevaluate_holdout), "results": evaluate(raw_cases)}
        out = args.out or QUALITY_DIR / f"cross_sectional_ranking_holdout_b_results_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(out), "validated_cases": len(raw_cases), "models_loaded": False}, indent=2))
        return 0
    if args.run_holdout_b:
        preregistration = holdout_b_preregistration()
        cases = preregistration["validated_cases"]
        if not cases:
            raise SystemExit("no frozen Holdout B cases passed the price-only gate")
        raw_cases = run_holdout_b(cases)
        payload = {**preregistration, "created_at": datetime.now(timezone.utc).isoformat(), "raw_cases": raw_cases, "results": evaluate(raw_cases)}
        out = args.out or QUALITY_DIR / f"cross_sectional_ranking_holdout_b_results_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(out), "validated_cases": len(cases), "promotion_eligible": False}, indent=2))
        return 0
    payload = development_payload()
    out = args.out or QUALITY_DIR / f"cross_sectional_ranking_development_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(out), "cohorts": len(payload["results"]["cohorts"]), "promotion_eligible": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
