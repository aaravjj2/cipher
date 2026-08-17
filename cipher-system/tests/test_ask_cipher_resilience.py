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


def test_workspace_context_is_available_to_openai_compatible_providers():
    specs = {row["function"]["name"]: row["function"] for row in ask_cipher._OPENAI_TOOL_SPECS}
    assert specs["get_workspace_context"]["parameters"] == {
        "type": "object", "properties": {}, "required": []
    }
    result = ask_cipher._dispatch_openai_tool(
        "get_workspace_context", {}, {"get_workspace_context": lambda: {
            "ticker": "NVDA", "read_only": True, "execution_capability": False,
        }}
    )
    assert '"ticker": "NVDA"' in result
    assert '"execution_capability": false' in result


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


def _provider_keys(monkeypatch, **present):
    """Control which provider keys appear configured."""
    monkeypatch.setattr(ask_cipher, "_anthropic_api_key", lambda: present.get("anthropic"))
    monkeypatch.setattr(ask_cipher, "_groq_api_key", lambda: present.get("groq"))
    monkeypatch.setattr(ask_cipher, "_openrouter_api_key", lambda: present.get("openrouter"))
    monkeypatch.setattr(ask_cipher, "_env_key", lambda name: present.get("pin") if name == "CIPHER_ASK_PROVIDER" else None)


def _spy_runners(monkeypatch, failing=()):
    """Replace each provider runner with a recorder that optionally raises."""
    called: list[str] = []

    def make(name):
        def runner(message, history, tools, append_event, key):
            called.append(name)
            if name in failing:
                raise _ProviderRefusal(402, f"{name} is out of credits")
            append_event({"type": "done", "text": f"answered by {name}"})
        return runner

    for name, attr in (("anthropic", "_run_chat_job_anthropic"),
                       ("groq", "_run_chat_job_groq"),
                       ("openrouter", "_run_chat_job_openrouter")):
        monkeypatch.setattr(ask_cipher, attr, make(name))
    return called


def test_groq_is_tried_before_openrouter(monkeypatch):
    """OpenRouter's balance is the thing that failed, so it is the last resort."""
    _provider_keys(monkeypatch, groq="g", openrouter="o")
    called = _spy_runners(monkeypatch)
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    assert called == ["groq"]
    assert events[-1]["text"] == "answered by groq"


def test_anthropic_wins_when_configured(monkeypatch):
    _provider_keys(monkeypatch, anthropic="a", groq="g", openrouter="o")
    called = _spy_runners(monkeypatch)
    ask_cipher.run_chat_job("hi", [], {}, [].append)
    assert called == ["anthropic"]


def test_a_failed_provider_falls_through_to_the_next(monkeypatch):
    _provider_keys(monkeypatch, groq="g", openrouter="o")
    called = _spy_runners(monkeypatch, failing={"groq"})
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    assert called == ["groq", "openrouter"]
    assert any(e["type"] == "notice" and "trying openrouter" in e["text"] for e in events)
    assert events[-1]["text"] == "answered by openrouter"


def test_a_provider_that_already_streamed_text_is_not_replaced(monkeypatch):
    """Splicing a second model's turn onto a partial answer is worse than the error."""
    _provider_keys(monkeypatch, groq="g", openrouter="o")
    called: list[str] = []

    def half_then_fail(message, history, tools, append_event, key):
        called.append("groq")
        append_event({"type": "text_delta", "text": "partial "})
        raise _ProviderRefusal(500, "died mid-answer")

    monkeypatch.setattr(ask_cipher, "_run_chat_job_groq", half_then_fail)
    monkeypatch.setattr(ask_cipher, "_run_chat_job_openrouter",
                        lambda *a, **k: called.append("openrouter"))
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    assert called == ["groq"], "must not continue into a second provider after streaming"
    assert events[-1]["type"] == "error"


def test_every_provider_failing_reports_all_of_them(monkeypatch):
    _provider_keys(monkeypatch, groq="g", openrouter="o")
    _spy_runners(monkeypatch, failing={"groq", "openrouter"})
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    error = events[-1]
    assert error["type"] == "error"
    assert "groq" in error["error"] and "openrouter" in error["error"]


def test_a_pinned_provider_is_the_only_one_tried(monkeypatch):
    _provider_keys(monkeypatch, groq="g", openrouter="o", pin="openrouter")
    called = _spy_runners(monkeypatch)
    ask_cipher.run_chat_job("hi", [], {}, [].append)
    assert called == ["openrouter"]


def test_an_unknown_pinned_provider_is_refused_rather_than_ignored(monkeypatch):
    _provider_keys(monkeypatch, groq="g", pin="grok")
    called = _spy_runners(monkeypatch)
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    assert called == []
    # Grok (xAI) is not Groq; silently falling back would hide the typo.
    assert "grok" in events[-1]["error"] and "groq" in events[-1]["error"]


def test_no_configured_provider_names_all_three_keys(monkeypatch):
    _provider_keys(monkeypatch)
    events = []
    ask_cipher.run_chat_job("hi", [], {}, events.append)
    for key in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        assert key in events[-1]["error"]


def test_groq_does_not_inherit_openrouters_shrunken_budget(monkeypatch):
    """The learned budget is an OpenRouter affordability figure and must not leak."""
    ask_cipher._remember_budget(300)
    seen = {}
    reply = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop")])

    def create(**kwargs):
        seen.update(kwargs); return reply

    _install(monkeypatch, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    ask_cipher._run_chat_job_groq("hi", [], {}, [].append, "key")
    assert seen["max_tokens"] == ask_cipher.GROQ_MAX_TOKENS
    assert seen["model"] == ask_cipher.GROQ_MODEL
