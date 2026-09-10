"""Engine loop, provider selection and usage-cap tests - all providers faked."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.copilot import engine, providers


@pytest.fixture(autouse=True)
def isolated_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "USAGE_PATH", tmp_path / "copilot" / "usage.json")
    yield


class FakeProvider:
    """Scripted turns: each call pops one scripted response; a response is
    either text (final answer) or tool calls the engine must run."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, name, messages, specs):
        self.calls.append([dict(m) for m in messages])
        if not self.script:
            return {"text": "script exhausted", "tool_calls": []}
        step = self.script.pop(0)
        if isinstance(step, str):
            return {"text": step, "tool_calls": []}
        return {"text": "", "tool_calls": step}


def _patch_providers(monkeypatch, fake, order=("groq",)):
    monkeypatch.setattr(providers, "available_providers", lambda: list(order))
    monkeypatch.setattr(engine.providers, "available_providers", lambda: list(order))
    monkeypatch.setattr(engine.providers, "chat", fake)
    monkeypatch.setattr(engine.tools, "dispatch", lambda name, args: {"fake": True, "tool": name})


def test_full_tool_round_then_answer(monkeypatch):
    fake = FakeProvider([
        [{"id": "t1", "name": "get_quote", "arguments": '{"ticker":"NVDA"}'}],
        "NVDA last is 214.11 [alpaca_quote].",
    ])
    _patch_providers(monkeypatch, fake)

    result = engine.run_query("what is NVDA doing?", record_usage=False)

    assert result["provider"] == "groq"
    assert result["answer"].startswith("NVDA")
    assert result["tool_trace"] == [{"tool": "get_quote", "args": {"ticker": "NVDA"}, "chars": result["tool_trace"][0]["chars"]}]
    # The tool result must have been fed back as a tool-role message.
    second_call_messages = fake.calls[1]
    roles = [m["role"] for m in second_call_messages]
    assert roles.count("tool") == 1


def test_provider_fallback_before_any_output(monkeypatch):
    calls = {"n": 0}

    def failing_then_working(name, messages, specs):
        calls["n"] += 1
        if name == "groq":
            raise providers.ProviderError("groq", "rate limited")
        return {"text": "fallback answer", "tool_calls": []}

    monkeypatch.setattr(providers, "available_providers", lambda: ["groq", "openrouter"])
    monkeypatch.setattr(engine.providers, "available_providers", lambda: ["groq", "openrouter"])
    monkeypatch.setattr(engine.providers, "chat", failing_then_working)

    result = engine.run_query("hello", record_usage=False)
    assert result["answer"] == "fallback answer"
    assert result["provider"] == "openrouter"


def test_all_providers_failing_reports_errors(monkeypatch):
    def always_fail(name, messages, specs):
        raise providers.ProviderError(name, "down")

    monkeypatch.setattr(providers, "available_providers", lambda: ["groq"])
    monkeypatch.setattr(engine.providers, "available_providers", lambda: ["groq"])
    monkeypatch.setattr(engine.providers, "chat", always_fail)

    result = engine.run_query("hello", record_usage=False)
    assert result["provider"] is None
    assert "groq: down" in result["error"]


def test_daily_usage_cap_blocks(monkeypatch):
    engine.check_and_record_usage()
    with pytest.raises(engine.CopilotError):
        for _ in range(engine.DAILY_LIMIT_DEFAULT + 2):
            engine.check_and_record_usage()


def test_empty_question_rejected():
    with pytest.raises(engine.CopilotError):
        engine.run_query("   ", record_usage=False)


def test_history_is_bounded_and_roles_filtered():
    history = [{"role": "system", "content": "evil"}] + [
        {"role": "user", "content": str(i)} for i in range(30)
    ]
    bounded = engine._bounded_history(history)
    assert len(bounded) <= engine.MAX_HISTORY_MESSAGES
    assert all(row["role"] in {"user", "assistant"} for row in bounded)


def test_deterministic_mode_without_providers(monkeypatch):
    monkeypatch.setattr(providers, "available_providers", lambda: [])
    monkeypatch.setattr(engine.providers, "available_providers", lambda: [])
    monkeypatch.setattr(
        engine.tools,
        "dispatch",
        lambda name, args: {
            "stores": [],
            **(
                {"last": 100.0, "bid": 99.0, "ask": 101.0, "day_change_pct": 1.0, "source": "alpaca_quote", "as_of": "2026-08-25T14:00:00+00:00"}
                if name == "get_quote"
                else {}
            ),
            **(
                {"call_wall_strike": 105.0, "put_wall_strike": 95.0, "gamma_flip_strike": 100.0, "source": "local_capture_gex", "as_of": "2026-08-25T14:00:00+00:00"}
                if name == "get_gex_matrix"
                else {}
            ),
        },
    )
    result = engine.run_query("SPY recap", record_usage=False)
    assert result["provider"] is None
    assert "Deterministic mode" in result["answer"]
    assert "GROQ_API_KEY" in result["answer"]


def test_system_prompt_carries_guardrails():
    prompt = engine.SYSTEM_PROMPT.lower()
    assert "read-only" in prompt
    assert "never recommend buying/selling" in prompt or "not advice" in prompt


def test_batched_tool_calls_run_and_replay_in_order(monkeypatch):
    """Multiple tool calls in one turn must all dispatch, replay in original
    order, and keep their ids aligned with the right results."""
    import time

    delays = {"get_quote": 0.15, "get_market_health": 0.05}

    def slow_dispatch(name, args):
        time.sleep(delays.get(name, 0.01))
        return {"tool": name}

    fake = FakeProvider([
        [
            {"id": "a", "name": "get_quote", "arguments": '{"ticker":"NVDA"}'},
            {"id": "b", "name": "get_market_health", "arguments": "{}"},
            {"id": "c", "name": "get_bars", "arguments": '{"ticker":"SPY"}'},
        ],
        "done",
    ])
    _patch_providers(monkeypatch, fake)
    monkeypatch.setattr(engine.tools, "dispatch", slow_dispatch)

    started = time.monotonic()
    result = engine.run_query("batch test", record_usage=False)
    elapsed = time.monotonic() - started

    assert result["answer"] == "done"
    # Parallel execution: wall time near the slowest call, not the sum.
    assert elapsed < sum(delays.values()) + 0.5
    trace_tools = [entry["tool"] for entry in result["tool_trace"]]
    assert trace_tools == ["get_quote", "get_market_health", "get_bars"]
    # Tool messages must be id-aligned with the assistant's requested order.
    second_call = fake.calls[1]
    tool_msgs = [m for m in second_call if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b", "c"]
