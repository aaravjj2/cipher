from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest

from core.paper_executor.cohorts import configurations, SharedObservations, CohortRuntime, CohortGroup
from core.paper_executor.config import InstrumentConfig
from core.paper_executor.service import PaperExecutorApp
from test_paper_executor_runtime import MockMarketData, runtime, signal


def test_shared_observations_survive_restart_and_overlap(tmp_path):
    md = MockMarketData()
    shared = SharedObservations(md, tmp_path / "observations.sqlite")
    first, second = shared.view(), shared.view()
    with first.scope("a"):
        quotes = first.quotes([md.option_symbol, "NVDA"])
    md.option_bid = 9
    with second.scope("a"):
        assert second.quotes([md.option_symbol])[md.option_symbol] == quotes[md.option_symbol]
    restarted = SharedObservations(md, shared.path).view()
    with restarted.scope("a"):
        assert restarted.quotes([md.option_symbol])[md.option_symbol].bid == 1
    with restarted.scope("b"):
        assert restarted.quotes([md.option_symbol])[md.option_symbol].bid == 9


def test_four_ledgers_frozen_versions_distinct_candidates_and_dedup(tmp_path):
    md = MockMarketData()
    template = runtime(tmp_path, md).cfg
    cfg = replace(template, instrument=InstrumentConfig(model="debit_spread"))
    shared = SharedObservations(md, tmp_path / "cohorts" / "observations.sqlite")
    apps = [PaperExecutorApp(c, market_data=shared.view(), runtime_class=CohortRuntime) for c in configurations(cfg)]
    for app in apps:
        app.runtime.clock = lambda: md.now
        app.runtime.quote_manager.clock = lambda: md.now
    group = CohortGroup(apps)
    assert len({a.cfg.database_path for a in apps}) == 4
    assert len({a.cfg.server.control_token_path for a in apps}) == 4
    assert len({a.cfg.server.port for a in apps}) == 4
    first = signal(md.now)
    assert all(r["accepted"] for r in group.ingest(first)["cohorts"].values())
    for app in apps:
        app.runtime.drain_for_tests()
    assert [len(a.db.rows("paper_positions")) for a in apps] == [1, 0, 0, 1]
    group.ingest(first)
    for app in apps:
        app.runtime.drain_for_tests()
    assert len(apps[1].db.rows("paper_positions")) == 0
    md.now += timedelta(minutes=5)
    group.ingest({**signal(md.now), "batch_id": "b2"})
    for app in apps:
        app.runtime.drain_for_tests()
    assert [len(a.db.rows("paper_positions")) for a in apps] == [1, 1, 0, 1]
    assert len(group.summary()) == 4
    for app in apps:
        for row in app.db.rows("paper_positions"):
            assert json.loads(row["payload_json"])["entry_evidence"]["cohort_id"] == app.cfg.experiment.cohort_id
    apps[0].runtime.config_hash = "changed"
    with pytest.raises(ValueError, match="Frozen experiment changed"):
        CohortGroup(apps)


def test_primary_changes_only_at_session_boundary_and_rolls_back(tmp_path, monkeypatch):
    md = MockMarketData()
    app = PaperExecutorApp(runtime(tmp_path, md).cfg, market_data=md)
    group = CohortGroup([app])
    candidate = {"cohort_id": "confirmation", "evaluation": {"current": {"promotion_eligible": True, "expectancy_usd": 20}}}
    monkeypatch.setattr(group, "summary", lambda: [candidate])
    group.maintain(datetime(2026, 9, 7, 13, 15, tzinfo=timezone.utc))  # Holiday
    assert group.primary["cohort_id"] == "baseline"
    group.maintain(datetime(2026, 9, 8, 14, 15, tzinfo=timezone.utc))  # Already intraday
    assert group.primary["cohort_id"] == "baseline"
    group.maintain(datetime(2026, 9, 9, 13, 15, tzinfo=timezone.utc))
    assert group.primary["cohort_id"] == "confirmation"
    candidate["evaluation"]["current"]["promotion_eligible"] = False
    group.maintain(datetime(2026, 9, 9, 13, 20, tzinfo=timezone.utc))
    assert group.primary["cohort_id"] == "confirmation"
    group.maintain(datetime(2026, 9, 10, 13, 15, tzinfo=timezone.utc))
    assert group.primary["cohort_id"] == "baseline"
    assert json.loads(group.primary_path.read_text())["reason"] == "candidate_gates_not_met"
