create extension if not exists pgcrypto;

create table if not exists public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.watchlists (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, name),
  unique (id, user_id)
);

create table if not exists public.watchlist_members (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  watchlist_id uuid not null,
  ticker text not null check (ticker = upper(ticker)),
  position integer not null default 0 check (position >= 0),
  added_at timestamptz not null default timezone('utc', now()),
  unique (user_id, watchlist_id, ticker),
  foreign key (watchlist_id, user_id)
    references public.watchlists(id, user_id) on delete cascade
);

create table if not exists public.saved_screens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  watchlist_id uuid,
  name text not null check (length(trim(name)) between 1 and 120),
  criteria jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, name),
  foreign key (watchlist_id, user_id)
    references public.watchlists(id, user_id) on delete set null (watchlist_id)
);

create table if not exists public.journal_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text check (ticker is null or ticker = upper(ticker)),
  title text not null check (length(trim(title)) between 1 and 240),
  body text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.chart_templates (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, name)
);

create table if not exists public.workspace_layouts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  layout jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, name)
);

create table if not exists public.holdings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker = upper(ticker)),
  shares numeric not null check (shares > 0),
  entry_price numeric not null check (entry_price > 0),
  entry_date date not null,
  status text not null default 'OPEN' check (status in ('OPEN', 'CLOSED')),
  exit_price numeric check (exit_price is null or exit_price > 0),
  exit_date date,
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  check ((status = 'OPEN' and exit_price is null and exit_date is null)
      or (status = 'CLOSED' and exit_price is not null and exit_date is not null))
);

create table if not exists public.alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker = upper(ticker)),
  kind text not null check (length(trim(kind)) between 1 and 100),
  threshold numeric,
  configuration jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.portfolio_risk_positions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker = upper(ticker)),
  contract text,
  quantity numeric not null check (quantity <> 0),
  entry_price numeric not null check (entry_price > 0),
  direction text not null check (direction in ('LONG', 'SHORT')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.portfolio_risk_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  cash numeric not null default 0 check (cash >= 0),
  settings jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.paper_user_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  record_type text not null check (length(trim(record_type)) between 1 and 100),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists watchlists_user_id_idx on public.watchlists (user_id);
create index if not exists watchlist_members_user_id_idx on public.watchlist_members (user_id);
create index if not exists saved_screens_user_id_idx on public.saved_screens (user_id);
create index if not exists journal_entries_user_id_idx on public.journal_entries (user_id);
create index if not exists chart_templates_user_id_idx on public.chart_templates (user_id);
create index if not exists workspace_layouts_user_id_idx on public.workspace_layouts (user_id);
create index if not exists holdings_user_id_idx on public.holdings (user_id);
create index if not exists alerts_user_id_idx on public.alerts (user_id);
create index if not exists portfolio_risk_positions_user_id_idx on public.portfolio_risk_positions (user_id);
create index if not exists paper_user_records_user_id_idx on public.paper_user_records (user_id);

alter table public.user_profiles enable row level security;
alter table public.watchlists enable row level security;
alter table public.watchlist_members enable row level security;
alter table public.saved_screens enable row level security;
alter table public.journal_entries enable row level security;
alter table public.chart_templates enable row level security;
alter table public.workspace_layouts enable row level security;
alter table public.holdings enable row level security;
alter table public.alerts enable row level security;
alter table public.portfolio_risk_positions enable row level security;
alter table public.portfolio_risk_settings enable row level security;
alter table public.paper_user_records enable row level security;

create policy user_profiles_select on public.user_profiles for select using (user_id = auth.uid());
create policy user_profiles_insert on public.user_profiles for insert with check (user_id = auth.uid());
create policy user_profiles_update on public.user_profiles for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy user_profiles_delete on public.user_profiles for delete using (user_id = auth.uid());

create policy watchlists_select on public.watchlists for select using (user_id = auth.uid());
create policy watchlists_insert on public.watchlists for insert with check (user_id = auth.uid());
create policy watchlists_update on public.watchlists for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy watchlists_delete on public.watchlists for delete using (user_id = auth.uid());

create policy watchlist_members_select on public.watchlist_members for select using (user_id = auth.uid());
create policy watchlist_members_insert on public.watchlist_members for insert with check (user_id = auth.uid());
create policy watchlist_members_update on public.watchlist_members for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy watchlist_members_delete on public.watchlist_members for delete using (user_id = auth.uid());

create policy saved_screens_select on public.saved_screens for select using (user_id = auth.uid());
create policy saved_screens_insert on public.saved_screens for insert with check (user_id = auth.uid());
create policy saved_screens_update on public.saved_screens for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy saved_screens_delete on public.saved_screens for delete using (user_id = auth.uid());

create policy journal_entries_select on public.journal_entries for select using (user_id = auth.uid());
create policy journal_entries_insert on public.journal_entries for insert with check (user_id = auth.uid());
create policy journal_entries_update on public.journal_entries for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy journal_entries_delete on public.journal_entries for delete using (user_id = auth.uid());

create policy chart_templates_select on public.chart_templates for select using (user_id = auth.uid());
create policy chart_templates_insert on public.chart_templates for insert with check (user_id = auth.uid());
create policy chart_templates_update on public.chart_templates for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy chart_templates_delete on public.chart_templates for delete using (user_id = auth.uid());

create policy workspace_layouts_select on public.workspace_layouts for select using (user_id = auth.uid());
create policy workspace_layouts_insert on public.workspace_layouts for insert with check (user_id = auth.uid());
create policy workspace_layouts_update on public.workspace_layouts for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy workspace_layouts_delete on public.workspace_layouts for delete using (user_id = auth.uid());

create policy holdings_select on public.holdings for select using (user_id = auth.uid());
create policy holdings_insert on public.holdings for insert with check (user_id = auth.uid());
create policy holdings_update on public.holdings for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy holdings_delete on public.holdings for delete using (user_id = auth.uid());

create policy alerts_select on public.alerts for select using (user_id = auth.uid());
create policy alerts_insert on public.alerts for insert with check (user_id = auth.uid());
create policy alerts_update on public.alerts for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy alerts_delete on public.alerts for delete using (user_id = auth.uid());

create policy portfolio_risk_positions_select on public.portfolio_risk_positions for select using (user_id = auth.uid());
create policy portfolio_risk_positions_insert on public.portfolio_risk_positions for insert with check (user_id = auth.uid());
create policy portfolio_risk_positions_update on public.portfolio_risk_positions for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy portfolio_risk_positions_delete on public.portfolio_risk_positions for delete using (user_id = auth.uid());

create policy portfolio_risk_settings_select on public.portfolio_risk_settings for select using (user_id = auth.uid());
create policy portfolio_risk_settings_insert on public.portfolio_risk_settings for insert with check (user_id = auth.uid());
create policy portfolio_risk_settings_update on public.portfolio_risk_settings for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy portfolio_risk_settings_delete on public.portfolio_risk_settings for delete using (user_id = auth.uid());

create policy paper_user_records_select on public.paper_user_records for select using (user_id = auth.uid());
create policy paper_user_records_insert on public.paper_user_records for insert with check (user_id = auth.uid());
create policy paper_user_records_update on public.paper_user_records for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy paper_user_records_delete on public.paper_user_records for delete using (user_id = auth.uid());
