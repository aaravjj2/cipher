# Hosted auth recovery — 2026-08-21

## Diagnosis

- The hosted page now renders the Supabase AuthPanel correctly.
- Browser smoke test with system Chromium verified `Email`, `Password`, and
  `Forgot password?`; unauthenticated `/auth/session` returns 401 as intended.
- Supabase email auth is enabled.
- The project reports `mailer_autoconfirm=false`, `disable_signup=false`, and no
  configured `site_url` in the public auth settings response.
- `POST /auth/v1/recover` accepts requests with HTTP 200, but the VM has no
  Supabase service-role key or management API token. Therefore the VM cannot
  inspect mailer delivery logs or configure SMTP.

## Required Supabase dashboard settings

In project `ipcsgrijatnnsbpguojl`:

1. Authentication → URL Configuration:
   - Site URL: `https://cipher-main.tail39504f.ts.net:8443`
   - Redirect URL: `https://cipher-main.tail39504f.ts.net:8443/**`
2. Authentication → Email → SMTP:
   - Configure a verified SMTP provider for reliable delivery. The hosted default
     mailer is rate-limited and is not suitable for dependable private use.
3. Confirm the reset email template links to `{{ .RedirectTo }}` or the project
   site URL.

No Supabase password or service key belongs in the repository or browser.

## Deliberate security boundary

The VM is **not** switching to password-only authentication automatically. In
hosted mode local `/api/login` is disabled, and enabling a second password gate
would create two unrelated authentication authorities and could bypass the
Supabase account/session model. If a local-only password gate is desired, it
must be an explicit migration back to `CIPHER_HOSTED=0`, followed by a separately
communicated temporary password and a service restart.

The frontend reset message now explains that delivery may require checking spam
or operator SMTP configuration without revealing whether an email is registered.
