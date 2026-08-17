---
description: Invoke the dedicated Aarav Finance research agent for stocks, options, earnings, valuation, macro, portfolio risk, and local Cipher analytics.
argument-hint: [finance research request]
allowed-tools: [Read, Glob, Grep, Bash, WebFetch]
---

# Aarav Finance

If this Codex surface does not recognize plugin slash commands, use the
project-level `$aarav-finance` skill instead.

The user invoked the dedicated finance-only agent with:

```text
$ARGUMENTS
```

Follow these instructions:

1. Read `skills/aarav-finance-agent/SKILL.md` completely.
2. Treat the request as financial research only. If it is not finance-related,
   explain that this agent is scoped to finance and ask for a finance question.
3. Check local Cipher availability with a bounded read-only request to
   `http://127.0.0.1:8282/health`. If available, use the smallest relevant GET
   routes from the skill. Do not read secrets or use POST routes.
4. Use connected Equibles and FlashAlpha MCP sources when appropriate.
   Prefer primary evidence and preserve source attribution.
5. Apply the standard response format and hard execution boundary from the
   skill. Hypothetical option payoffs are allowed; broker actions are not.
6. If local Cipher or an external source is unavailable, say exactly what is
   unavailable and continue with the evidence that remains.

Examples:

```text
/aarav-finance:finance Analyze NVDA's current fundamentals and Cipher options structure.
/aarav-finance:finance Give me a SPY GEX/VEX and Night Vision market brief.
/aarav-finance:finance Review the latest AMD earnings dislocation and compare it with IV.
/aarav-finance:finance Find multi-bagger candidates in semiconductor infrastructure.
```
