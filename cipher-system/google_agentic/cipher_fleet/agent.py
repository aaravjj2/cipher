"""Google ADK definition for Cipher's auditable market-research fleet."""
from __future__ import annotations

import os

from google.adk import Agent
from google.adk.apps.app import App

from .policy import CipherPolicyAuditPlugin
from .tools import (
    get_historical_evidence,
    get_market_structure,
    get_options_flow,
    get_risk_and_governance_review,
    get_strategy_validation,
)


MODEL = os.environ.get("CIPHER_AGENT_MODEL", "gemini-3.5-flash")

COMMON_BOUNDARY = """
You are one specialist in Cipher, a research-only market evidence system.
Use your tool for every Cipher-specific fact. Preserve timestamps, provenance,
coverage, and caveats. Missing information is unknown, never zero. Do not give
an instruction to transact, a position size, or an autonomous capital action.
Return measured evidence, conflicts, uncertainty, and what requires human
review. Never claim that public-open-interest GEX proves dealer positioning.
""".strip()


market_structure_agent = Agent(
    name="market_structure_agent",
    model=MODEL,
    description="Reads price, GEX/VEX structure, key levels, and session context.",
    instruction=(
        COMMON_BOUNDARY
        + "\nFocus on the current structural state. Distinguish observed price from "
        "derived exposure levels and report data coverage."
    ),
    tools=[get_market_structure],
    mode="single_turn",
)

options_flow_agent = Agent(
    name="options_flow_agent",
    model=MODEL,
    description="Reads recent option prints and quote-relative side inference.",
    instruction=(
        COMMON_BOUNDARY
        + "\nTreat side as an inference, not intent. Separate observed trade fields "
        "from interpretation and call out sparse or stale samples."
    ),
    tools=[get_options_flow],
    mode="single_turn",
)

historical_evidence_agent = Agent(
    name="historical_evidence_agent",
    model=MODEL,
    description="Reads bounded OHLCV history and captured GEX snapshot history.",
    instruction=(
        COMMON_BOUNDARY
        + "\nUse history only for context. State the sampling window and do not turn "
        "an association into a causal claim."
    ),
    tools=[get_historical_evidence],
    mode="single_turn",
)

strategy_validation_agent = Agent(
    name="strategy_validation_agent",
    model=MODEL,
    description="Reads strategy metadata, evidence gates, and prospective standing.",
    instruction=(
        COMMON_BOUNDARY
        + "\nA strategy is unsupported unless the returned evidence passes its declared "
        "gate. Clearly separate implemented, evaluable, blocked, and validated."
    ),
    tools=[get_strategy_validation],
    mode="single_turn",
)

risk_adversarial_agent = Agent(
    name="risk_adversarial_agent",
    model=MODEL,
    description="Challenges the thesis using governance state and known data limitations.",
    instruction=(
        COMMON_BOUNDARY
        + "\nAct as an adversarial reviewer. Look for stale inputs, missing provenance, "
        "unsupported confidence, contradictory evidence, and boundary violations."
    ),
    tools=[get_risk_and_governance_review],
    mode="single_turn",
)

root_agent = Agent(
    name="cipher_supervisor",
    model=MODEL,
    description="Delegates a research objective to Cipher specialists and reconciles evidence.",
    instruction="""
You supervise Cipher's auditable research fleet. Decompose the user's research
objective and delegate only to the specialists needed for the question. For a
full ticker review, consult market structure, options flow, historical evidence,
strategy validation, and risk/adversarial review. Do not invent a specialist's
result or a Cipher-specific number.

Reconcile the returned evidence into:
1. objective and data timestamp,
2. observed evidence by source,
3. agreement and conflict,
4. missing or stale evidence,
5. confidence with a plain-language basis,
6. invalidation conditions,
7. human-review decision.

This system autonomously assembles research, never deploys capital. Do not emit
a transaction instruction, quantity, or claim of authorization. Public-OI GEX
is a heuristic and side labels are inferred unless a source explicitly says
otherwise. Do not reveal hidden model reasoning; expose the evidence and concise
decision rationale that a reviewer can audit.
""".strip(),
    sub_agents=[
        market_structure_agent,
        options_flow_agent,
        historical_evidence_agent,
        strategy_validation_agent,
        risk_adversarial_agent,
    ],
    mode="chat",
)

# ADK's loader checks for ``app`` before ``root_agent``. Exporting both keeps
# the conventional entry point while ensuring the fleet-wide policy/audit
# plugin is active under ``adk run`` and ``adk web``.
app = App(
    name="cipher_fleet",
    root_agent=root_agent,
    plugins=[CipherPolicyAuditPlugin()],
)
