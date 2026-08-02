from datetime import date

from core.paper_executor.capture_backtest import run_backtest


def test_capture_backtest_includes_cluster_and_flash(tmp_path):
    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    (uploaded / "cluster_20260728T140000Z_a.json").write_text(
        """
        {
          "captured_at": "2026-07-28T14:00:00Z",
          "scan_type": "cluster",
          "cards": [
            {"ticker": "AAPL", "direction": "bullish", "setup_type": "CLUSTER", "spot": 100, "target": 102, "strength": 250, "rank": 1}
          ]
        }
        """,
        encoding="utf-8",
    )
    (uploaded / "flash_20260728T140500Z_b.json").write_text(
        """
        {
          "captured_at": "2026-07-28T14:05:00Z",
          "scan_type": "flash",
          "cards": [
            {"ticker": "AAPL", "direction": "bullish", "setup_type": "Floor bounce", "spot": 101, "target": 102, "invalidation": 99, "score": 70, "rank": 3}
          ]
        }
        """,
        encoding="utf-8",
    )
    (uploaded / "flash_20260728T141000Z_c.json").write_text(
        """
        {
          "captured_at": "2026-07-28T14:10:00Z",
          "scan_type": "flash",
          "cards": [
            {"ticker": "AAPL", "direction": "bullish", "setup_type": "Floor bounce", "spot": 102.1, "target": 103, "invalidation": 100, "score": 72, "rank": 2}
          ]
        }
        """,
        encoding="utf-8",
    )
    (uploaded / "flash_20260728T141500Z_d.json").write_text(
        """
        {
          "captured_at": "2026-07-28T14:15:00Z",
          "scan_type": "flash",
          "cards": [
            {"ticker": "AAPL", "direction": "bullish", "setup_type": "Floor bounce", "spot": 102.4, "target": 103, "invalidation": 100, "score": 74, "rank": 2}
          ]
        }
        """,
        encoding="utf-8",
    )
    (uploaded / "cluster_20260728T142000Z_e.json").write_text(
        """
        {
          "captured_at": "2026-07-28T14:20:00Z",
          "scan_type": "cluster",
          "cards": [
            {"ticker": "MSFT", "direction": "bullish", "setup_type": "CLUSTER", "spot": 200, "target": 205, "strength": 200, "rank": 6}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = run_backtest(tmp_path, date(2026, 7, 28), cooldown_minutes=10, max_hold_minutes=45)

    assert report["inputs"]["observations"] == 5
    assert {row["scan_type"] for row in report["trades"]} == {"cluster", "flash"}
    assert len(report["trades"]) == 2
    cluster = next(row for row in report["trades"] if row["scan_type"] == "cluster")
    assert cluster["exit_reason"] == "target_touched_snapshot"
    assert cluster["option_model"] == "atm_debit_spread_proxy"
    assert cluster["reference_option_model"] == "atm_long_option_delta_proxy"
    assert cluster["spread_structure"] == "call_debit_spread"
    assert "total_spread_pnl_dollars" in report["summary"][0]
