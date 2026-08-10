"""Envelope guards for /api/news.

The route is deliberately a thin pass-through of yahoo_rss_headlines: headlines only,
no sentiment score, no ranking, no derived signal. That restraint is the thing worth
pinning — a future edit that starts scoring or re-ordering items would still look like
a working news panel, so these tests assert the payload is exactly what the feed gave,
in the feed's own order, and that the disclaimer travels with it.

The feed itself is stubbed: the point is the envelope, not Yahoo's uptime.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import app as core_app  # noqa: E402


def _items(n):
    return [
        {"title": f"Headline {i}", "link": f"https://example.com/{i}", "published": "Mon, 10 Aug 2026 18:00:00 +0000"}
        for i in range(n)
    ]


def test_envelope_passes_feed_items_through_unchanged(monkeypatch):
    feed = _items(3)
    monkeypatch.setattr(core_app, "yahoo_rss_headlines", lambda symbol, limit: feed)

    out = core_app.news_headlines("nvda")

    assert out["ticker"] == "NVDA"
    assert out["read_only"] is True
    assert out["source"] == "Yahoo Finance RSS"
    # Same objects, same order: nothing sorted, filtered, scored or annotated.
    assert out["headlines"] == feed
    assert all(set(row) == {"title", "link", "published"} for row in out["headlines"])


def test_caveat_is_always_attached(monkeypatch):
    monkeypatch.setattr(core_app, "yahoo_rss_headlines", lambda symbol, limit: [])
    out = core_app.news_headlines("AAPL")
    # The panel renders this string verbatim, so an empty or missing caveat would ship a
    # headline list with no statement of its limits.
    assert out["caveat"] == core_app.NEWS_CAVEAT
    assert "does not score" in out["caveat"]
    assert out["headlines"] == []


def test_symbol_and_limit_reach_the_feed_normalized(monkeypatch):
    seen = {}

    def fake(symbol, limit):
        seen["symbol"] = symbol
        seen["limit"] = limit
        return []

    monkeypatch.setattr(core_app, "yahoo_rss_headlines", fake)
    core_app.news_headlines("  tsla  ", limit=7)
    assert seen == {"symbol": "TSLA", "limit": 7}


def test_limit_is_clamped_to_a_sane_range(monkeypatch):
    seen = []
    monkeypatch.setattr(
        core_app, "yahoo_rss_headlines", lambda symbol, limit: seen.append(limit) or []
    )
    for asked in (0, -5, 1000):
        core_app.news_headlines("SPY", limit=asked)
    # A caller cannot ask for zero items, nor walk the whole feed history.
    assert seen == [1, 1, 50]
