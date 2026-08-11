import json
from types import SimpleNamespace

import pytest

from core import options_backtest_jobs as jobs


def test_options_jobs_are_allowlisted_and_have_no_execution_capability(monkeypatch):
    monkeypatch.setattr(jobs.threading.Thread, "start", lambda self: None)
    job_id = jobs.start_job("weekly_bullish_debit")
    job = jobs.get_job(job_id)
    assert job["research_only"] is True
    assert job["execution_capability"] is False
    assert [row["id"] for row in jobs.protocols()] == list(jobs.PROTOCOLS)
    with pytest.raises(ValueError):
        jobs.start_job("../../arbitrary")


def test_options_job_captures_bounded_json_result(monkeypatch):
    monkeypatch.setattr(jobs.threading.Thread, "start", lambda self: None)
    job_id = jobs.start_job("fixed_width")
    jobs._run(job_id, run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps({"status": "ok"}), stderr=""))
    job = jobs.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"status": "ok"}
