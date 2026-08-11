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


class _ProviderRefusal(Exception):
    """Shaped like the OpenAI SDK's error: status_code plus an unwrapped inner body."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"message": message, "code": status_code}


# The live 402 text this parser exists for.
_LIVE_402 = (
    "This request requires more credits, or fewer max_tokens. You requested up to "
    "1024 tokens, but can only afford 679. To increase your limits, add more credits."
)


def test_affordable_budget_is_read_from_the_provider_not_guessed():
    assert ask_cipher.affordable_max_tokens(_ProviderRefusal(402, _LIVE_402)) == 679
    # A 402 that names no number gives nothing to retry with.
    assert ask_cipher.affordable_max_tokens(_ProviderRefusal(402, "out of credits")) is None
    # Only affordability refusals are eligible; a 429 must not be retried at a new budget.
    assert ask_cipher.affordable_max_tokens(_ProviderRefusal(429, _LIVE_402)) is None


def _client_that_refuses_once(refusal, reply="the answer", finish_reason="stop"):
    """A client that raises `refusal` on the first call, then answers. Records budgets."""
    budgets = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=reply, tool_calls=[]), finish_reason=finish_reason
        )]
    )

    def create(**kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            raise refusal
        return response

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), budgets


def _install(monkeypatch, client):
    monkeypatch.setitem(
        __import__("sys").modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: client)
    )


def test_unaffordable_budget_is_retried_at_the_number_the_provider_named(monkeypatch):
    client, budgets = _client_that_refuses_once(_ProviderRefusal(402, _LIVE_402))
    _install(monkeypatch, client)
    events = []

    ask_cipher._run_chat_job_openrouter("hi", [], {}, events.append, "key")

    assert budgets == [ask_cipher.OPENROUTER_MAX_TOKENS, 679]
    # The reduction is disclosed rather than applied behind the reader's back.
    notice = next(row for row in events if row["type"] == "notice")
    assert "679" in notice["text"]
    assert events[-1] == {"type": "done", "text": "the answer"}


def test_a_budget_too_small_to_answer_is_refused_instead_of_stubbed(monkeypatch):
    too_small = _LIVE_402.replace("can only afford 679", "can only afford 12")
    client, budgets = _client_that_refuses_once(_ProviderRefusal(402, too_small))
    _install(monkeypatch, client)

    with pytest.raises(_ProviderRefusal):
        ask_cipher._run_chat_job_openrouter("hi", [], {}, [].append, "key")
    # It never retried: 12 tokens cannot carry an answer worth returning.
    assert budgets == [ask_cipher.OPENROUTER_MAX_TOKENS]


def test_the_reduced_budget_still_refuses_a_truncated_answer(monkeypatch):
    """The retry must not become a way to smuggle a cut-off answer past the guard."""
    client, budgets = _client_that_refuses_once(
        _ProviderRefusal(402, _LIVE_402), reply="half an ans", finish_reason="length"
    )
    _install(monkeypatch, client)
    events = []

    with pytest.raises(ask_cipher.AskCipherError, match="token limit"):
        ask_cipher._run_chat_job_openrouter("hi", [], {}, events.append, "key")

    assert budgets == [ask_cipher.OPENROUTER_MAX_TOKENS, 679]
    assert not any(row["type"] == "done" for row in events)


@pytest.fixture(autouse=True)
def _forget_learned_budget():
    """The learned budget is module state; leaking it between tests hides ordering bugs."""
    ask_cipher._remember_budget(None)
    yield
    ask_cipher._remember_budget(None)


def test_the_affordable_budget_is_remembered_for_the_next_question(monkeypatch):
    """Otherwise every question spends a failed call rediscovering the same number."""
    client, budgets = _client_that_refuses_once(_ProviderRefusal(402, _LIVE_402))
    _install(monkeypatch, client)
    ask_cipher._run_chat_job_openrouter("hi", [], {}, [].append, "key")
    assert budgets == [ask_cipher.OPENROUTER_MAX_TOKENS, 679]
    assert ask_cipher._learned_budget() == 679

    # A second question starts at the learned budget: one call, no 402.
    calls = []
    reply = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="second", tool_calls=[]), finish_reason="stop")])
    def create(**kwargs):
        calls.append(kwargs["max_tokens"]); return reply
    _install(monkeypatch, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    events = []
    ask_cipher._run_chat_job_openrouter("again", [], {}, events.append, "key")
    assert calls == [679], "second question did not start at the remembered budget"
    assert any("679" in str(e.get("text","")) for e in events if e["type"] == "notice")


def test_truncation_at_the_remembered_budget_forgets_it(monkeypatch):
    """So added credits are discovered on the next question instead of needing a restart."""
    ask_cipher._remember_budget(300)
    truncated = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="cut off", tool_calls=[]), finish_reason="length")])
    _install(monkeypatch, SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: truncated))))

    with pytest.raises(ask_cipher.AskCipherError, match="token limit"):
        ask_cipher._run_chat_job_openrouter("hi", [], {}, [].append, "key")
    assert ask_cipher._learned_budget() is None, "a too-small remembered budget must not stick"


def test_truncation_at_the_full_ceiling_does_not_set_a_budget(monkeypatch):
    """Truncation at 1024 is not an affordability signal, so nothing should be remembered."""
    truncated = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="cut off", tool_calls=[]), finish_reason="length")])
    _install(monkeypatch, SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: truncated))))
    with pytest.raises(ask_cipher.AskCipherError, match="token limit"):
        ask_cipher._run_chat_job_openrouter("hi", [], {}, [].append, "key")
    assert ask_cipher._learned_budget() is None
