from core.paper_executor.ingestion import normalize_batch
from core.paper_executor.capture_files import summarize


def test_batch_id_is_stable_for_same_payload():
    payload = {"source": "access_obsidian_browser", "cards": [{"ticker": "AAPL"}]}
    assert normalize_batch(payload)["batch_id"] == normalize_batch(payload)["batch_id"]


def test_capture_file_parent_fields_are_propagated_to_cards():
    payload = {
        "captured_at": "2026-07-28T14:27:24-04:00",
        "scan_type": "flash_agentic",
        "cards": [{
            "ticker": "AAPL",
            "direction": "bearish",
            "setup_type": "Rejection reversal",
            "spot": 339.15,
            "target": 335,
            "invalidation": 342.5,
        }],
    }

    card = normalize_batch(payload)["cards"][0]

    assert card["scanner_type"] == "flash_agentic"
    assert card["captured_at"] == "2026-07-28T14:27:24-04:00"


def test_scanner_run_rows_are_normalized_to_executor_cards():
    payload = {
        "captured_at": "2026-07-29T09:52:03-04:00",
        "mode": "flash",
        "rows": [{
            "ticker": "GOOGL",
            "bias": "BULLISH",
            "setup_family": "floor_bounce",
            "spot": 332.5,
            "push_target": 335,
            "invalidation": 330,
        }],
    }

    card = normalize_batch(payload)["cards"][0]

    assert card["scanner_type"] == "flash"
    assert card["direction"] == "bullish"
    assert card["setup"] == "floor bounce"
    assert card["target"] == 335


def test_capture_summary_counts_patterns(tmp_path):
    ready = tmp_path / "ready"
    ready.mkdir()
    (ready / "flash_agentic_20260728T182724Z_a.json").write_text(
        """
        {
          "captured_at": "2026-07-28T18:27:24Z",
          "scan_type": "flash_agentic",
          "cards": [
            {"ticker": "AAPL", "direction": "bearish", "setup_type": "Rejection reversal", "score": 92, "target": 335, "invalidation": 342.5},
            {"ticker": "AMD", "direction": "bullish", "setup_type": "Rejection reversal", "score": 99, "target": 466.69, "invalidation": 447.5}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = summarize(tmp_path)

    assert report["files"] == 1
    assert report["cards"] == 2
    assert report["actionable_cards"] == 2
    assert report["files_by_scan"] == {"flash_agentic": 1}
    assert report["top_tickers"]["AAPL"] == 1
    assert report["top_patterns"][0]["pattern"].startswith("flash_agentic|rejection reversal|")
