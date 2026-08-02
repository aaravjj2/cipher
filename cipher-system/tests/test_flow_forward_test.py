from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from core import flow_forward_test as fft


def registration() -> dict:
    payload = {
        "schema_version": 1,
        "registered_at": "2026-07-30T14:32:00Z",
        "purpose": "test",
        "model_id": "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "timeframe": "5m",
        "lookback": 64,
        "pred_bars": 16,
        "sample_count": 1,
        "seed": 42,
        "horizon_sessions": 2,
        "minimum_flow_premium": 25000.0,
        "maximum_dte": 45,
        "minimum_flow_prints": 20,
        "latest_scan_ticker_limit": 5,
        "minimum_absolute_prediction_pct": 0.0,
        "stop_pct": 0.03,
        "minimum_target_distance_pct": 0.3,
        "maximum_target_distance_pct": 5.0,
        "primary_only": True,
        "minimum_scored_sample": 100,
        "decision_rule": "context only",
    }
    payload["config_id"] = fft.canonical_config_id(payload)
    return payload


def signal_row(*, prediction: float = 0.25, group: str = "agreed") -> dict:
    row = {
        "created_at": "2026-07-30T14:35:00+00:00",
        "as_of": "2026-07-30",
        "ticker": "MSFT",
        "horizon": 2,
        "setup_rank": 0,
        "is_primary": True,
        "kind": "flow_golden",
        "side": "above",
        "direction": "long",
        "spot": 400.0,
        "level": 410.0,
        "target_distance_pct": 2.5,
        "stop_pct": 0.03,
        "min_abs_kronos_pred_return_pct": 0.0,
        "kronos_available": True,
        "kronos_direction": "long" if prediction > 0 else "short",
        "kronos_pred_return_pct": prediction,
        "kronos_agrees": group == "agreed",
        "config_id": registration()["config_id"],
        "evaluation_group": group,
        "eligible_for_scoring": True,
        "distance_ok": True,
        "status": "pending",
    }
    row["id"] = fft.signal_id(row)
    return row


def test_make_signal_rows_scores_disagreed_group(monkeypatch):
    monkeypatch.setattr(
        fft,
        "kronos_forecast_signal",
        lambda *args, **kwargs: {
            "available": True,
            "direction": "short",
            "pred_return_pct": -0.4,
        },
    )
    snapshot = {
        "ticker": "MSFT",
        "as_of": "2026-07-30",
        "spot": 400.0,
        "setups": [{"kind": "golden", "side": "above", "center": 410.0}],
    }

    rows = fft.make_signal_rows(
        snapshot,
        horizon=2,
        stop_pct=0.03,
        predictor=object(),
        kronos_timeframe="5m",
        kronos_lookback=64,
        kronos_pred_bars=16,
        kronos_sample_count=1,
        min_abs_kronos_pred_return_pct=0.0,
        pre_registration=registration(),
    )

    assert len(rows) == 1
    assert rows[0]["evaluation_group"] == "disagreed"
    assert rows[0]["eligible_for_scoring"] is True
    assert rows[0]["status"] == "pending"


def test_store_signals_preserves_first_prediction(tmp_path):
    db_path = tmp_path / "forward.sqlite"
    first = signal_row(prediction=0.25)
    second = dict(first)
    second["created_at"] = "2026-07-30T15:00:00+00:00"
    second["kronos_pred_return_pct"] = 9.99

    result1 = fft.store_signals([first], db_path=db_path)
    result2 = fft.store_signals([second], db_path=db_path)

    with sqlite3.connect(db_path) as db:
        created_at, prediction = db.execute(
            "select created_at, kronos_pred_return_pct from forward_signals where id = ?",
            (first["id"],),
        ).fetchone()
    assert result1["inserted"] == 1
    assert result2["duplicates_preserved"] == 1
    assert created_at == first["created_at"]
    assert prediction == 0.25


def test_open_signals_includes_both_evaluation_groups(tmp_path):
    db_path = tmp_path / "forward.sqlite"
    agreed = signal_row(prediction=0.25, group="agreed")
    disagreed = signal_row(prediction=-0.25, group="disagreed")
    disagreed["ticker"] = "GOOGL"
    disagreed["id"] = fft.signal_id(disagreed)
    fft.store_signals([agreed, disagreed], db_path=db_path)

    rows = fft.open_signals(db_path=db_path)

    assert {row["evaluation_group"] for row in rows} == {"agreed", "disagreed"}


def test_latest_scan_tickers_uses_mirrored_cluster(tmp_path, monkeypatch):
    root = tmp_path / "raw_windows" / "device-windows"
    uploaded = root / "uploaded"
    uploaded.mkdir(parents=True)
    (uploaded / "cluster_20260730T143000Z_test.json").write_text(
        json.dumps(
            {
                "scan_type": "cluster",
                "cards": [
                    {"ticker": "NVDA", "rank": 2},
                    {"ticker": "MSFT", "rank": 1},
                    {"ticker": "NVDA", "rank": 3},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fft, "BROWSER_CAPTURE_ROOT", root)

    assert fft.latest_scan_tickers(scan_dir=tmp_path / "missing", limit=2) == ["MSFT", "NVDA"]


def test_local_daily_bars_aggregates_intraday_rows(monkeypatch):
    rows = [
        {
            "timestamp": datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        },
        {
            "timestamp": datetime(2026, 7, 30, 13, 35, tzinfo=timezone.utc),
            "open": 100.5,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 20.0,
        },
    ]
    monkeypatch.setattr(fft, "load_local_ohlcv_rows", lambda ticker, timeframe: rows)

    bars = fft.local_daily_bars("MSFT", "2026-07-30", "2026-07-30")

    assert bars == [
        {
            "time": "2026-07-30",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.5,
            "volume": 30.0,
        }
    ]
