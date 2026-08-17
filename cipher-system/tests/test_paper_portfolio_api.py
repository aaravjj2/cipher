from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import fronttest_portfolios, paper_portfolio_api  # noqa: E402


def test_snapshot_exposes_six_read_only_shadow_portfolios(tmp_path: Path) -> None:
    db_path = tmp_path / "fronttest.sqlite"
    db = fronttest_portfolios.connect(db_path)
    db.close()
    result = paper_portfolio_api.snapshot(db_path)
    assert result["portfolio_count"] == 6
    assert result["paper_only"] is True
    assert result["read_only"] is True
    assert result["execution_capability"] is False
    assert result["combined_starting_cash"] == 600_000
    assert result["opportunity_summary"]["signals"] == 0
    assert result["opportunity_summary"]["scope"] == "underlying_path_counterfactual"
    assert all(row["positions"] == [] and row["signals"] == [] for row in result["portfolios"])


def test_snapshot_audits_signal_disposition_position_and_equity(tmp_path: Path) -> None:
    db_path = tmp_path / "fronttest.sqlite"
    db = fronttest_portfolios.connect(db_path)
    db.execute(
        """insert into signals values
           ('s1','v6_nvda_p05','NVDA','P05','short','2026-08-13T14:00:00Z',
            '2026-08-13T14:01:00Z','{}','OPENED',null)"""
    )
    db.execute(
        """insert into signal_outcomes(
               signal_id,portfolio_id,symbol,status,outcome,evaluated_through,resolved_at,
               entry_underlying,exit_underlying,target,stop,bars_observed,mfe_pct,mae_pct,
               methodology,created_at,updated_at)
           values ('s1','v6_nvda_p05','NVDA','RESOLVED','TARGET',
                   '2026-08-13T15:00:00Z','2026-08-13T15:00:00Z',185,183,183,187,
                   12,1.2,.4,'underlying_path_only','2026-08-13T15:00:00Z','2026-08-13T15:00:00Z')"""
    )
    db.execute(
        """insert into positions (
           position_id,portfolio_id,signal_id,status,contract,option_type,expiration,
           strike,quantity,allocated_capital,entry_at,entry_bid,entry_ask,entry_fill,
           underlying_entry,exit_at,exit_bid,exit_ask,exit_fill,exit_reason,pnl,return_pct
           ) values ('p1','v6_nvda_p05','s1','CLOSED','NVDA260821P00180000','put',
           '2026-08-21',180,2,1000,'2026-08-13T14:01:00Z',4.8,5.0,5.0,185,
           '2026-08-13T15:00:00Z',5.8,6.0,5.8,'underlying_target',160,16)"""
    )
    db.commit()
    db.close()
    result = paper_portfolio_api.snapshot(db_path)
    row = next(x for x in result["portfolios"] if x["portfolio_id"] == "v6_nvda_p05")
    assert row["realized_pnl"] == 160
    assert row["realized_equity"] == 100_160
    assert row["wins"] == 1
    assert row["signals"][0]["disposition"] == "OPENED"
    assert row["signals"][0]["outcome"] == "TARGET"
    assert row["opportunity_summary"]["targets"] == 1
    assert row["positions"][0]["exit_reason"] == "underlying_target"
    assert row["equity_curve"][-1]["equity"] == 100_160


def test_snapshot_separates_realized_midpoint_and_liquidation_equity(tmp_path: Path) -> None:
    db_path = tmp_path / "fronttest.sqlite"
    db = fronttest_portfolios.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,
                                detected_at,payload_json,disposition)
             values ('open1','qqq_early','QQQ','early_bull','long',?,?,?,'OPENED')""",
        (now, now, '{}'),
    )
    db.execute(
        """insert into positions(position_id,portfolio_id,signal_id,status,contract,option_type,
             expiration,strike,quantity,allocated_capital,entry_at,entry_bid,entry_ask,entry_fill,
             underlying_entry,last_mark_at,last_bid,last_ask)
             values ('p-open','qqq_early','open1','OPEN','QQQ-C','call','2026-08-21',100,2,400,
                     ?,1.9,2.0,2.02,100,?,2.4,2.5)""", (now, now),
    )
    db.commit(); db.close()
    result = paper_portfolio_api.snapshot(db_path)
    row = next(item for item in result["portfolios"] if item["portfolio_id"] == "qqq_early")
    assert row["realized_equity"] == 100_000
    assert row["marked_equity"] == 100_086
    assert row["liquidation_equity"] < row["marked_equity"]
    assert row["positions"][0]["mark_status"] == "current"
    assert result["combined_marked_equity"] == 600_086
    assert result["normalized_comparison"]["ranked"] is False
