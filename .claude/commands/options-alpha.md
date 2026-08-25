---
description: Run one Options Alpha paper-trade decision loop per the agent playbook
allowed-tools: mcp__cipher-market_get_quote, mcp__cipher-market_get_gex_levels, mcp__cipher-market_decision_quality, mcp__cipher-market_autopilot_status, mcp__cipher-market_paper_ledger_summary, Bash(python3 *pretrade_gate.py*), Bash(python3 *agent_decision_log.py*)
---

# Options Alpha Agent — one loop iteration

Follow `cipher-system/docs/agent/AGENT_CHARTER.md` (law) and
`cipher-system/docs/agent/PLAYBOOK.md` (procedure) exactly. Paper only.

Inputs I will give you after this line (ticker is required; thesis hint optional):

$ARGUMENTS

Procedure:

1. **Research** — call cipher-market tools for the ticker: `get_quote`,
   `get_gex_levels`, then `decision_quality` and `autopilot_status` once.
2. **Thesis** — write the idea with every input cited (quote fields, gamma
   flip level, wall distances), plus what would falsify it. If the evidence
   does not support a trade, say so and stop: refusals are results.
3. **Gate** — run:
   `python3 cipher-system/scripts/pretrade_gate.py --decision-id agent_$(date -u +%Y%m%dT%H%M%S)_<TICKER> --ticker <TICKER>`
   On BLOCKED: append BLOCKED to the decision log with the gate's JSON as the
   reason payload and stop.
4. **Intent** — on PASS, append INTENT to
   `/home/aarav/Aarav/cipher/runtime/data/agent_decision_log.jsonl` using
   `agent_decision_log.py append --event INTENT --payload '<json>'`. The
   rationale field must be your step-2 thesis verbatim. Use the same
   decision_id the gate returned PASS for.
5. **Order** — via the alpaca-trading MCP server, place ONE single-leg option
   contract order on the ticker, limit price at ask + 2% slippage rounded to
   $0.01, quantity 1, day TIF, paper account. Choose the nearest weekly expiry
   and the strike closest to ~25-delta in the trade direction from
   `get_gex_levels` chain context; state both choices in the intent notes if
   the MCP tool accepts notes.
6. **Log outcome** — append SUBMITTED with the broker order id; poll the order
   until terminal; append FILLED (price/qty) or UNFILLED (status).
7. **Verify** — call `paper_ledger_summary`; confirm the position appears;
   append RECONCIALIZED with matches_local_ledger set accordingly.

Report back: verdict per step, the decision_id, and any BLOCKED reasons —
plainly, without spin. Two consecutive tool failures: append BLOCKED and stop.
