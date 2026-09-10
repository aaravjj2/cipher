# Cipher MCP and freshness verification

## Deployed

- Public MCP: `https://cipher-main.tail39504f.ts.net:10000/mcp`.
- Vercel production: `https://web-omega-silk-71.vercel.app`;
  deployment `dpl_3vTrUuofSmfFP7mRt1RoCwdAGmBv`, READY.
- VM static build published atomically; previous releases provide rollback.
- Core, web, MCP bridge, and local-paper executor active.
- Weekly universe-validation timer enabled; next scheduled Sep 13 at 10:02 UTC.

## Evidence

- All 32 MCP tools called successfully through the public HTTPS endpoint.
- Public OAuth DCR, operator consent, S256 PKCE, refresh, discovery and authenticated
  tool calls passed. Missing authorization rejected with 401.
- MCP regression suite: 81 passed (including real stdio, HTTP, OAuth and live core).
- Main Python regression suite: 1,261 passed, 2 skipped. Subsequent Flash/freshness
  changes checked with 9 freshness tests; quote/fallback/publish checks: 18 passed.
- Node suite after Flash UI change: 154 passed.
- ESLint, TypeScript, production build, JavaScript syntax, systemd unit verification
  and git whitespace checks passed.
- Public quote returns SIP and the original Sep 4 source timestamp, appropriate
  for the weekend/Labor Day closure, not a manufactured current quote.
- Universe revalidated against Alpaca: 559 → 557; AVB/EQR no longer matched active
  `has_options` eligibility. This validates membership, not current market caps.
- Research ranking Sep 7 10:30 UTC and earnings radar Sep 7 12:16 UTC are current.

## Explicit remaining limitations

- Flash browser evidence is **stale**, captured Jul 23; loop stopped and its required
  WebBridge at `127.0.0.1:10086` is unreachable. API/global freshness now reports this
  exception, and UI shows the full date and “Stale capture”. Restoring fresh source
  evidence requires the external connected, logged-in browser. It was not fabricated
  or relabelled current. No external proprietary signal was locally reconstructed.
- Production host login rejected the old password in the local legacy password file.
  Password unchanged. Auth-route tests pass, but the user's current-password login
  was not verified.
- Supabase remains external/unavailable; MCP OAuth does not depend on it.
- ChatGPT account attachment is not automated: user must add/refresh the connector
  and approve OAuth. Public protocol checks are not an in-account ChatGPT test.
- 33 reviewed Cipher read-only tools are exposed, not arbitrary shell execution,
  session-owned tools, other connected apps, legacy writing tools, or broker orders.
- The `--oauth` check creates a deployment-verification client/grant in the OAuth
  store; no credentials were printed. Older grants without resource binding need
  reconnection. See CHATGPT_SETUP.md.
