"""Cipher Copilot engine: the grounded query loop.

`run_query(question)` drives one provider through at most MAX_TOOL_ROUNDS of
tool calls against the registry in tools.py, then returns a structured result:

    {"answer": markdown, "provider": str|None, "tool_trace": [...], "caveats": [...]}

Grounding rules live in SYSTEM_PROMPT and are enforced socially (the model sees
them) plus mechanically (every number the model can quote had to come through a
tool result this turn). When no provider is configured the engine degrades to
DETERMINISTIC MODE: it detects tickers in the question, pulls the matching raw
evidence itself, and prints it clearly labelled as data-not-narrative — never a
silent failure.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from core.copilot import providers, tools

MAX_TOOL_ROUNDS = 6
MAX_QUESTION_CHARS = 8_000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 6_000
MAX_TOOL_RESULT_CHARS = 30_000
MAX_PARALLEL_TOOLS = 4

ROOT = Path(__file__).resolve().parents[1]
USAGE_PATH = ROOT / "data" / "copilot" / "usage.json"
DAILY_LIMIT_DEFAULT = 200
_USAGE_LOCK = threading.Lock()

TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

SYSTEM_PROMPT = """You are Cipher Copilot, a read-only trading RESEARCH copilot for Cipher, \
a personal options/equity research system with rich locally captured market data.

How to answer:
- Decide which tools give you the evidence you need; call them BEFORE writing numbers.
- The user has a paper trading account: when a question mentions "my position(s)", \
"my account", entries/cost basis, P&L, or recent trades, call get_positions / \
get_account / get_trade_history first and ground the answer in those actuals.
- For anything about the user's own data (prices, GEX, flow, scanner reads, earnings models, \
portfolio, journal), every number you state MUST come from a tool result in this conversation. \
Cite provenance inline like [alpaca_quote @ 2026-08-25T14:00Z] or [local_capture_gex @ ...].
- Call get_market_health first when freshness matters (intraday questions) and disclose stale data.
- For general finance education (concepts, mechanics like IV crush / pinning / theta decay), \
answer from knowledge but prefix that section with "[knowledge]" so it is never confused with data.

Hard rules:
- NEVER recommend buying/selling a specific security. Describe what the data shows, lay out \
tradeoff structures if asked, and let the user decide. This is research, not advice.
- Never invent a number, date, strike, or verdict about Cipher's data. If no tool covers it, say so. \
This includes earnings dates, guidance claims, and news events: anything not in a tool result \
this turn is either labeled "[knowledge]" (and flagged as unverified) or left out entirely - \
never blended into the evidence section.
- Disclose staleness and gaps plainly instead of smoothing over them.
- GEX figures are an open-interest heuristic, not verified dealer positioning - keep that caveat.

Style: concise markdown. Lead with the direct answer, then an evidence section (small tables \
where tabular), then caveats. No filler preamble."""


class CopilotError(ValueError):
    """Raised before any provider call when the request itself is invalid."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(tools.env_key(name) or default)
    except ValueError:
        return default


def check_and_record_usage() -> None:
    """One shared daily budget across CLI + Discord so neither surface can
    silently drain the provider balances the other depends on."""
    today = datetime.now(timezone.utc).date().isoformat()
    limit = _env_int("CIPHER_COPILOT_DAILY_LIMIT", DAILY_LIMIT_DEFAULT)
    with _USAGE_LOCK:
        try:
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8")) if USAGE_PATH.is_file() else {}
        except (OSError, ValueError):
            data = {}
        count = data.get(today, 0)
        if count >= limit:
            raise CopilotError(f"Daily copilot limit reached ({limit} queries/day). Try again tomorrow.")
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps({today: count + 1}), encoding="utf-8")


def normalize_question(question: str) -> str:
    prompt = str(question or "").strip()
    if not prompt:
        raise CopilotError("question is required")
    if len(prompt) > MAX_QUESTION_CHARS:
        raise CopilotError(f"question exceeds {MAX_QUESTION_CHARS} characters")
    return prompt


def _bounded_history(history: list[dict] | None) -> list[dict]:
    kept: list[dict] = []
    used = 0
    for row in reversed((history or [])[-MAX_HISTORY_MESSAGES:]):
        content = str(row.get("content") or "")
        if kept and used + len(content) > MAX_HISTORY_CHARS:
            break
        kept.insert(0, {"role": row.get("role", "user"), "content": content})
        used += len(content)
    return [{"role": r["role"], "content": r["content"]} for r in kept if r["role"] in {"user", "assistant"}]


def run_query(
    question: str,
    history: list[dict] | None = None,
    *,
    provider_pin: str | None = None,
    record_usage: bool = True,
) -> dict:
    prompt = normalize_question(question)
    if record_usage:
        check_and_record_usage()

    specs = tools.to_openai_specs()
    base_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _bounded_history(history) + [
        {"role": "user", "content": prompt}
    ]
    messages = [dict(m) for m in base_messages]

    order = ([provider_pin] if provider_pin else providers.available_providers())
    tool_trace: list[dict] = []
    # Bound tool payloads to the preferred provider's context reality up front:
    # if groq answers, oversized history would have already caused HTTP 413;
    # if it fails anyway, tighter results remain valid for every fallback.
    tool_chars = providers.tool_result_budget(order[0]) if order else 30_000

    if not order:
        answer = deterministic_digest(prompt)
        return {
            "answer": answer,
            "provider": None,
            "tool_trace": [],
            "caveats": ["No LLM provider configured - raw evidence only, no narrative."],
        }

    failures: list[str] = []
    for index, name in enumerate(order):
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                turn = providers.chat(name, messages, specs)
                text = turn.get("text") or ""
                calls = turn.get("tool_calls") or []
                if not calls:
                    return {
                        "answer": text.strip() or "(empty response)",
                        "provider": name,
                        "tool_trace": tool_trace,
                        "caveats": _collect_caveats(tool_trace),
                    }
                assistant_msg: dict = {"role": "assistant", "content": text or None, "tool_calls": calls}
                messages.append(assistant_msg)

                def run_one(call: dict) -> tuple[str, str]:
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    result = tools.dispatch(call["name"], args)
                    return tools.bounded_json(result, tool_chars), json.dumps(args, sort_keys=True)

                # Independent tool calls run concurrently: a matrix fetch costs
                # seconds of OPRA paging, and models frequently batch 3-4 of them.
                with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_TOOLS, len(calls))) as pool:
                    encoded_pairs = list(pool.map(run_one, calls))
                for call, (encoded, args_key) in zip(calls, encoded_pairs):
                    tool_trace.append({"tool": call["name"], "args": json.loads(args_key or "{}"), "chars": len(encoded)})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": encoded})
            # Loop exhausted while still demanding tools: force a final answer.
            messages.append(
                {
                    "role": "user",
                    "content": "Tool budget reached. Answer now from the evidence already gathered.",
                }
            )
            final = providers.chat(name, messages, specs)
            return {
                "answer": (final.get("text") or "").strip(),
                "provider": name,
                "tool_trace": tool_trace,
                "caveats": _collect_caveats(tool_trace) + ["Answer produced at the tool-round limit."],
            }
        except Exception as exc:  # noqa: BLE001 - try next provider before any output streamed
            failures.append(f"{name}: {providers.describe_failure(exc)}")
            if index < len(order) - 1:
                continue
    return {
        "answer": "",
        "provider": None,
        "tool_trace": tool_trace,
        "caveats": failures + ["Every configured LLM provider failed."],
        "error": " | ".join(failures),
    }


def _collect_caveats(tool_trace: list[dict]) -> list[str]:
    caveats = []
    seen = set()
    for entry in tool_trace:
        key = json.dumps(entry.get("args") or {}, sort_keys=True)
        if key in seen or entry["tool"] != "get_gex_matrix":
            seen.add(key)
            continue
        seen.add(key)
        caveats.append("GEX figures are an open-interest heuristic, not verified dealer positioning.")
    return caveats


# --------------------------------------------------------------------------
# Deterministic mode: no provider keys, still useful
# --------------------------------------------------------------------------


def deterministic_digest(question: str) -> str:
    """Raw-evidence fallback. Deliberately narrative-free: it lists what the
    stores hold for the tickers mentioned rather than pretending to reason."""
    candidates = sorted({m.group(1) for m in TICKER_RE.finditer(question.upper())})
    focus = [t for t in candidates if t not in {"A", "I", "THE", "FOR", "AND", "IS", "IT", "TO", "OF", "ON", "MY", "DO", "WHAT", "HOW", "WHY"}][:4]
    lines = ["**Deterministic mode** (no LLM provider configured) - raw evidence, no narrative.", ""]
    health = tools.dispatch("get_market_health", {})
    chain_stores = [s for s in (health.get("stores") or []) if str(s.get("store", "")).startswith("chain_capture")]
    freshest = min((s["age_minutes"] for s in chain_stores if s.get("age_minutes") is not None), default=None)
    lines.append(f"- Chain captures tracked: {len(chain_stores)}; freshest {freshest if freshest is not None else 'unknown'} min old.")
    for ticker in focus or ["SPY"]:
        quote = tools.dispatch("get_quote", {"ticker": ticker})
        gex = tools.dispatch("get_gex_matrix", {"ticker": ticker})
        if quote.get("error"):
            lines.append(f"\n## {ticker}\n- quote unavailable: {quote['error']}")
        else:
            lines.append(
                f"\n## {ticker}\n"
                f"- last {quote.get('last')} bid/ask {quote.get('bid')}/{quote.get('ask')} "
                f"({quote.get('day_change_pct'):+}% today) [{quote.get('source')} @ {quote.get('as_of')}]"
            )
        if gex.get("error"):
            lines.append(f"- gex unavailable: {gex['error']}")
        else:
            lines.append(
                f"- net GEX walls: call {gex.get('call_wall_strike')} / put {gex.get('put_wall_strike')}, "
                f"flip ~{gex.get('gamma_flip_strike')} [{gex.get('source')} @ {gex.get('as_of')}]"
            )
    lines.append("\nSet GROQ_API_KEY or OPENROUTER_API_KEY in cipher-system/.env for full answers.")
    return "\n".join(lines)
