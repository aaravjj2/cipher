# Cipher Google Agentic Fleet

This is the Google ADK adaptation of Cipher for the **Fortified Enterprise
Fleet** track. It composes specialist research agents over Cipher's existing
read-only core API; it does not duplicate the market-data engine or introduce a
broker connection.

## Fleet

- `market_structure_agent` — quote, GEX/VEX structure, and session levels.
- `options_flow_agent` — recent option prints and explicitly inferred side.
- `historical_evidence_agent` — OHLCV context and captured GEX history.
- `strategy_validation_agent` — strategy catalog, prospective standing, and
  evidence gates.
- `risk_adversarial_agent` — governance state, data limitations, and the human
  review boundary.
- `cipher_supervisor` — delegates, reconciles conflicts, and produces a
  research memo with uncertainty and provenance.

All tools make HTTP `GET` requests against an explicit path allowlist. Tool
results are projected and bounded before reaching Gemini. The audit plugin logs
event metadata, tool names, payload sizes, and content hashes; it intentionally
does not store prompts, secrets, raw tool payloads, or hidden model reasoning.

## Local run

Cipher core must already be listening on `127.0.0.1:8282`.

```bash
python3 -m venv .venv-google-agentic
. .venv-google-agentic/bin/activate
pip install -r cipher-system/google_agentic/requirements.txt
export GOOGLE_API_KEY="..."
adk run cipher-system/google_agentic/cipher_fleet
```

The model defaults to `gemini-3.5-flash` for the current ADK 2.x runtime. It can
be overridden without changing code:

```bash
export CIPHER_AGENT_MODEL="gemini-3.5-flash"
```

Operational state is written under `cipher-system/data/agent_fleet/`, which is
local runtime data and must not be committed.

## Security boundary

- Research only; no capital deployment or transactional market action.
- Default core origin is loopback. Any non-loopback origin must be explicitly
  named in `CIPHER_AGENT_ALLOWED_CORE_HOSTS`.
- Every callable tool and every reachable core path is allowlisted.
- Unknown data remains unknown; projections preserve provider caveats.
- Agent output must end at human review.
