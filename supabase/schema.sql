-- Supabase Dashboard > SQL Editor에서 한 번 실행합니다.
create extension if not exists pgcrypto;

create table if not exists public.roster_snapshots (
  roster_date date not null,
  team text not null,
  first_players jsonb not null default '{}'::jsonb,
  futures_players jsonb not null default '{}'::jsonb,
  futures_roster_date date,
  synced_at timestamptz not null default now(),
  primary key (roster_date, team),
  constraint roster_team_check check (team in ('두산','LG','KIA','삼성','롯데','한화','SSG','키움','NC','KT')),
  constraint first_players_object_check check (jsonb_typeof(first_players) = 'object'),
  constraint futures_players_object_check check (jsonb_typeof(futures_players) = 'object')
);

create table if not exists public.daily_results (
  id uuid primary key default gen_random_uuid(),
  draw_date date not null,
  team text not null,
  mode text not null,
  include_permanent boolean not null default false,
  numbers jsonb not null,
  roster_date date not null,
  created_at timestamptz not null default now(),
  unique (draw_date, team, mode, include_permanent),
  constraint daily_team_check check (team in ('두산','LG','KIA','삼성','롯데','한화','SSG','키움','NC','KT')),
  constraint daily_mode_check check (mode in ('0','1','2','3','4','5','6','all')),
  constraint daily_numbers_array_check check (jsonb_typeof(numbers) = 'array' and jsonb_array_length(numbers) = 6)
);

create table if not exists public.user_draws (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  draw_date date not null,
  team text not null,
  mode text not null,
  include_permanent boolean not null default false,
  numbers jsonb not null,
  roster_date date not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, draw_date, team, mode, include_permanent),
  constraint user_draw_team_check check (team in ('두산','LG','KIA','삼성','롯데','한화','SSG','키움','NC','KT')),
  constraint user_draw_mode_check check (mode in ('0','1','2','3','4','5','6','all')),
  constraint user_draw_numbers_array_check check (jsonb_typeof(numbers) = 'array' and jsonb_array_length(numbers) = 6)
);

create index if not exists daily_results_lookup_idx
  on public.daily_results (team, draw_date);
create index if not exists user_draws_lookup_idx
  on public.user_draws (user_id, team, draw_date);

alter table public.roster_snapshots enable row level security;
alter table public.daily_results enable row level security;
alter table public.user_draws enable row level security;

drop policy if exists "public can read roster snapshots" on public.roster_snapshots;
create policy "public can read roster snapshots"
  on public.roster_snapshots for select
  to anon, authenticated
  using (true);

drop policy if exists "public can read daily results" on public.daily_results;
create policy "public can read daily results"
  on public.daily_results for select
  to anon, authenticated
  using (true);

drop policy if exists "owners can read user draws" on public.user_draws;
create policy "owners can read user draws"
  on public.user_draws for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists "owners can insert todays user draws" on public.user_draws;
create policy "owners can insert todays user draws"
  on public.user_draws for insert
  to authenticated
  with check (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  );

drop policy if exists "owners can update todays user draws" on public.user_draws;
create policy "owners can update todays user draws"
  on public.user_draws for update
  to authenticated
  using (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  )
  with check (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  );

grant usage on schema public to anon, authenticated;
grant select on public.roster_snapshots, public.daily_results to anon, authenticated;
grant select, insert, update on public.user_draws to authenticated;
