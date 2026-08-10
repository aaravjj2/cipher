"""Guards for describe_provider_error — the text a user sees when the LLM fails.

An LLM-provider failure is the one error in Ask Cipher the user can actually fix
themselves (add credits, replace a key), and they can only fix it if the bubble says
which one it was. The SDK's own repr buries that under a nested error dict, so these
tests pin two things: the cause is named, and the provider's own words are still
carried through rather than replaced with a summary of our own invention.

The last test is the important one — an unrecognised failure must NOT be dressed up
as a known, tidy cause.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import ask_cipher  # noqa: E402


class _ProviderError(Exception):
    """Stands in for openai.APIStatusError: same duck type (status_code + body)."""

    def __init__(self, status_code, body, text="raw sdk repr"):
        super().__init__(text)
        self.status_code = status_code
        self.body = body


OUT_OF_CREDITS = (
    "This request requires more credits, or fewer max_tokens. You requested up to "
    "1024 tokens, but can only afford 679."
)

# The shape an actual openai.APIStatusError carries: the SDK has already unwrapped the
# gateway's {"error": {...}} envelope, so `body` IS the inner object. This was captured
# from a live OpenRouter 402 -- an earlier version of this test assumed the nested shape
# and passed while the real path silently fell through to the raw repr.
SDK_BODY = {"message": OUT_OF_CREDITS, "code": 402, "metadata": {"limit_source": "openrouter_credits"}}
# Some gateways return it still nested; both are accepted.
NESTED_BODY = {"error": {"message": OUT_OF_CREDITS, "code": 402}}


def test_402_names_credits_as_the_cause():
    msg = ask_cipher.describe_provider_error(_ProviderError(402, SDK_BODY))
    assert "out of credits" in msg
    # The provider's own numbers survive: they are the actionable part.
    assert "1024" in msg and "679" in msg


def test_the_nested_envelope_shape_is_also_understood():
    msg = ask_cipher.describe_provider_error(_ProviderError(402, NESTED_BODY))
    assert "1024" in msg and "679" in msg


def test_the_sdk_repr_is_not_what_the_user_reads():
    raw = (
        "APIStatusError: Error code: 402 - {'error': {'message': '...', "
        "'previous_errors': [{'code': 402}, {'code': 402}, {'code': 402}]}}"
    )
    msg = ask_cipher.describe_provider_error(_ProviderError(402, SDK_BODY, text=raw))
    assert "previous_errors" not in msg
    assert len(msg) < 400


def test_401_is_reported_as_a_key_problem_not_a_billing_one():
    msg = ask_cipher.describe_provider_error(
        _ProviderError(401, {"message": "No auth credentials found"})
    )
    assert "rejected" in msg and "key" in msg
    assert "credits" not in msg


def test_429_is_reported_as_rate_limiting():
    msg = ask_cipher.describe_provider_error(
        _ProviderError(429, {"message": "Slow down"})
    )
    assert "rate-limit" in msg


def test_an_unmapped_status_still_reports_the_status_and_the_provider_text():
    msg = ask_cipher.describe_provider_error(
        _ProviderError(503, {"message": "upstream unavailable"})
    )
    assert "503" in msg
    assert "upstream unavailable" in msg


def test_a_non_http_failure_is_not_dressed_up_as_a_provider_verdict():
    """A local bug must not be reported as somebody else's billing problem."""
    msg = ask_cipher.describe_provider_error(KeyError("get_standing"))
    assert msg.startswith("KeyError")
    for invented in ("credits", "rate-limit", "rejected", "HTTP"):
        assert invented not in msg


def test_a_string_error_body_is_handled():
    msg = ask_cipher.describe_provider_error(_ProviderError(402, {"error": "flat string"}))
    assert "flat string" in msg


def test_a_bodyless_status_error_falls_back_to_the_exception_text():
    msg = ask_cipher.describe_provider_error(_ProviderError(402, None, text="boom"))
    assert "out of credits" in msg
    assert "boom" in msg
