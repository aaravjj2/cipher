# Aarav Finance

Repo-local Codex plugin for source-first, read-only investment research.

## Dedicated agent

The reliable project-level skill invocation is:

```text
$aarav-finance <your research request>
```

Plugin-capable Codex surfaces may also expose:

```text
/aarav-finance:finance <your research request>
```

Examples:

```text
/aarav-finance:finance Give me a full SPY market-structure brief.
/aarav-finance:finance Compare NVDA fundamentals with its current options positioning.
/aarav-finance:finance Find earnings dislocations in semiconductor infrastructure.
```

The `$aarav-finance` skill is recommended because some Codex surfaces do not
expose plugin `commands/` as slash commands. The agent automatically checks the
local Cipher core when it is running. It can
read quote, bars, Strike Matrix/GEX/VEX, Night Vision, Spyglass flow, Setup
Scanner, scanner history, GEX replay, research status, ranking, and weight-lab
diagnostics. If the local core is offline, it reports that limitation and uses
only authenticated external sources that are available.

The plugin combines external MCP sources with Cipher's local quantitative
engine:

- Equibles: independent SEC/FINRA/FRED/13F/insider/congressional verification.
- FlashAlpha: options exposure and earnings analytics. Treat its dealer-position
  labels as provider analytics, not ground truth.
- Cipher: local Alpaca-backed chain, GEX/VEX, scanner, replay, backtest, and
  research-governance surfaces.

No credentials are stored here. Authenticate remote MCP connections through
Codex. Cipher remains read-only: this plugin must not submit orders, connect to
broker account endpoints, or promote a research result to live execution.

Options Chain MCP is documented as an optional fallback in the options skill;
Cipher's Alpaca feed remains the primary chain source for this checkout.

The external LLMQuant and Finance Skills projects are intentionally not vendored
into this private checkout. Their overlapping workflows are represented by the
local skills here, so the research rules can be audited alongside Cipher and
remain aligned with its no-orders boundary.
