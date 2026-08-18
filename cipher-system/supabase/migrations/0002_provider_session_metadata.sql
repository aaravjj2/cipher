create table if not exists public.provider_connection_metadata (
  user_id uuid primary key references auth.users(id) on delete cascade,
  provider text not null check (provider in ('alpaca')),
  options_feed text not null check (options_feed in ('opra', 'indicative')),
  stock_feed text not null check (stock_feed in ('sip', 'iex')),
  status text not null check (status in ('connected', 'expired', 'disconnected', 'unavailable')),
  connected_at timestamptz,
  last_used_at timestamptz,
  expires_at timestamptz,
  updated_at timestamptz not null default timezone('utc', now())
);

alter table public.provider_connection_metadata enable row level security;

create policy provider_connection_metadata_select
  on public.provider_connection_metadata
  for select using (user_id = auth.uid());

create policy provider_connection_metadata_insert
  on public.provider_connection_metadata
  for insert with check (user_id = auth.uid());

create policy provider_connection_metadata_update
  on public.provider_connection_metadata
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy provider_connection_metadata_delete
  on public.provider_connection_metadata
  for delete using (user_id = auth.uid());
