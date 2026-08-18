# Cipher hosted Supabase setup

This directory contains the schema for the hosted multi-user deployment.

## Manual setup

1. Create a Supabase project manually.
2. Enable the intended sign-in methods under **Authentication → Providers**.
3. Add the Vercel production and preview callback URLs under **Authentication → URL Configuration**.
4. Apply `migrations/0001_user_state.sql`, then `migrations/0002_provider_session_metadata.sql`, then `migrations/0003_chart_saves_and_standing_notes.sql` in the Supabase SQL editor or through the operator's approved migration workflow.
5. Create two disposable test users and run the isolation checks below.
6. Remove the disposable users and keep the project reference only in operator configuration.

The browser receives only `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY`. Never put `SUPABASE_SERVICE_ROLE_KEY`, database
passwords, or Alpaca credentials in browser variables, this repository, or a
Vercel public environment.

## RLS verification

Run these checks while authenticated as two different test users:

- User A creates a watchlist called `same-name`.
- User B creates a watchlist called `same-name`.
- Each user can select only their own row.
- User A cannot update or delete User B's row by changing its UUID.
- An insert containing User B's `user_id` is rejected for User A.
- The same checks pass for journals, layouts, chart saves, standing notes, holdings, alerts, and paper-user records.

The policies use `user_id = auth.uid()` for select/update/delete and
`with check (user_id = auth.uid())` for insert/update. The Python API must pass
the authenticated user's JWT to Supabase so these policies remain active.

## Rollback

Hosted Auth can be disabled only after stopping public Vercel traffic. Restore
the local password-gated Node configuration and retain the current GCP VM
service units. Do not drop tables or delete migrations as a rollback mechanism;
RLS migrations are additive and user-state data must remain recoverable.
