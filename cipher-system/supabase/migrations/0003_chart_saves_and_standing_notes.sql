create table if not exists public.chart_saves (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker = upper(ticker)),
  price numeric not null check (price >= 0),
  view text not null check (length(trim(view)) between 1 and 80),
  date_added text not null check (length(trim(date_added)) between 1 and 40),
  top_levels jsonb not null default '[]'::jsonb,
  image_url text not null default '',
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.standing_notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  note_date date not null,
  note text not null check (length(trim(note)) between 1 and 2000),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, note_date)
);

create index if not exists chart_saves_user_id_idx on public.chart_saves (user_id, created_at desc);
create index if not exists standing_notes_user_id_idx on public.standing_notes (user_id, note_date desc);

alter table public.chart_saves enable row level security;
alter table public.standing_notes enable row level security;

create policy chart_saves_select on public.chart_saves for select using (user_id = auth.uid());
create policy chart_saves_insert on public.chart_saves for insert with check (user_id = auth.uid());
create policy chart_saves_delete on public.chart_saves for delete using (user_id = auth.uid());

create policy standing_notes_select on public.standing_notes for select using (user_id = auth.uid());
create policy standing_notes_insert on public.standing_notes for insert with check (user_id = auth.uid());
create policy standing_notes_update on public.standing_notes for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy standing_notes_delete on public.standing_notes for delete using (user_id = auth.uid());
