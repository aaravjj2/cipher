# Options Alpha Agent — Session Playbook

The exact procedure a Claude Code session follows to produce one paper trade.
Every step is either an MCP tool call or one of two scripts; nothing else is
on the happy path. The charter (`AGENT_CHARTER.md`) is law; this file is the
procedure that obeys it.

## Preconditions (checked once at session start)

1. `runtime/config/alpaca-paper.env` exists with PK-prefixed keys.
2. `.mcp.json` servers `cipher-market` and `alpaca-trading` are enabled.
3. Today's date has no more than the daily budget of new positions already
   (the gate re-checks anyway).

## The loop, per trade idea

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. RESEARCH   cipher-market tools:                              │
│      get_quote / get_gex_levels / gex_regime /                  │
│      decision_quality / autopilot_status                        │
│ 2. THESIS     cite every input: snapshot fields, gamma flip,    │
│               spread %, and what would falsify the idea         │
│ 3. GATE       pretrade_gate.py --decision-id <id> --ticker <T>  │
│      BLOCKED → log BLOCKED with the gate's reason; stop         │
│ 4. INTENT     agent_decision_log.py append --event INTENT ...   │
│      (rationale field = the step-2 thesis, verbatim)            │
│ 5. ORDER      alpaca-trading MCP place_option_order             │
│      limit at ask + executor slippage convention; quantity 1    │
│ 6. LOG        SUBMITTED (broker order id) → FILLED/UNFILLED     │
│ 7. VERIFY     paper_ledger_summary must show the position;      │
│      append RECONCIALIZED with matches_local_ledger true/false  │
└─────────────────────────────────────────────────────────────────┘
```

Decision IDs are `agent_<UTC yyyymmddTHHMMSS>_<ticker>` so they sort, are
unique without coordination, and read honestly in the ledger.

## Hard rules during the session

- Step 3 runs **every time**, even for the second idea in the same minute.
- Steps 4–6 happen only on PASS, in that order, with the same `decision_id`.
- If any step errors twice consecutively, append BLOCKED with the error and
  end the loop for the idea. Two failures mean something is wrong with the
  environment, not with the idea.
- Refusals are results. A session that logs six NO_TRADEs and zero fills had
  a successful day if the gates said so.
- Before reporting, run `agent_decision_log.py chain --decision-id <id>` for
  each decision of the session: anomalies are reported, never fixed in place.

## End-of-session report

Run `python3 cipher-system/scripts/agent_session_report.py` — it chains every
decision from today, counts outcomes, names anomalies and blocked reasons,
and renders the summary. Append `decision_quality` statistics and state
plainly: trades attempted, blocked count with reasons, fills reconciled
true/false, and the current expectancy caveat from the analyzer.
