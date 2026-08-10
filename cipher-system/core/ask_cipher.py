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


def _anthropic_api_key() -> str | None:
    """Reads ANTHROPIC_API_KEY from .env (same manual parse local_settings()
    uses in core/app.py), falling back to the process environment. Returns
    None rather than raising -- this feature must degrade to a clear "not
    configured" error, not take down the whole core service."""
    import os

    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() == "ANTHROPIC_API_KEY":
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
    return os.environ.get("ANTHROPIC_API_KEY") or None


class AskCipherError(ValueError):
    """Raised before any API call is attempted (no key configured, guardrail
    exceeded). Subclasses ValueError so app.py's existing
    `except ValueError as exc: send_json(422, ...)` handles it with no new
    plumbing, matching the convention core/holdings.py already established."""


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


def run_chat_job(message: str, history: list[dict], tool_impls: dict[str, Callable], append_event: Callable) -> None:
    """Runs one chat turn against Claude, streaming events back via
    `append_event`. Never raises to the caller -- every failure path (missing
    key, API error, a refused request) is reported as an `error` event so the
    job registry always reaches a terminal state.
    """
    api_key = _anthropic_api_key()
    if not api_key:
        append_event({"type": "error", "error": "ANTHROPIC_API_KEY is not configured in .env"})
        return

    try:
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
        for stream in runner:
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "tool_use":
                    append_event({"type": "tool_call", "name": event.content_block.name})
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    final_text += event.delta.text
                    append_event({"type": "text_delta", "text": event.delta.text})

        append_event({"type": "done", "text": final_text})
    except Exception as exc:  # noqa: BLE001 - surface every failure as a terminal chat event
        append_event({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
