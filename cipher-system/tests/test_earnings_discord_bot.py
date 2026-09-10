from __future__ import annotations

from pathlib import Path

from earnings_model import discord_bot


def _position(index: int) -> dict:
    return {
        "symbol": f"T{index:02d}",
        "strategy_type": "Debit Bull Call Spread",
        "report_date": "2026-08-27",
        "expiry_date": "2026-08-28" if index < 20 else "2026-09-04",
        "legs": [
            {"action": "BUY", "type": "CALL", "strike": 100.0},
            {"action": "SELL", "type": "CALL", "strike": 105.0},
        ],
        "contracts": 2,
        "total_cost": 400.0,
        "max_gain": 600.0,
        "notes": "Paper-only validation position with a deliberately descriptive note.",
    }


def test_paper_book_is_split_without_losing_positions_or_exceeding_discord_limit():
    payloads = discord_bot.build_paper_portfolio_payloads([_position(i) for i in range(33)])
    assert len(payloads) >= 2
    assert sum(len(payload["embeds"][0]["fields"]) for payload in payloads) == 33
    assert all(
        discord_bot._embed_chars(payload["embeds"][0]) <= discord_bot.DISCORD_EMBED_CHAR_LIMIT
        for payload in payloads
    )
    descriptions = " ".join(payload["embeds"][0]["description"] for payload in payloads)
    assert "2026-08-28" in descriptions
    assert "2026-09-04" in descriptions


def test_weekly_preview_uses_live_cards(monkeypatch):
    cards = [{
        "symbol": "NVDA", "scheduled_date": "2026-08-26",
        "eps_estimate_avg": 2.09, "hist_beat_rate": 0.53,
        "direction_bias": "BULLISH", "confidence": 0.64,
        "pre_drift_20d": 4.2, "expected_gap_pct": 1.1,
        "recommended_strategy": "Paper bull call spread",
    }]
    monkeypatch.setattr(discord_bot, "find_upcoming_earnings", lambda days_ahead: cards)
    sent = []
    monkeypatch.setattr(
        discord_bot,
        "send_discord_payload",
        lambda payload, webhook_url=None: sent.append(payload) or {"status": "delivered"},
    )
    assert discord_bot.notify_discord_weekly_preview("https://example.invalid") == {"status": "delivered"}
    text = str(sent[0])
    assert "2026-08-26" in text
    assert "August 18" not in text
    assert discord_bot._embed_chars(sent[0]["embeds"][0]) <= discord_bot.DISCORD_EMBED_CHAR_LIMIT


def test_portfolio_notification_reports_partial_delivery_failure(monkeypatch):
    monkeypatch.setattr(discord_bot, "get_active_paper_positions", lambda: [_position(i) for i in range(33)])
    monkeypatch.setattr(
        discord_bot, "get_paper_scorecard",
        lambda: {"settled": 13, "wins": 2, "realized_pnl": -14245.0},
    )
    outcomes = iter(({"status": "delivered"}, {"status": "error", "reason": "bad"}))
    monkeypatch.setattr(discord_bot, "send_discord_payload", lambda *_args, **_kwargs: next(outcomes))
    result = discord_bot.notify_discord_paper_book("https://example.invalid")
    assert result["status"] == "error"
    assert result["messages"] == 2


def test_portfolio_embed_labels_estimated_settlement_results():
    payload = discord_bot.build_paper_portfolio_embed(
        [_position(1)], {"settled": 13, "wins": 2, "realized_pnl": -14245.0}
    )
    description = payload["embeds"][0]["description"]
    assert "2/13 wins" in description
    assert "-$14,245.00" in description
    assert "estimated entry prices" in description


def test_scheduled_digest_preserves_failures_and_orders_stages():
    unit = (
        Path(__file__).resolve().parents[2]
        / "infra/gcp-cipher-vm/systemd/cipher-earnings-digest.service"
    ).read_text(encoding="utf-8")
    stages = ("paper-settle", "radar", "paper-enter", "notify-discord")
    assert [unit.index(f"earnings_model {stage}") for stage in stages] == sorted(
        unit.index(f"earnings_model {stage}") for stage in stages
    )
    assert unit.count("|| status=1") == 4
    assert "exit $status" in unit
    assert '--radar-input' in unit


def test_scheduled_preview_uses_fresh_artifact_and_condenses_blocked_cards(monkeypatch, tmp_path):
    import json
    from datetime import datetime, timezone
    path = tmp_path / 'radar.json'
    path.write_text(json.dumps({'as_of': datetime.now(timezone.utc).isoformat(), 'cards': [{'symbol':'TEST','scheduled_date':'2026-09-15','strategy_eligible':False}], 'data_status':'current'}))
    monkeypatch.setattr(discord_bot, 'find_upcoming_earnings', lambda **k: (_ for _ in ()).throw(AssertionError('no rescan')))
    sent=[]
    monkeypatch.setattr(discord_bot, 'send_discord_payload', lambda p, **k: sent.append(p) or {'status':'delivered'})
    assert discord_bot.notify_discord_weekly_preview(radar_path=str(path))['status'] == 'delivered'
    assert sent[0]['embeds'][0]['fields'] == []
    path.write_text(json.dumps({'as_of':'2020-01-01T00:00:00Z','cards':[]}))
    assert discord_bot.notify_discord_weekly_preview(radar_path=str(path))['status'] == 'warning'
    assert len(sent)==1
