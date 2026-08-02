from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import cluster_kronos_forward as ckf


def registration() -> dict:
    payload = {
        "schema_version": 1,
        "registered_at": "2026-07-30T14:00:00Z",
        "purpose": "test",
        "model_id": "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "data_provider": "tradier_production_timesales",
        "session_filter": "open",
        "timeframe": "5min",
        "context_bars": 64,
        "prediction_bars": 16,
        "sample_count": 1,
        "seed": 42,
        "maximum_rank": 5,
        "minimum_absolute_prediction_pct": 0.0,
        "maximum_generation_delay_minutes": 10,
        "minimum_scored_sample": 100,
        "score_rule": "test",
        "decision_rule": "context only",
    }
    payload["config_id"] = ckf.canonical_config_id(payload)
    return payload


def test_future_timestamps_respects_regular_session():
    last = datetime(2026, 7, 31, 15, 55, tzinfo=ckf.ET)  # Friday
    values = ckf.future_timestamps(last, 2)
    assert values[0] == datetime(2026, 8, 3, 9, 30, tzinfo=ckf.ET)
    assert values[1] == datetime(2026, 8, 3, 9, 35, tzinfo=ckf.ET)


def test_process_capture_freezes_first_prediction(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    db_path = ckf.db_path_for(runtime)
    ckf.ensure_schema(db_path)
    capture = tmp_path / "cluster_20260730T143600Z_test.json"
    capture.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-30T10:36:00-04:00",
                "scan_type": "cluster",
                "cards": [
                    {
                        "ticker": "MSFT",
                        "rank": 1,
                        "direction": "bullish",
                        "spot": 400.0,
                        "target": 410.0,
                        "setup_type": "CLUSTER",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ckf,
        "fetch_bars",
        lambda *args, **kwargs: [
            {
                "timestamp": datetime(2026, 7, 30, 10, 30, tzinfo=ckf.ET),
                "open": 399.0,
                "high": 401.0,
                "low": 398.0,
                "close": 400.0,
                "volume": 100.0,
                "amount": 40000.0,
            }
        ],
    )
    monkeypatch.setattr(
        ckf,
        "forecast_from_bars",
        lambda *args, **kwargs: {
            "available": True,
            "context_end_at": "2026-07-30T10:30:00-04:00",
            "reference_close": 400.0,
            "pred_close": 404.0,
            "pred_return_pct": 1.0,
            "direction": "bullish",
        },
    )
    monkeypatch.setattr(
        ckf,
        "utcnow",
        lambda: datetime(2026, 7, 30, 14, 40, tzinfo=timezone.utc),
    )

    first = ckf.process_capture_file(
        capture,
        db_path=db_path,
        client=object(),
        predictor=object(),
        registration=registration(),
    )
    second = ckf.process_capture_file(
        capture,
        db_path=db_path,
        client=object(),
        predictor=object(),
        registration=registration(),
    )

    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "select evaluation_group, prospective_eligible, pred_return_pct from predictions"
        ).fetchone()
    assert first["inserted"] == 1
    assert second["duplicates"] == 1
    assert row == ("agreed", 1, 1.0)


def test_score_pending_scores_agreed_and_disagreed(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    db_path = ckf.db_path_for(runtime)
    ckf.ensure_schema(db_path)
    reg = registration()
    captured = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)
    with sqlite3.connect(db_path) as db:
        for index, group in enumerate(("agreed", "disagreed")):
            ident = f"id-{index}"
            payload = {
                "id": ident,
                "evaluation_group": group,
                "cluster_direction": "bullish",
            }
            db.execute(
                """
                insert into predictions (
                    id, config_id, capture_file, captured_at, generated_at, ticker,
                    rank, cluster_direction, setup, spot, target, strength,
                    context_end_at, reference_close, pred_close, pred_return_pct,
                    kronos_direction, evaluation_group, prospective_eligible,
                    generation_delay_seconds, status, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ident,
                    reg["config_id"],
                    "capture.json",
                    captured.isoformat(),
                    captured.isoformat(),
                    "MSFT",
                    1,
                    "bullish",
                    "cluster",
                    100.0,
                    105.0,
                    10.0,
                    captured.isoformat(),
                    100.0,
                    101.0 if group == "agreed" else 99.0,
                    1.0 if group == "agreed" else -1.0,
                    "bullish" if group == "agreed" else "bearish",
                    group,
                    1,
                    0.0,
                    "pending",
                    json.dumps(payload),
                ),
            )
    future = [
        {
            "timestamp": captured.astimezone(ckf.ET) + timedelta(minutes=5 * (i + 1)),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + i / 10.0,
            "volume": 1.0,
            "amount": 100.0,
        }
        for i in range(16)
    ]
    monkeypatch.setattr(ckf, "completed_future_bars", lambda *args, **kwargs: future)
    monkeypatch.setattr(
        ckf,
        "utcnow",
        lambda: datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc),
    )

    result = ckf.score_pending(db_path=db_path, client=object(), registration=reg)

    assert result["scored"] == 2
    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from outcomes").fetchone()[0] == 2
        assert db.execute("select count(*) from predictions where status='scored'").fetchone()[0] == 2


def test_report_excludes_audit_only_from_headline(tmp_path):
    runtime = tmp_path / "runtime"
    db_path = ckf.db_path_for(runtime)
    ckf.ensure_schema(db_path)
    reg = registration()
    with sqlite3.connect(db_path) as db:
        for index, eligible in enumerate((1, 0)):
            ident = f"p-{index}"
            db.execute(
                """
                insert into predictions (
                    id, config_id, capture_file, captured_at, generated_at, ticker,
                    rank, cluster_direction, setup, spot, target, strength,
                    context_end_at, reference_close, pred_close, pred_return_pct,
                    kronos_direction, evaluation_group, prospective_eligible,
                    generation_delay_seconds, status, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ident, reg["config_id"], "x", "2026-07-30T14:00:00+00:00",
                    "2026-07-30T14:01:00+00:00", "MSFT", 1, "bullish", "cluster",
                    100.0, 105.0, 1.0, "2026-07-30T14:00:00+00:00", 100.0,
                    101.0, 1.0, "bullish", "agreed", eligible, 60.0, "scored", "{}",
                ),
            )
            db.execute(
                """
                insert into outcomes (
                    prediction_id, scored_at, horizon_end_at, actual_close,
                    actual_return_pct, cluster_directional_return_pct,
                    cluster_direction_positive, kronos_correct, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ident, "2026-07-30T16:00:00+00:00", "2026-07-30T16:00:00+00:00",
                    101.0, 1.0, 1.0, 1, 1, "{}",
                ),
            )

    report = ckf.write_report(runtime, db_path, reg)

    assert report["prospective"]["n"] == 1
    assert report["audit_only"]["n"] == 1
    assert Path(report["markdown_path"]).is_file()
