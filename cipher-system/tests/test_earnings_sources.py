from core import earnings_sources, event_context


def test_sources_preserve_conflicting_estimates_and_revision_history(tmp_path):
    result = earnings_sources.collect(
        ["NVDA"], observed_at="2026-08-17T12:00:00+00:00",
        yahoo_fetch=lambda symbol: [{"symbol": symbol, "scheduled_date": "2026-08-26", "timing": "UNKNOWN",
                                      "status": "ESTIMATED", "provider": "yahoo_finance_via_yfinance",
                                      "provider_event_id": "y1"}],
        finviz_fetch=lambda: [{"Ticker": "NVDA", "Earnings": "08/27/2026 AMC"}],
    )
    assert len(result["events"]) == 2
    assert all(row["conflict"] for row in result["events"])
    payload = event_context.snapshot(["NVDA"], [], observed_at="2026-08-17T12:00:00+00:00", earnings=result)
    latest = event_context.save(payload, directory=tmp_path)
    ticker = event_context.for_ticker("NVDA", latest=latest)
    assert ticker["earnings"]["status"] == "AVAILABLE"
    assert len(ticker["earnings"]["events"]) == 2
    assert (tmp_path / "event_calendar.sqlite").exists()
