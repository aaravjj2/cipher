# Browser-first research handoff

Cipher Research Copilot is the durable research memory and workflow layer. The AI host's Browser, Chrome, or Computer capability performs the live website work.

## Run contract

1. Start with `start_daily_workflow` and use `get_next_cipher_step` to select the next screen.
2. Navigate only to the already-authorized Cipher session. Read the visible page; do not inspect cookies, local storage, saved passwords, network credentials, or hidden account data.
3. Capture a screenshot when it adds evidence, then call `attach_screenshot` with its local path.
4. Call `record_observation` with visible values, a short factual note, and a confidence level. Mark a step complete only after the relevant visible data has been recorded.
5. When every screen is complete, call `build_research_card` and `export_daily_report`.

## Guardrails

- Research only: do not submit trades, change watchlists, save/clear journal entries, alter API connections, or send support messages.
- Treat GEX, VEX, flow, scanner results, and chart levels as observed inputs—not trade instructions.
- Never put market-data keys, broker credentials, or session values into observations or reports.
- If a click would change an external account or publish data, stop and ask the user first.

## Suggested browser prompts

- "Start today's Cipher workflow for SPY, QQQ, and IWM; read every visible value on the Strike Matrix and record it."
- "Continue the current Cipher run, capture the Night Vision/X-Ray screen, record visible hot strikes and levels, and advance only if the data is complete."
- "Build and export the daily research card from the completed run; do not make any trade or account changes."
