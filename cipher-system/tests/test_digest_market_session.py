from datetime import datetime, timezone

import pytest

from scripts import copilot_daily_digest as digest
from core import portfolio_daily_report as daily


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def clock(monkeypatch, value):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment(value).astimezone(tz)
    monkeypatch.setattr(digest, "datetime", Frozen)


@pytest.mark.parametrize("at", ["2026-09-07T14:00:00", "2026-09-06T14:00:00", "2026-09-08T10:35:00", "2026-09-08T20:01:00"])
def test_alerts_skip_closed_sessions_without_fetch_send_or_state(monkeypatch, at):
    clock(monkeypatch, at)
    monkeypatch.setattr(digest, "build_digest", lambda: pytest.fail("must gate before fetching"))
    assert digest.main(["--alerts-only"]) == 0


def test_holiday_daily_report_does_not_open_database_or_send(monkeypatch, tmp_path):
    clock(monkeypatch, "2026-09-07T17:40:00")
    monkeypatch.setattr(digest, "build_digest", lambda: pytest.fail("holiday fetch"))
    assert digest.main([]) == 0
    path = tmp_path / "absent.sqlite"
    result = daily.deliver(lambda _: pytest.fail("holiday send"), db_path=path,
                           now=moment("2026-09-07T20:10:00"), force=True)
    assert result["status"] == "market_closed"
    assert not path.exists()


def test_dry_run_alerts_never_send_or_save(monkeypatch, capsys):
    clock(monkeypatch, "2026-09-08T14:00:00")
    monkeypatch.setattr(digest, "build_digest", lambda: (["digest"], []))
    monkeypatch.setattr(digest, "load_state", lambda: {})
    monkeypatch.setattr(digest, "diff_regimes", lambda *args: ["test crossing"])
    monkeypatch.setattr(digest, "push", lambda _: pytest.fail("dry run send"))
    monkeypatch.setattr(digest, "save_state", lambda _: pytest.fail("dry run state mutation"))
    assert digest.main(["--alerts-only", "--dry-run"]) == 0
    assert "test crossing" in capsys.readouterr().out


def test_first_alert_run_seeds_baseline_without_sending(monkeypatch):
    clock(monkeypatch, "2026-09-08T14:00:00")
    rows = [{"ticker": "SPY"}]
    saved = []
    monkeypatch.setattr(digest, "build_digest", lambda: (["digest"], rows))
    monkeypatch.setattr(digest, "load_state", lambda: {})
    monkeypatch.setattr(digest, "push", lambda _: pytest.fail("no crossing"))
    monkeypatch.setattr(digest, "save_state", saved.append)
    assert digest.main(["--alerts-only", "--enable-legacy-notifications"]) == 0
    assert saved == [rows]


def test_retired_scheduled_sender_is_silent(monkeypatch):
    monkeypatch.setattr(digest, 'build_digest', lambda: pytest.fail('retired job fetched'))
    assert digest.main([]) == 0
    assert digest.main(['--alerts-only']) == 0


def test_crossings_require_new_fresh_same_day_provider_timestamps():
    before = {"ticker": "SPY", "regime": "negative", "as_of": "2026-09-08T13:58:00Z"}
    after = {"ticker": "SPY", "regime": "positive", "as_of": "2026-09-08T13:59:00Z"}
    now = moment("2026-09-08T14:00:00")
    assert len(digest.diff_regimes([before], [after], now)) == 1
    for timestamp in [None, "bad", "2026-09-08T13:58:00Z", "2026-09-08T14:01:00Z", "2026-09-04T20:00:00Z"]:
        assert digest.diff_regimes([before], [{**after, "as_of": timestamp}], now) == []
    assert digest.diff_regimes([before], [after], moment("2026-09-08T14:10:00")) == []
    assert digest.diff_regimes([{**before, "as_of": "2026-09-04T20:00:00Z"}], [after], now) == []
    assert digest.diff_regimes([{**before, "regime": "unknown"}], [after], now) == []


@pytest.mark.parametrize("flip", [None, float("nan"), 0])
def test_missing_gamma_is_unknown(monkeypatch, flip):
    monkeypatch.setattr(digest.tools, "dispatch", lambda *args: {"spot": 100, "near_walls": {"gamma_flip_level": flip}})
    assert digest.regime_rows(["SPY"])[0]["regime"] == "unknown"


def test_digest_uses_only_internal_portfolio_account_data(monkeypatch):
    calls = []
    def dispatch(name, args):
        calls.append(name)
        assert name not in {"get_account", "get_trade_history", "get_positions"}
        return {"portfolio": {"combined_marked_equity": 24699.2, "daily_realized_pnl": 0,
                              "portfolios": [{"open_positions": 0}]}}
    monkeypatch.setattr(digest.tools, "dispatch", dispatch)
    monkeypatch.setattr(digest, "regime_rows", lambda _: [])
    monkeypatch.setattr(digest, "load_state", lambda: {})
    chunks, _ = digest.build_digest()
    assert "get_paper_portfolio" in calls
    assert "Cipher local paper portfolios" in "".join(chunks)
    assert "24,699.20" in "".join(chunks)
