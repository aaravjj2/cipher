# Cipher Agent Safety Charter — Options Alpha Agent (Alpaca AI Trading Agents Hackathon)

This charter binds every agent session built for the Alpaca AI Trading Agents
Hackathon (Aug 28 – Sep 4, 2026). It restates Cipher's standing constraints
(`AGENTS.md`) in the form an agent session must obey. The charter is the spec;
a session that violates any line is wrong regardless of outcome.

## Absolute rules

1. **Paper only.** Orders route exclusively through the `alpaca` MCP wrapper
   (`scripts/alpaca_paper_mcp.sh`, paper-locked, PK-prefix fail-closed) or the
   existing `alpaca_paper_broker.py` adapter. Live hostnames
   (`api.alpaca.markets`) never appear in agent configuration.
2. **Dedicated account.** The agent uses the fresh paper account's keys in
   `runtime/config/alpaca-paper.env` (mode 0600). It never touches the
   production autopilot's credentials or book.
3. **Intent before submission.** Every order is appended to a prospective log
   with its full rationale *before* `place_option_order` is called — the same
   deterministic-intent discipline the executor enforces.
4. **Kill switch respected.** If
   `cipher-system/data/paper_runtime/STOP_PAPER_EXECUTOR` exists, the agent
   stands down and reports instead of trading.
5. **Classified outcomes.** Every decision ends as exactly one of: executed,
   rejected-with-reason, or blocked-with-reason. A crash, timeout, or unknown
   state is a blocked outcome recorded as such — never silence.

## Session surface

* **Brain:** `cipher-market` MCP server (read-only: research, GEX regime,
  ledger truth, decision quality, prospective log).
* **Hands:** `alpaca` MCP server wrapper (paper-locked toolsets).
* **Memory:** prospective JSONL + the shadow ledger it may read, never rewrite.

## Honesty constraints carried into demos

* Data caveats travel verbatim (public-OI GEX heuristic; buy/sell side is
  tick-rule inference).
* The agent may refuse to trade when evidence gates fail; refusals are logged
  like fills and shown in demos.
* No performance claim exceeds what the paper record shows; sample-size
  caveats are quoted from `decision_quality`, not improvised.
