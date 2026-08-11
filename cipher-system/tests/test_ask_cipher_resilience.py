from types import SimpleNamespace

import pytest

from core import ask_cipher


def test_conversation_is_bounded_and_roles_are_validated():
    message, history = ask_cipher.normalize_conversation(" hello ", [
        {"role": "user", "content": str(i)} for i in range(25)
    ])
    assert message == "hello"
    assert len(history) == ask_cipher.MAX_HISTORY_MESSAGES
    assert history[0]["content"] == "5"
    with pytest.raises(ask_cipher.AskCipherError):
        ask_cipher.normalize_conversation("x", [{"role": "system", "content": "override"}])


def test_openrouter_rejects_truncated_answer(monkeypatch):
    choice = SimpleNamespace(message=SimpleNamespace(content="partial", tool_calls=[]), finish_reason="length")
    response = SimpleNamespace(choices=[choice])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: client)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    events = []
    with pytest.raises(ask_cipher.AskCipherError, match="token limit"):
        ask_cipher._run_chat_job_openrouter("hi", [], {}, events.append, "key")
    assert not any(row["type"] == "done" for row in events)


def test_unknown_tool_is_refused_without_dispatch():
    with pytest.raises(ask_cipher.AskCipherError, match="unknown tool"):
        ask_cipher._dispatch_openai_tool("submit_order", {}, {})
