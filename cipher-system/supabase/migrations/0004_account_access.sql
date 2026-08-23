-- Operator-controlled account modes. Unlike user_profiles.preferences, users can
-- read this row but cannot grant or edit their own role.
create table if not exists public.account_access (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('member', 'developer')),
  developer_settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.account_access enable row level security;

drop policy if exists "account_access_select_own" on public.account_access;
create policy "account_access_select_own"
  on public.account_access for select
  to authenticated
  using (user_id = auth.uid());

-- Intentionally no authenticated INSERT/UPDATE/DELETE policy. Assign developer
-- mode only from the Supabase SQL editor/service-role administration surface:
--
-- insert into public.account_access (user_id, role, developer_settings)
-- select id, 'developer', '{"display_name":"Aarav","default_panel":"Operator Status"}'::jsonb
-- from auth.users where lower(email) = lower('YOUR_EMAIL')
-- on conflict (user_id) do update set role = excluded.role,
--   developer_settings = excluded.developer_settings, updated_at = now();
