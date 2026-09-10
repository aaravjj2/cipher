"""LLM provider chain for Cipher Copilot.

Same provider philosophy as ask_cipher.py: try each configured provider until
one answers a *complete* turn; never splice a second model onto a partial
answer. Copilot is non-streaming (CLI/Discord surfaces want one string), which
lets the loop here stay small: one `chat()` call returns either final text or
the tool calls the engine must run.

Providers speak two shapes:
- OpenAI-compatible chat completions (Groq, OpenRouter) via the `openai` SDK
- Anthropic messages via the `anthropic` SDK

Keys resolve through tools.env_key so cipher-system/.env stays the single
secret surface.
"""
from __future__ import annotations

import json

from core.copilot.tools import env_key


class ProviderError(RuntimeError):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider


class NoProviderError(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            "copilot",
            "no LLM provider configured; set GROQ_API_KEY or OPENROUTER_API_KEY in cipher-system/.env",
        )


GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_MODEL_DEFAULT = "openai/gpt-oss-120b"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL_DEFAULT = "anthropic/claude-opus-5"
ANTHROPIC_MODEL_DEFAULT = "claude-opus-5"

MAX_TOKENS_OPENAI_COMPAT = 2048
# Groq's on-demand organisation has an 8k tokens-per-minute ceiling (see
# ask_cipher.py): 2048 completion + prompt fits without tripping HTTP 413.
MAX_TOKENS_ANTHROPIC = 4096

# Per-provider tool-result char budgets fed back into the conversation. Groq's
# 8k TPM ceiling means a handful of fat tool results alone exceed the whole
# request budget (HTTP 413), so its results are bounded tightly - the same
# tradeoff ask_cipher.py makes with GROQ_TOOL_RESULT_CHARS=4000.
TOOL_RESULT_BUDGETS = {"groq": 3_500, "openrouter": 30_000, "anthropic": 120_000}


def tool_result_budget(provider: str) -> int:
    """Char budget for each tool result inserted into the message list."""
    return TOOL_RESULT_BUDGETS.get(provider, 30_000)


def _model_for(provider: str) -> tuple[str, int]:
    if provider == "groq":
        return env_key("CIPHER_COPILOT_GROQ_MODEL") or env_key("CIPHER_GROQ_MODEL") or GROQ_MODEL_DEFAULT, MAX_TOKENS_OPENAI_COMPAT
    if provider == "openrouter":
        return env_key("CIPHER_COPILOT_OPENROUTER_MODEL") or OPENROUTER_MODEL_DEFAULT, 1024
    return env_key("CIPHER_COPILOT_ANTHROPIC_MODEL") or ANTHROPIC_MODEL_DEFAULT, MAX_TOKENS_ANTHROPIC


def available_providers() -> list[str]:
    """Configured providers in preference order, or the single pinned provider
    when CIPHER_COPILOT_PROVIDER is set. Groq outranks Anthropic here (unlike
    ask_cipher.py) because this deployment currently holds a Groq key and not an
    Anthropic one; pinning overrides either way."""
    keys = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    order = [p for p in ("groq", "anthropic", "openrouter") if env_key(keys[p])]
    pinned = (env_key("CIPHER_COPILOT_PROVIDER") or "").strip().lower()
    if pinned:
        if pinned not in keys:
            raise ProviderError("copilot", f"CIPHER_COPILOT_PROVIDER={pinned!r} is not one of {sorted(keys)}")
        return [pinned] if pinned in order else []
    return order


def any_provider_configured() -> bool:
    try:
        return bool(available_providers())
    except ProviderError:
        return False


def describe_failure(exc: BaseException) -> str:
    """One actionable sentence per provider failure, mirroring ask_cipher's
    describe_provider_error but without chat-UI framing."""
    if isinstance(exc, ProviderError):
        return str(exc)
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    detail = ""
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            detail = str(inner["message"])
        elif isinstance(inner, str):
            detail = inner
        elif isinstance(body.get("message"), str):
            detail = body["message"]
    causes = {
        401: "rejected the API key",
        402: "is out of credits",
        403: "refused this request",
        404: "does not know this model",
        413: "rejected the request size",
        429: "is rate-limiting this key",
    }
    cause = causes.get(status)
    if status and (cause or detail):
        suffix = f" ({detail})" if detail and not cause else ""
        tail = f" Provider said: {detail}" if cause and detail else ""
        return f"provider returned HTTP {status}: {cause or 'request failed'}{suffix}{tail}"
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# Internal message shape (engine-facing)
#
#   {"role": "system"|"user"|"assistant", "content": str}
#   {"role": "assistant", "content": str|None,
#    "tool_calls": [{"id","name","arguments": json-str}]}
#   {"role": "tool", "tool_call_id": str, "content": str}
#
# Each adapter translates this to its wire format so the engine never cares.
# --------------------------------------------------------------------------


def _openai_wire_messages(messages: list[dict]) -> list[dict]:
    """Internal -> OpenAI wire shape. Replayed assistant turns must carry
    {"type": "function", "function": {name, arguments}} or Groq rejects the
    request (HTTP 400), which a raw internal-shape passthrough would trigger."""
    wire = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            wire.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call.get("arguments") or "{}"},
                        }
                        for call in msg["tool_calls"]
                    ],
                }
            )
            continue
        wire.append(msg)
    return wire


def _chat_openai_compat(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    messages: list[dict],
    specs: list[dict],
) -> dict:
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model, max_tokens=max_tokens, messages=_openai_wire_messages(messages), tools=specs
    )
    if not response.choices:
        raise ProviderError(provider, "response contained no choices")
    choice = response.choices[0]
    message = choice.message
    text = message.content or ""
    tool_calls = [
        {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
        for tc in (message.tool_calls or [])
    ]
    if choice.finish_reason == "length" and not text.strip():
        raise ProviderError(provider, "answer hit the token limit before any text was produced")
    return {"text": text, "tool_calls": tool_calls}


def _to_anthropic_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Translates the internal shape to Anthropic blocks. Anthropic requires
    strict role alternation, so consecutive `tool` results are folded into ONE
    user message of tool_result blocks, matching how ask_cipher feeds runners."""
    system_parts = [m.get("content") or "" for m in messages if m.get("role") == "system"]
    converted: list[dict] = []

    def flush_tool_results(buffer: list[dict]) -> None:
        if not buffer:
            return
        converted.append({"role": "user", "content": [{"type": "tool_result", **item} for item in buffer]})

    tool_buffer: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "tool":
            tool_buffer.append(
                {
                    "tool_use_id": msg.get("tool_call_id"),
                    "content": str(msg.get("content") or "")[:120_000],
                }
            )
            continue
        flush_tool_results(tool_buffer)
        tool_buffer = []
        calls = msg.get("tool_calls") or []
        if role == "assistant" and calls:
            blocks = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in calls:
                raw = call.get("arguments")
                try:
                    input_obj = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except ValueError:
                    input_obj = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or f"call_{len(blocks)}",
                        "name": call["name"],
                        "input": input_obj,
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": "user" if role == "user" else "assistant", "content": msg.get("content") or ""})
    flush_tool_results(tool_buffer)

    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": "(begin)"})
    return ("\n\n".join(p for p in system_parts if p) or None), converted


def _chat_anthropic(
    provider: str,
    api_key: str,
    model: str,
    max_tokens: int,
    messages: list[dict],
    specs: list[dict],
) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system_text, converted = _to_anthropic_messages(messages)
    tools = []
    for spec in specs:
        fn = spec.get("function", {})
        schema = json.loads(json.dumps(fn.get("parameters") or {"type": "object", "properties": {}}))
        tools.append({"name": fn.get("name"), "description": fn.get("description"), "input_schema": schema})
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_text,
        messages=converted,
        tools=tools,
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    tool_calls = [
        {"id": b.id or f"call_{i}", "name": b.name, "arguments": json.dumps(b.input)}
        for i, b in enumerate(response.content)
        if getattr(b, "type", "") == "tool_use"
    ]
    return {"text": text, "tool_calls": tool_calls}


def chat(provider: str, messages: list[dict], specs: list[dict]) -> dict:
    """One assistant turn against one provider. Raises ProviderError naming the
    failing provider; the engine decides whether falling back is safe."""
    if provider == "groq":
        key = env_key("GROQ_API_KEY")
        if not key:
            raise NoProviderError()
        model, budget = _model_for(provider)
        return _chat_openai_compat(provider, key, GROQ_BASE, model, budget, messages, specs)
    if provider == "openrouter":
        key = env_key("OPENROUTER_API_KEY")
        if not key:
            raise NoProviderError()
        model, budget = _model_for(provider)
        return _chat_openai_compat(provider, key, OPENROUTER_BASE, model, budget, messages, specs)
    if provider == "anthropic":
        key = env_key("ANTHROPIC_API_KEY")
        if not key:
            raise NoProviderError()
        model, budget = _model_for(provider)
        return _chat_anthropic(provider, key, model, budget, messages, specs)
    raise ProviderError("copilot", f"unknown provider {provider!r}")
