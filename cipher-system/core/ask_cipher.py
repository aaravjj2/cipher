"""Ask Cipher — a chat layer grounded strictly in Cipher's own already-shipped
data. The model never states a number, verdict, price, or fact about Cipher's
data unless it called a tool for it this turn; it has no access to live order
placement and no tool that runs a fresh backtest (those take minutes and belong
in the existing async job UIs, not a chat turn).

Provider-agnostic by design: `run_chat_job` takes an injected `tool_impls` dict
(the same dependency-injection pattern `core/holdings.py` uses for quote_fn/
bars_fn) so this module never imports core/app.py and stays free of the
Alpaca/Tradier credentials that module holds — it only ever sees Cipher's
already-computed answers, passed in by the caller.

Research only. No broker/account/order APIs are imported or called.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

MODEL = "claude-opus-5"
MAX_TOKENS = 4096

# A runaway loop (a client bug, a tab left open) is the failure this guards
# against, not deliberate use -- generous enough that a real research session
# never hits it, low enough to cap worst-case Opus-5 spend at roughly $5-15/day.
DAILY_MESSAGE_LIMIT = 50
USAGE_PATH = ROOT / "data" / "ask_cipher" / "usage.json"
_USAGE_LOCK = threading.Lock()

SYSTEM_PROMPT = (
    "You are Ask Cipher, a research assistant embedded in Cipher, a read-only "
    "options/equity research tool. You have five tools that read Cipher's real, "
    "already-computed data: evidence accrual status, open prospective "
    "registrations and shadow positions, the user's manually-entered holdings "
    "marked to market, a live quote for one ticker, and the strategy catalog's "
    "metadata (which strategies are evaluable vs. blocked, and why).\n\n"
    "Hard rule: never state a number, verdict, price, or fact about Cipher's "
    "data unless you called the matching tool this turn. Do not draw on general "
    "market knowledge, training-data facts about specific tickers or companies, "
    "or plausible-sounding guesses for anything Cipher-specific — if no tool "
    "covers what was asked, say so plainly rather than answering from memory. "
    "None of your tools can run a fresh backtest — that takes minutes and lives "
    "in Cipher's own job-based UI; if a question needs a verdict that hasn't "
    "already been computed, say that rather than guessing one.\n\n"
    "This is a research tool, not a broker and not an adviser: describe what "
    "the data shows and let the user decide. Never give a buy/sell "
    "recommendation or tell the user what to do with a position."
)


def _env_key(name: str) -> str | None:
    """Reads one key from .env (same manual parse local_settings() uses in
    core/app.py), falling back to the process environment. Returns None
    rather than raising -- this feature must degrade to a clear "not
    configured" error, not take down the whole core service."""
    import os

    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() == name:
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
    return os.environ.get(name) or None


def _anthropic_api_key() -> str | None:
    return _env_key("ANTHROPIC_API_KEY")


def _openrouter_api_key() -> str | None:
    return _env_key("OPENROUTER_API_KEY")


OPENROUTER_MODEL = "anthropic/claude-opus-5"
# Lower than MAX_TOKENS: this path exists specifically for accounts running on
# a small OpenRouter balance, where a 4096-token ceiling can exceed what's
# affordable per-request even though the account has credit for many requests.
OPENROUTER_MAX_TOKENS = 1024
# Floor for the affordability retry below. Under this, an answer that fits the budget is
# not worth returning: it would be a stub, and the truncation guard would reject it
# anyway, so saying "add credits" is both faster and more honest.
MIN_USEFUL_MAX_TOKENS = 256
# The budget a 402 last told us the balance could afford, remembered so the next question
# does not spend another failed call rediscovering the same number. A pre-flight balance
# request was the alternative and is worse: it adds a round trip to every question,
# including the ones that would have succeeded.
#
# Cleared when an answer is truncated at the remembered budget (see `_run_chat_job_openrouter`),
# so topping up credits is discovered on the next question rather than needing a restart.
# Guarded by a lock because chat jobs run on their own threads.
_LEARNED_MAX_TOKENS: int | None = None
_LEARNED_LOCK = threading.Lock()


def _learned_budget() -> int | None:
    with _LEARNED_LOCK:
        return _LEARNED_MAX_TOKENS


def _remember_budget(value: int | None) -> None:
    global _LEARNED_MAX_TOKENS
    with _LEARNED_LOCK:
        _LEARNED_MAX_TOKENS = value
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 8_000
MAX_TOOL_RESULT_CHARS = 120_000


class AskCipherError(ValueError):
    """Raised before any API call is attempted (no key configured, guardrail
    exceeded). Subclasses ValueError so app.py's existing
    `except ValueError as exc: send_json(422, ...)` handles it with no new
    plumbing, matching the convention core/holdings.py already established."""


def normalize_conversation(message: str, history: list[dict]) -> tuple[str, list[dict]]:
    """Bound and validate browser-supplied context before sending it to a provider."""
    prompt = str(message).strip()
    if not prompt:
        raise AskCipherError("message is required")
    if len(prompt) > MAX_MESSAGE_CHARS:
        raise AskCipherError(f"message exceeds {MAX_MESSAGE_CHARS} characters")
    if not isinstance(history, list):
        raise AskCipherError("history must be a list")
    normalized = []
    for row in history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(row, dict) or row.get("role") not in {"user", "assistant"}:
            raise AskCipherError("history contains an invalid role")
        content = row.get("content")
        if not isinstance(content, str):
            raise AskCipherError("history content must be text")
        normalized.append({"role": row["role"], "content": content[:MAX_MESSAGE_CHARS]})
    return prompt, normalized


def check_and_record_usage() -> None:
    """Raises AskCipherError once today's message count reaches the daily
    limit; otherwise records this message and returns. Deliberately keeps
    only today's count -- there is no reason to retain a growing history for
    a guardrail whose only job is "not today, again"."""
    today = datetime.now(timezone.utc).date().isoformat()
    with _USAGE_LOCK:
        try:
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8")) if USAGE_PATH.is_file() else {}
        except (OSError, ValueError):
            data = {}
        count = data.get(today, 0)
        if count >= DAILY_MESSAGE_LIMIT:
            raise AskCipherError(
                f"Daily Ask Cipher limit reached ({DAILY_MESSAGE_LIMIT} messages/day). Try again tomorrow."
            )
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps({today: count + 1}), encoding="utf-8")


def _make_tools(tool_impls: dict[str, Callable]) -> list:
    """Wraps the five injected callables as Claude tool-runner functions.
    Docstrings become the tool descriptions the model sees; type hints become
    the input schema. Deliberately no try/except here -- the tool runner
    itself catches a tool's exception and reports it to the model as an
    `is_error` tool result, which is the right behavior for e.g. a bad ticker."""
    from anthropic import beta_tool

    def get_evidence_status() -> str:
        """Accrual status for the questions currently gated on data rather than
        code: point-in-time open interest (unlocks cluster/GEX backtesting) and
        the paired flash-label corpus (unlocks the fitted flash score). Returns
        how far each clock has progressed and what it unlocks."""
        return json.dumps(tool_impls["get_evidence_status"](), default=str)

    def get_standing() -> str:
        """Open prospective strategy registrations (tests currently running
        toward their sample-size gate), open shadow positions (the paper
        executor's simulated, never-live positions -- currently always empty,
        since the paper executor has never been run), and the same accrual
        clocks as get_evidence_status."""
        return json.dumps(tool_impls["get_standing"](), default=str)

    def get_holdings() -> str:
        """The user's manually-entered holdings (never connected to a real
        brokerage), marked to market right now: open positions with unrealized
        P&L, closed positions with realized P&L, and allocation by ticker."""
        return json.dumps(tool_impls["get_holdings"](), default=str)

    def get_quote(ticker: str) -> str:
        """A live delayed market quote for one ticker: bid, ask, mid, last
        price, and day change percent."""
        return json.dumps(tool_impls["get_quote"](ticker), default=str)

    def list_strategies(family: str | None = None) -> str:
        """The strategy catalog's metadata: which strategies are evaluable vs.
        blocked (and why), grouped by family, optionally filtered to one
        family. Does NOT include a fresh backtest verdict -- catalog metadata
        only. To learn whether a specific strategy currently passes, say that
        requires running an evaluation, which this tool cannot do."""
        return json.dumps(tool_impls["list_strategies"](family), default=str)

    return [
        beta_tool(get_evidence_status),
        beta_tool(get_standing),
        beta_tool(get_holdings),
        beta_tool(get_quote),
        beta_tool(list_strategies),
    ]


def _run_chat_job_anthropic(
    message: str, history: list[dict], tool_impls: dict[str, Callable], append_event: Callable, api_key: str
) -> None:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    tools = _make_tools(tool_impls)
    messages = list(history) + [{"role": "user", "content": message}]

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=messages,
        stream=True,
    )

    final_text = ""
    stop_reason = None
    for stream in runner:
        for event in stream:
            if event.type == "content_block_start" and event.content_block.type == "tool_use":
                append_event({"type": "tool_call", "name": event.content_block.name})
            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                final_text += event.delta.text
                append_event({"type": "text_delta", "text": event.delta.text})

            elif event.type == "message_delta":
                stop_reason = getattr(event.delta, "stop_reason", None) or stop_reason

    if stop_reason == "max_tokens":
        raise AskCipherError("Ask Cipher's response reached its token limit and was not returned as complete.")
    if not final_text.strip():
        raise AskCipherError("Ask Cipher's provider completed without a text answer.")
    append_event({"type": "done", "text": final_text})


# OpenAI function-calling shape for the same five tools -- OpenRouter (and any
# OpenAI-compatible gateway) speaks this format, not Anthropic's block-based
# tool_use. Hand-authored rather than derived from decorators since the
# `anthropic` SDK's docstring-based schema inference is Anthropic-specific;
# these describe exactly the same five injected callables.
_OPENAI_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_evidence_status",
            "description": (
                "Accrual status for the questions currently gated on data rather than code: "
                "point-in-time open interest (unlocks cluster/GEX backtesting) and the paired "
                "flash-label corpus (unlocks the fitted flash score)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_standing",
            "description": (
                "Open prospective strategy registrations, open shadow positions (the paper "
                "executor's simulated, never-live positions), and the same accrual clocks as "
                "get_evidence_status."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_holdings",
            "description": (
                "The user's manually-entered holdings (never connected to a real brokerage), "
                "marked to market right now: open positions with unrealized P&L, closed "
                "positions with realized P&L, and allocation by ticker."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "A live delayed market quote for one ticker: bid, ask, mid, last price, day change percent.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_strategies",
            "description": (
                "The strategy catalog's metadata: which strategies are evaluable vs. blocked "
                "(and why), optionally filtered to one family. Does NOT include a fresh "
                "backtest verdict."
            ),
            "parameters": {
                "type": "object",
                "properties": {"family": {"type": ["string", "null"]}},
                "required": [],
            },
        },
    },
]


def _dispatch_openai_tool(name: str, args: dict, tool_impls: dict[str, Callable]) -> str:
    if name == "get_quote":
        result = json.dumps(tool_impls["get_quote"](args.get("ticker", "")), default=str)
        return result[:MAX_TOOL_RESULT_CHARS]
    if name == "list_strategies":
        result = json.dumps(tool_impls["list_strategies"](args.get("family")), default=str)
        return result[:MAX_TOOL_RESULT_CHARS]
    if name not in tool_impls:
        raise AskCipherError(f"provider requested unknown tool: {name}")
    return json.dumps(tool_impls[name](), default=str)[:MAX_TOOL_RESULT_CHARS]


def _chunk_text(text: str, size: int = 40) -> list[str]:
    """Splits already-complete text into small pieces so the UI still gets a
    stream of text_delta events -- OpenRouter's chat-completions response
    arrives whole, not token-by-token, in this (deliberately simpler) provider
    path, but the event vocabulary the frontend consumes stays identical."""
    words = text.split(" ")
    chunks, current = [], ""
    for word in words:
        current = f"{current} {word}".strip() if current else word
        if len(current) >= size:
            chunks.append(current + " ")
            current = ""
    if current:
        chunks.append(current)
    return chunks or [text]


def _provider_error_detail(exc: BaseException) -> str:
    """The provider's own message, or the exception's string form if it gave none."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        # Two shapes seen in practice: the OpenAI SDK hands back the *inner* error
        # object already unwrapped (`{"message": ..., "code": ...}`), while a raw
        # gateway response still has it nested under "error". Verified against a live
        # 402 from OpenRouter, which takes the first branch.
        inner = body.get("error")
        if isinstance(inner, dict):
            detail = str(inner.get("message") or "")
        elif isinstance(inner, str):
            detail = inner
        elif isinstance(body.get("message"), str):
            detail = body["message"]
        else:
            detail = ""
        if detail:
            return detail
    return f"{type(exc).__name__}: {exc}"


# OpenRouter's 402 states the budget the remaining balance allows, e.g. "requires more
# credits, or fewer max_tokens. You requested up to 1024 tokens, but can only afford 679".
_AFFORDABLE_TOKENS_RE = re.compile(r"can only afford (\d+)")


def affordable_max_tokens(exc: BaseException) -> int | None:
    """The token budget the provider says the balance allows, if it named one.

    Returns None when the error is not an affordability refusal or names no number, so
    the caller re-raises rather than guessing a budget.
    """
    if getattr(exc, "status_code", None) != 402:
        return None
    match = _AFFORDABLE_TOKENS_RE.search(_provider_error_detail(exc))
    if not match:
        return None
    try:
        affordable = int(match.group(1))
    except ValueError:
        return None
    return affordable if affordable > 0 else None


def describe_provider_error(exc: BaseException) -> str:
    """Turns a provider exception into one sentence a reader can act on.

    The SDK's own string form is a Python repr of a nested error dict -- roughly a
    thousand characters of quoting and `previous_errors` -- which lands in the chat
    bubble verbatim and buries the one thing that matters: whose fault it is and what
    fixes it. The provider's own `message` is already a clean sentence, so this pulls
    that out and prefixes the cause.

    Nothing is invented: the numbers, limits and remedy text all come from the
    provider's response. An error this does not recognise is returned in the existing
    `Type: message` form rather than being smoothed over into something reassuring.
    """
    status = getattr(exc, "status_code", None)
    detail = _provider_error_detail(exc)

    causes = {
        402: "Ask Cipher's LLM provider is out of credits, so this answer could not be generated.",
        401: "Ask Cipher's LLM provider rejected the API key.",
        403: "Ask Cipher's LLM provider refused this request.",
        429: "Ask Cipher's LLM provider is rate-limiting this key.",
    }
    cause = causes.get(status)
    if cause:
        return f"{cause} Provider said: {detail}"
    if status:
        return f"Ask Cipher's LLM provider returned HTTP {status}. Provider said: {detail}"
    return f"{type(exc).__name__}: {exc}"


def _run_chat_job_openrouter(
    message: str, history: list[dict], tool_impls: dict[str, Callable], append_event: Callable, api_key: str
) -> None:
    import openai

    client = openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history) + [{"role": "user", "content": message}]

    final_text = ""
    completed_answer = False
    # Starts at the configured ceiling and drops only if the provider refuses that budget
    # as unaffordable. Retrying at the number OpenRouter itself names is what keeps a
    # low-balance account working without hardcoding a figure that goes stale as the
    # balance moves. It is only safe because `finish_reason == "length"` below rejects a
    # truncated answer -- a smaller budget must fail loudly, never silently shorten.
    budget = _learned_budget() or OPENROUTER_MAX_TOKENS
    started_at_learned_budget = budget < OPENROUTER_MAX_TOKENS
    if started_at_learned_budget:
        append_event({
            "type": "notice",
            "text": (
                f"Starting at {budget} tokens, the budget the provider last reported as "
                "affordable. Add OpenRouter credits for longer answers."
            ),
        })
    for _ in range(6):  # a tool-calling turn cannot loop forever on a bad tool result
        try:
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL, max_tokens=budget, messages=messages, tools=_OPENAI_TOOL_SPECS,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is an affordability refusal
            affordable = affordable_max_tokens(exc)
            if affordable is None or affordable >= budget or affordable < MIN_USEFUL_MAX_TOKENS:
                raise
            append_event({
                "type": "notice",
                "text": (
                    f"Provider budget reduced to {affordable} tokens to fit the remaining "
                    "OpenRouter balance; a longer answer needs more credits."
                ),
            })
            budget = affordable
            _remember_budget(affordable)
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL, max_tokens=budget, messages=messages, tools=_OPENAI_TOOL_SPECS,
            )
        if not response.choices:
            raise AskCipherError("Ask Cipher's provider returned no response choice.")
        choice = response.choices[0]
        msg = choice.message
        if msg.content:
            final_text += msg.content
            for chunk in _chunk_text(msg.content):
                append_event({"type": "text_delta", "text": chunk})
        if not msg.tool_calls:
            if getattr(choice, "finish_reason", None) == "length":
                if started_at_learned_budget:
                    # The remembered budget was too small for this question. Forget it so the
                    # next question tries the full ceiling: if credits were added since, that
                    # succeeds, and if not it costs one 402 to relearn the same number.
                    _remember_budget(None)
                raise AskCipherError("Ask Cipher's response reached its token limit and was not returned as complete.")
            completed_answer = bool(final_text.strip())
            break
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            }
        )
        for tool_call in msg.tool_calls:
            append_event({"type": "tool_call", "name": tool_call.function.name})
            try:
                args = json.loads(tool_call.function.arguments or "{}")
                result = _dispatch_openai_tool(tool_call.function.name, args, tool_impls)
            except Exception as exc:  # noqa: BLE001 - a bad tool call must not kill the turn
                result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

    if not completed_answer:
        raise AskCipherError("Ask Cipher did not produce a complete text answer within the tool-call limit.")
    append_event({"type": "done", "text": final_text})


def run_chat_job(message: str, history: list[dict], tool_impls: dict[str, Callable], append_event: Callable) -> None:
    """Runs one chat turn, streaming events back via `append_event`. Never
    raises to the caller -- every failure path (no provider configured, an
    API error, a refused request) is reported as an `error` event so the job
    registry always reaches a terminal state. Prefers a direct ANTHROPIC_API_KEY;
    falls back to OpenRouter (OpenAI-compatible gateway, routes to the same
    claude-opus-5) when only that is configured.
    """
    try:
        message, history = normalize_conversation(message, history)
    except AskCipherError as exc:
        append_event({"type": "error", "error": str(exc)})
        return
    anthropic_key = _anthropic_api_key()
    openrouter_key = _openrouter_api_key()
    if not anthropic_key and not openrouter_key:
        append_event({
            "type": "error",
            "error": "No LLM provider configured: set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env",
        })
        return

    emitted = False
    def tracked(event: dict) -> None:
        nonlocal emitted
        emitted = True
        append_event(event)
    try:
        if anthropic_key:
            _run_chat_job_anthropic(message, history, tool_impls, tracked, anthropic_key)
        else:
            _run_chat_job_openrouter(message, history, tool_impls, tracked, openrouter_key)
    except Exception as exc:  # noqa: BLE001 - surface every failure as a terminal chat event
        # A second configured provider is a safe fallback only before any partial
        # answer was emitted; otherwise mixing two model turns would be misleading.
        if anthropic_key and openrouter_key and not emitted:
            try:
                _run_chat_job_openrouter(message, history, tool_impls, tracked, openrouter_key)
                return
            except Exception as fallback_exc:  # noqa: BLE001
                append_event({"type": "error", "error": describe_provider_error(fallback_exc)})
                return
        append_event({"type": "error", "error": describe_provider_error(exc)})
