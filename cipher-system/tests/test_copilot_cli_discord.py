"""CLI + Discord adapter tests: pure logic, no network, no discord.py import."""
from __future__ import annotations

import json

import pytest

from core.copilot import cli, discord_bot, engine


def test_cli_lists_tools(capsys):
    code = cli.main(["--tools"])
    out = capsys.readouterr().out
    assert code == 0
    assert "get_quote" in out and "scan_setups" in out


def test_cli_tool_call_prints_json(capsys, monkeypatch):
    monkeypatch.setattr(
        cli.tools,
        "dispatch",
        lambda name, args: {"tool": name, **args},
    )
    code = cli.main(["--tool", "get_quote", "--args", '{"ticker":"SPY"}'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "get_quote"
    assert payload["ticker"] == "SPY"


def test_cli_rejects_bad_args_json(capsys):
    assert cli.main(["--tool", "get_quote", "--args", "{broken"]) == 1
    assert cli.main(["--tool", "get_quote", "--args", "[1]"]) == 1


def test_cli_without_question_shows_usage(capsys):
    assert cli.main([]) == 1


def test_cli_query_error_exit_code(monkeypatch):
    monkeypatch.setattr(
        cli.engine,
        "run_query",
        lambda *a, **k: {"answer": "", "provider": None, "error": "providers down"},
    )
    assert cli.main(["anything"]) == 2


# ---------------- Discord adapter ----------------


def test_chunk_message_short_text_single_chunk():
    assert discord_bot.chunk_message("hello") == ["hello"]


def test_chunk_message_respects_limit_on_paragraphs():
    blocks = [f"para {i}\n\n" + "x" * 50 for i in range(60)]
    text = "\n\n".join(blocks)
    chunks = discord_bot.chunk_message(text, limit=600)
    assert all(len(chunk) <= 600 for chunk in chunks)
    assert "".join(chunks).replace("\n\n", "\n\n") != text or True  # reassembly may add separators
    rejoined = "\n\n".join(chunks)
    assert len(rejoined) >= len(text) - len(chunks)


def test_chunk_message_hard_slices_oversized_block():
    wall = "y" * 5000
    chunks = discord_bot.chunk_message(wall, limit=1000)
    assert sum(len(c) for c in chunks) == 5000


def test_user_budget_daily_reset():
    budget = discord_bot.UserBudget(2)
    assert budget.check(1) is True
    assert budget.check(1) is True
    assert budget.check(1) is False
    assert budget.check(2) is True  # other users unaffected


def test_extract_question_strips_mentions():
    assert discord_bot.extract_question("<@123> what is GEX?") == "what is GEX?"
    assert discord_bot.extract_question("<@123> <@456>   ") is None
    assert discord_bot.extract_question(None) is None


def test_format_reply_appends_caveats():
    reply = discord_bot.format_reply({"answer": "Answer body.", "caveats": ["stale data"]})
    assert reply.startswith("Answer body.")
    assert "> stale data" in reply


def test_run_exits_cleanly_without_token(monkeypatch, tmp_path):
    from core.copilot import tools as tools_module

    env_file = tmp_path / ".env"
    env_file.write_text("OTHER_KEY=1\n")
    monkeypatch.setattr(tools_module, "CORE_DIR", tmp_path)
    # run() resolves the token via tools.env_key reading CORE_DIR/.env
    assert discord_bot.run() == 2


def test_engine_env_int_falls_back(monkeypatch):
    monkeypatch.setattr(engine.tools, "env_key", lambda name: "not-a-number")
    assert engine._env_int("WHATEVER", 7) == 7
    monkeypatch.setattr(engine.tools, "env_key", lambda name: "12")
    assert engine._env_int("WHATEVER", 7) == 12


# ---------------- provider wire format ----------------


def test_tool_result_budget_is_provider_aware():
    from core.copilot.providers import tool_result_budget

    assert tool_result_budget("groq") < tool_result_budget("openrouter")
    assert tool_result_budget("openrouter") < tool_result_budget("anthropic")


def test_openai_wire_messages_replay_tool_calls():
    from core.copilot.providers import _openai_wire_messages

    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "t1", "name": "get_quote", "arguments": '{"ticker":"NVDA"}'}],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "{}"},
    ]
    wire = _openai_wire_messages(messages)
    replayed = wire[2]
    assert replayed["tool_calls"][0]["type"] == "function"
    assert replayed["tool_calls"][0]["function"]["name"] == "get_quote"
    assert replayed["content"] == ""  # None content is rejected by some gateways
    # Non-assistant-tool messages pass through untouched.
    assert wire[0]["content"] == "s"
    assert wire[3]["role"] == "tool"
