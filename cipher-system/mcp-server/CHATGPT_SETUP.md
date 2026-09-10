# Cipher in ChatGPT

The deployed connector exposes 32 reviewed read-only Cipher tools through the existing
OAuth-protected bridge. This covers quotes/bars, GEX, Night Vision, options chains/flow,
earnings, saved scanner/research evidence, governance, freshness, and Cipher's internal
paper portfolios and Autopilot ledger. Supabase is not required.

It does **not** export this Codex session's shell, browser, external connected apps,
credentials, broker order tools, or legacy research-writing tools. Those are separate
permissions/integrations, not capabilities an MCP URL automatically inherits.

## One-shot VM setup and verification

```bash
bash cipher-system/scripts/setup_chatgpt_mcp.sh --all
```

This preserves the existing token and OAuth grants, installs the versioned service,
enables the existing port-10000 Funnel, and checks the real HTTPS endpoint. Prerequisites:
the Cipher VM, working core, existing runtime token and `/etc/cipher/cipher.env`.
No OpenAI API key, Alpaca trading access, or Supabase connection is needed.
Add `--oauth` to exercise public registration, consent, PKCE and refresh as well.
That creates a deployment-verification OAuth grant; it does not attach a ChatGPT account.

## Attach once in your ChatGPT account

1. Enable Developer mode in Settings → Security and login.
2. In ChatGPT Plugins, create a developer-mode app named `Cipher`.
3. MCP URL: `https://cipher-main.tail39504f.ts.net:10000/mcp`.
4. Choose OAuth and dynamic client registration (DCR), not CIMD or no authentication.
5. On Cipher's consent page, enter the operator token from the local file
   `/home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt`.
   Keep it private; never paste it into a chat message.
6. Enable Cipher in the conversation's Developer mode menu. For an existing connector,
   refresh its tools. Older grants without a resource audience require reconnection.

If DCR is unavailable, use `register_oauth_client.py` with the **exact callback URL**
shown by ChatGPT, then enter the returned public client ID and leave the secret blank.

Example prompt: “Use Cipher. First check SPY data freshness, then inspect its quote,
GEX levels and my internal paper portfolios. Report source times and missing data.”

Only the user can finish the account consent step; the VM check does not claim to
test inside their ChatGPT conversation.

Live refresh: quote, bars, options chain/flow, GEX, and Night Vision calls use the
authenticated Alpaca-backed core when invoked. Flash and browser-capture data are
not exposed by MCP.

## Freshness and safety

Prices retain provider timestamps. Friday data is last-session data over the weekend
and Labor Day, not live Sunday data. Research-job freshness uses elapsed age rather
than an exchange-session comparison. Missing/future observations remain unavailable.
The retired Tradier tape is labelled snapshot-only; browser captures can still be old
when the source device is offline. No freshness timestamp is rewritten to hide a gap.

The bridge and host UI re-establish expired provider sessions after core restarts.
OAuth uses PKCE, resource-bound access tokens and operator consent. Tool routing is
allowlisted; no arbitrary URL, filesystem path, shell execution, or order action.

Official OpenAI documentation:
[Developer mode](https://developers.openai.com/api/docs/guides/developer-mode),
[MCP authentication](https://developers.openai.com/plugins/build/auth).
