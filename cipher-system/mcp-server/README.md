# Cipher Research MCP

Browser-first options research workflow for an authorized Cipher browser session. The server keeps evidence, screenshots, option-chain snapshots, and neutral research reports locally. It never submits orders, changes broker settings, or returns secret values.

## Start

Open the project in Codex, then start the configured MCP server. The launcher is:

    C:\Aarav\cipher-system\mcp-server\run.py

It loads the local market-data configuration from the existing app environment without exposing the credentials.

## Daily sequence

1. Start a run with start_daily_workflow.
2. Use Browser or Computer to collect visible Cipher evidence: Strike Matrix, Night Vision/X-Ray, flow, and Setup Scanner.
3. Save each visible result with record_observation and attach_screenshot.
4. Pull approved Alpaca data with pull_option_chain. Select OPRA only when the Alpaca entitlement supports it; otherwise the stored feed label records indicative data.
5. Run score_option_liquidity, detect_unusual_flow, rank_research_candidates, and compare_scan_to_previous.
6. Export a ticker brief or daily report.

## Research tools

- pull_option_chain: bid, ask, mid, last, volume, open interest when supplied, IV, Greeks, and timestamps.
- score_option_liquidity: spread, volume, open interest, and quote-quality screening.
- detect_unusual_flow: volume/OI and estimated premium screening. Quote side is explicitly inferred, not verified.
- rank_research_candidates: prioritizes items for further research using data and saved Cipher evidence. It is not advice.
- compare_scan_to_previous: observes changes between locally saved snapshots.
- record_outcome: preserves later observations for calibration, not performance claims.

SQLite data is under data. Reports are under exports. The database and reports remain local to this project.
