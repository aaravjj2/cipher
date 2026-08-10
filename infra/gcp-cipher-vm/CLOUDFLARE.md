# Cloudflare Tunnel and Access deployment

Cipher uses Cloudflare Tunnel as an outbound-only connector to `http://127.0.0.1:8283`. Cloudflare Access must authenticate the user before any request reaches the tunnel. Do not use a Quick Tunnel: it has a random public hostname and no Access policy.

## Required account setup

1. Add a domain to Cloudflare and create a Cloudflare Zero Trust organization.
2. In **Networking → Tunnels**, create a remotely managed tunnel named `cipher-main`.
3. Add one published application route:
   - Hostname: a dedicated name such as `cipher.example.com`.
   - Service: `http://127.0.0.1:8283`.
   - No TLS verification exception is needed because the origin connection is loopback HTTP.
4. In **Access controls → Applications**, create a self-hosted application for the exact hostname.
5. Add an Allow policy restricted to the owner's Cloudflare account membership. For an existing account that does not use Cloudflare as its identity provider, an exact-email one-time-PIN policy is the fallback.
6. Enable **Protect with Access** on the tunnel route. Configure `access.required=true`, the Zero Trust team name, and the application's Audience (AUD) tag. This makes `cloudflared` validate the signed Access JWT before proxying to Cipher.
7. Keep every Cipher path, including `/api/health`, behind Access during initial deployment. Do not create an Everyone/Bypass policy.

The existing Cipher password gate remains enabled as defense in depth for the first launch. Removing that second login is a separate migration: first disable all direct Tailscale routes and prove that Cloudflare Access JWT validation protects every path. The scanner-ingest endpoint also needs a deliberate Cloudflare service-auth design before its Tailscale route can be retired; never put a Cloudflare service-token secret in browser JavaScript.

## Deliver the tunnel token

Copy the remotely managed tunnel token into the ignored local deployment environment as `CLOUDFLARE_TUNNEL_TOKEN`, or create the GCP secret directly without placing the value in shell history. Deployment maps it to `cipher-cloudflare-tunnel-token`. On the VM, `sync-secrets.py` materializes `/etc/cipher/cloudflare-tunnel.token` as mode `0600`.

The systemd service reads the token with `LoadCredential`; the token does not appear in the process command line, shared Cipher environment file, or journal.

```bash
sudo systemctl restart cipher-secrets.service
sudo systemctl enable --now cipher-cloudflared.service
sudo systemctl status cipher-cloudflared.service
```

## Required verification before removing Tailscale

```bash
python3 infra/gcp-cipher-vm/bin/verify-cloudflare-access.py --hostname cipher.example.com
```

The unauthenticated verifier checks `/`, `/api/health`, and `/api/quote` and fails if any returns Cipher content or market data. Then test interactively in a fresh browser profile:

1. The hostname redirects to Cloudflare Access.
2. A non-allowed identity is denied.
3. The allowed owner identity completes Cloudflare authentication.
4. Cipher's password gate accepts the owner password.
5. The Options Backtest panel shows real coverage and caveats.
6. Network/SSE requests work and the browser console has no errors.
7. Logout invalidates the Cipher session; a fresh browser still encounters Access.

Only after those checks pass should the Tailscale `:8443` route be removed. Keep the old scanner-ingest route until its replacement has its own tested Access policy.
