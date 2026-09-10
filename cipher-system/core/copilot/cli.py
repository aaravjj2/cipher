"""Command-line surface for Cipher Copilot.

    python -m core.copilot "is NVDA pinning into Friday?"
    python -m core.copilot --tool get_gex_matrix --args '{"ticker":"NVDA"}'
    python -m core.copilot --repl
    python -m core.copilot "..." --json --trace

Exit codes: 0 answered, 1 usage/config error, 2 provider failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from core.copilot import engine, tools


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="core.copilot", description="Read-only Cipher research copilot")
    parser.add_argument("question", nargs="*", help="the financial question to answer")
    parser.add_argument("--tool", help="call one registered tool directly instead of a full query")
    parser.add_argument("--args", default="{}", help="JSON object of tool arguments (with --tool)")
    parser.add_argument("--repl", action="store_true", help="interactive multi-turn session")
    parser.add_argument("--provider", help="pin one LLM provider: groq|openrouter|anthropic")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit the raw result object")
    parser.add_argument("--trace", action="store_true", help="print the tool trace to stderr")
    parser.add_argument("--tools", action="store_true", help="list registered tools and exit")
    return parser


def _print_tools() -> None:
    for name, meta in tools.TOOL_SPECS.items():
        params = ", ".join(meta["params"]) or "-"
        print(f"{name:24s} {meta['description']} [{params}]")


def run_tool_command(name: str, args_json: str) -> int:
    try:
        args = json.loads(args_json)
    except ValueError as exc:
        print(f"invalid --args JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(args, dict):
        print("--args must be a JSON object", file=sys.stderr)
        return 1
    result = tools.dispatch(name, args)
    print(json.dumps(result, indent=2, default=str))
    return 0


def run_query_once(question: str, *, provider: str | None, as_json: bool, show_trace: bool) -> int:
    result = engine.run_query(question, provider_pin=provider)
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("answer") or "")
        if result.get("error"):
            print(f"\n[error] {result['error']}", file=sys.stderr)
    if show_trace:
        for entry in result.get("tool_trace") or []:
            print(f"tool {entry['tool']} {json.dumps(entry['args'])} -> {entry['chars']} chars", file=sys.stderr)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"provider={result.get('provider')} at {stamp}", file=sys.stderr)
    return 0 if not result.get("error") else 2


def run_repl(provider: str | None) -> int:
    history: list[dict] = []
    print("Cipher Copilot REPL — read-only research. Blank line or 'exit' quits.")
    while True:
        try:
            line = input("copilot> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line or line.lower() in {"exit", "quit"}:
            return 0
        result = engine.run_query(line, history=history, provider_pin=provider)
        answer = result.get("answer") or ""
        print(answer)
        if result.get("error"):
            print(f"[error] {result['error']}", file=sys.stderr)
            continue
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": answer})


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    opts = parser.parse_args(argv)
    if opts.tools:
        _print_tools()
        return 0
    if opts.tool:
        return run_tool_command(opts.tool, opts.args)
    question = " ".join(opts.question).strip()
    if not question and not opts.repl:
        parser.print_usage()
        return 1
    if opts.repl and not question:
        return run_repl(opts.provider)
    if opts.repl:
        # Seed the repl with a first question so `--repl "query"` starts warm.
        code = run_query_once(question, provider=opts.provider, as_json=opts.as_json, show_trace=opts.trace)
        if code != 0:
            return code
        return run_repl(opts.provider)
    return run_query_once(question, provider=opts.provider, as_json=opts.as_json, show_trace=opts.trace)


if __name__ == "__main__":
    raise SystemExit(main())
