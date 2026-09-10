"""Tests for the append-only prospective prediction log.

The log exists so every daily radar outcome — including NO_TRADE and
gate-blocked decisions — is recorded before outcomes exist, idempotently per
(date, ticker), with zero manual steps in the 08:15 ET digest. These tests pin
the writer's honesty guarantees: no duplicate lines on rerun, required fields
always present, entry attempts read from the paper book as of log time, and a
write failure that degrades loudly instead of breaking the digest. No test
touches the network or the local core.
"""
import json
import sys
from pathlib import Path

import pytest

joblib = pytest.importorskip("joblib", reason="earnings_model requires joblib")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from earnings_model import prospective_log as pl  # noqa: E402
from earnings_model import cli  # noqa: E402


def _card(symbol="INTU", scheduled="2026-08-25", **overrides):
    card = {
        "symbol": symbol,
        "scheduled_date": scheduled,
        "earnings_date_confirmation": "single_source_unconfirmed",
        "days_until": 0,
        "direction_bias": "NEUTRAL / MIXED",
        "confidence": 0.5,
        "expected_gap_pct": 0.89,
        "recommended_strategy": "NO TRADE — direction model failed independent holdout gate",
    }
    card.update(overrides)
    return card


def _log_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_record_carries_every_required_field_for_a_no_trade_card(tmp_path):
    record = pl.build_record(
        _card(),
        run_date="2026-08-25",
        model_version="earnings-v2.0-2026-08-23",
        gate_status="NO_TRADE_DIRECTION_FAILED_HOLDOUT",
        entry_attempted=False,
    )
    for field in pl.REQUIRED_FIELDS:
        assert field in record, f"prospective record lost {field}"
    assert record["gate_status"] == "NO_TRADE_DIRECTION_FAILED_HOLDOUT"
    assert record["entry_attempted"] is False


def test_append_is_idempotent_per_date_and_ticker(tmp_path):
    path = tmp_path / "prospective.jsonl"
    first = pl.build_record(
        _card(), run_date="2026-08-25", model_version="v1",
        gate_status="GATE", entry_attempted=False,
    )
    summary_one = pl.append_records([first], path=path)
    assert summary_one["written"] == 1

    # Same (date, ticker), different content: the original line stands untouched.
    changed = pl.build_record(
        _card(confidence=0.9), run_date="2026-08-25", model_version="v1",
        gate_status="GATE", entry_attempted=True,
    )
    summary_two = pl.append_records([changed], path=path)
    assert summary_two["written"] == 0
    assert summary_two["skipped_existing"] == 1

    lines = _log_lines(path)
    assert len(lines) == 1
    assert lines[0]["predicted_confidence"] == 0.5


def test_raw_probability_survives_for_future_validation():
    record = pl.build_record(
        _card(prob_day5_up=0.63, raw_direction='BULLISH', forecast_status='UNVALIDATED'),
        run_date='2026-09-09', model_version='frozen-v1',
        gate_status='NO_TRADE_DIRECTION_FAILED_HOLDOUT', entry_attempted=False,
    )
    assert record['raw_prob_day5_up'] == 0.63
    assert record['predicted_confidence'] == 0.5
    assert record['forecast_status'] == 'UNVALIDATED'


def test_new_day_appends_fresh_lines_without_disturbing_old_ones(tmp_path):
    path = tmp_path / "prospective.jsonl"
    pl.append_records(
        [pl.build_record(_card("AAA"), run_date="2026-08-25", model_version="v1",
                         gate_status="G", entry_attempted=False)],
        path=path,
    )
    pl.append_records(
        [
            pl.build_record(_card("AAA"), run_date="2026-08-26", model_version="v1",
                            gate_status="G", entry_attempted=False),
            pl.build_record(_card("BBB"), run_date="2026-08-26", model_version="v1",
                            gate_status="G", entry_attempted=False),
        ],
        path=path,
    )
    lines = _log_lines(path)
    assert [(row["date"], row["ticker"]) for row in lines] == [
        ("2026-08-25", "AAA"), ("2026-08-26", "AAA"), ("2026-08-26", "BBB"),
    ]


def test_missing_required_field_raises_instead_of_thinning_the_log(tmp_path):
    path = tmp_path / "prospective.jsonl"
    broken = {"date": "2026-08-25", "ticker": "AAA"}
    with pytest.raises(ValueError, match="missing required fields"):
        pl.append_records([broken], path=path)
    # Nothing partial was written.
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_malformed_trailing_line_is_counted_not_crashed_on(tmp_path):
    path = tmp_path / "prospective.jsonl"
    good = pl.build_record(_card("AAA"), run_date="2026-08-25", model_version="v1",
                           gate_status="G", entry_attempted=False)
    path.write_text(json.dumps(good) + "\n" + "{truncated", encoding="utf-8")
    keys, malformed = pl.existing_keys(path)
    assert keys == {("2026-08-25", "AAA")}
    assert malformed == 1


def test_attempted_entries_reads_paper_book_by_symbol_and_report_date(tmp_path):
    from earnings_model import paper_portfolio as pp

    db_path = str(tmp_path / "paper.sqlite")
    conn = pp.init_paper_db(db_path)
    conn.execute(
        """
        INSERT INTO paper_positions (
            symbol, strategy_type, report_date, entry_date, expiry_date,
            spot_at_entry, legs_json, contracts, unit_debit, total_cost,
            max_gain, max_loss, status, notes, created_at
        ) VALUES ('INTU', 'Debit Bull Call Spread', '2026-08-25', '2026-08-25',
                  '2026-08-28', 100.0, '[]', 1, 1.0, 100.0, 100.0, 100.0,
                  'OPEN', '', '2026-08-25')
        """
    )
    conn.commit()
    conn.close()

    attempted = pl.attempted_entries(
        [("INTU", "2026-08-25"), ("MSFT", "2026-08-25")], db_path=db_path
    )
    assert attempted == {("INTU", "2026-08-25")}

    # A missing paper book means nothing was attempted, never an error.
    assert pl.attempted_entries([("INTU", "2026-08-25")],
                                db_path=str(tmp_path / "absent.sqlite")) == set()


def test_log_radar_cards_writes_no_trade_cards_and_marks_known_entries(tmp_path):
    path = tmp_path / "prospective.jsonl"
    summary = pl.log_radar_cards(
        [_card("INTU", "2026-08-25"), _card("MSFT", "2026-08-27")],
        run_date="2026-08-25",
        model_version="earnings-v2.0-2026-08-23",
        gate_status="NO_TRADE_DIRECTION_FAILED_HOLDOUT",
        path=path,
        paper_db_path=str(tmp_path / "absent-paper.sqlite"),
        now_utc=None,
    )
    assert summary["written"] == 2
    lines = _log_lines(path)
    assert all(row["entry_attempted"] is False for row in lines)
    assert lines[0]["calendar_confirmation"] == "single_source_unconfirmed"


def test_radar_cli_step_writes_the_log(monkeypatch, tmp_path, capsys):
    log_path = tmp_path / "runtime" / "earnings_prospective_log.jsonl"

    monkeypatch.setattr(cli, "find_upcoming_earnings", lambda **kwargs: [_card()])
    monkeypatch.setattr(cli, "get_paper_scorecard", lambda: {})
    monkeypatch.setattr(
        cli, "load_trained_models",
        lambda: {
            "results": {
                "model_version": "earnings-v2.0-2026-08-23",
                "models": {},
                "strategy_gate": {"status": "NO_TRADE_DIRECTION_FAILED_HOLDOUT"},
            }
        },
    )
    monkeypatch.setattr(cli, "render_radar_table", lambda cards: "RADAR")

    from earnings_model import prospective_log as pl_module

    monkeypatch.setattr(pl_module, "DEFAULT_LOG_PATH", log_path)

    monkeypatch.setattr(
        sys, "argv", ["earnings_model", "radar", "--json-output", str(tmp_path / "radar.json")]
    )
    assert cli.main() is None

    lines = _log_lines(log_path)
    assert len(lines) == 1
    record = lines[0]
    for field in ("date", "ticker", "model_version", "predicted_direction",
                  "predicted_confidence", "predicted_gap_magnitude_pct", "gate_status",
                  "calendar_confirmation", "entry_attempted"):
        assert field in record
    out = capsys.readouterr().out
    assert "Prospective log: wrote 1" in out

    # A rerun of the same digest step must not duplicate lines.
    assert cli.main() is None
    assert len(_log_lines(log_path)) == 1
