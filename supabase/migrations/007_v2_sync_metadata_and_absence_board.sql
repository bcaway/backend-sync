-- ==============================================================================
-- BCAway Supabase Migration: v2.0 Sync Metadata & Absence Board Engine
-- Migration: 007_v2_sync_metadata_and_absence_board.sql
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Sync Metadata Table (Doc Date Verification & Freshness)
-- ------------------------------------------------------------------------------
create table if not exists public.sync_metadata (
    id int primary key default 1 check (id = 1),
    doc_date date not null,
    today_date date not null,
    is_updated_for_today boolean not null default false,
    last_synced_at timestamptz not null default now(),
    absence_count int not null default 0,
    raw_header_text text,
    sync_status text not null default 'ok'
);

create index if not exists idx_sync_metadata_is_updated
    on public.sync_metadata (is_updated_for_today);

alter table public.sync_metadata enable row level security;

-- Read policy: Only authorized BCAway users (@bergen.org, @bcaway.app) or service_role
drop policy if exists "Allow read sync_metadata to authorized users" on public.sync_metadata;
create policy "Allow read sync_metadata to authorized users"
on public.sync_metadata
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (auth.jwt() ->> 'role') = 'service_role'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
);

-- Write policy: Only service_role can modify sync_metadata
drop policy if exists "Allow service_role manage sync_metadata" on public.sync_metadata;
create policy "Allow service_role manage sync_metadata"
on public.sync_metadata
for all
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (auth.jwt() ->> 'role') = 'service_role'
)
with check (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (auth.jwt() ->> 'role') = 'service_role'
);


-- ------------------------------------------------------------------------------
-- 2. Absence Board Posts (Crowdsourced Future Teacher Absences)
-- ------------------------------------------------------------------------------
create table if not exists public.absence_board_posts (
    id uuid primary key default gen_random_uuid(),
    teacher_id uuid references public.teachers(id) on delete set null,
    teacher_name text not null,
    target_date date not null,
    periods_impacted text not null default 'all',
    source_type text not null check (source_type in ('in_class', 'schoology', 'email', 'other')),
    notes text check (char_length(notes) <= 280),
    submitted_by uuid not null references auth.users(id) on delete cascade,
    confirmation_count int not null default 0,
    report_count int not null default 0,
    status text not null default 'active' check (status in ('active', 'official_confirmed', 'expired', 'hidden')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_absence_board_target_date on public.absence_board_posts (target_date);
create index if not exists idx_absence_board_teacher on public.absence_board_posts (teacher_name);
create index if not exists idx_absence_board_status on public.absence_board_posts (status);
create index if not exists idx_absence_board_submitted_by on public.absence_board_posts (submitted_by);

alter table public.absence_board_posts enable row level security;

-- Read policy: Authenticated BCAway users can view active / official_confirmed posts,
-- or users can view their own posts even if pending/expired/hidden.
drop policy if exists "Allow read active absence board posts" on public.absence_board_posts;
create policy "Allow read active absence board posts"
on public.absence_board_posts
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (
    (lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
     or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app')
    and (status in ('active', 'official_confirmed', 'expired') or submitted_by = auth.uid())
  )
);

-- Insert policy: Authenticated @bergen.org or @bcaway.app users can create posts
drop policy if exists "Allow authorized users to insert board posts" on public.absence_board_posts;
create policy "Allow authorized users to insert board posts"
on public.absence_board_posts
for insert
to authenticated
with check (
  auth.uid() = submitted_by
  and (
    lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
    or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
  )
);

-- Update policy: Author or admin can update their post
drop policy if exists "Allow author or admin update board posts" on public.absence_board_posts;
create policy "Allow author or admin update board posts"
on public.absence_board_posts
for update
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or auth.uid() = submitted_by
)
with check (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or auth.uid() = submitted_by
);

-- Delete policy: Author or admin can delete their post
drop policy if exists "Allow author or admin delete board posts" on public.absence_board_posts;
create policy "Allow author or admin delete board posts"
on public.absence_board_posts
for delete
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or auth.uid() = submitted_by
);


-- ------------------------------------------------------------------------------
-- 3. Absence Board Confirmations Table (Peer Upvoting)
-- ------------------------------------------------------------------------------
create table if not exists public.absence_board_confirmations (
    id uuid primary key default gen_random_uuid(),
    post_id uuid not null references public.absence_board_posts(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    created_at timestamptz not null default now(),
    unique (post_id, user_id)
);

create index if not exists idx_board_confirm_post on public.absence_board_confirmations (post_id);
create index if not exists idx_board_confirm_user on public.absence_board_confirmations (user_id);

alter table public.absence_board_confirmations enable row level security;

-- Read policy: Users can see their own confirmations
drop policy if exists "Users can read own confirmations" on public.absence_board_confirmations;
create policy "Users can read own confirmations"
on public.absence_board_confirmations
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or auth.uid() = user_id
);

-- Insert policy: Authenticated users can confirm once per post
drop policy if exists "Users can confirm posts" on public.absence_board_confirmations;
create policy "Users can confirm posts"
on public.absence_board_confirmations
for insert
to authenticated
with check (
  auth.uid() = user_id
  and (
    lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
    or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
  )
);

-- Delete policy: Users can remove their own confirmation
drop policy if exists "Users can remove confirmations" on public.absence_board_confirmations;
create policy "Users can remove confirmations"
on public.absence_board_confirmations
for delete
to authenticated
using (auth.uid() = user_id);


-- ------------------------------------------------------------------------------
-- 4. Absence Board Reports Table (Abuse / Flagging)
-- ------------------------------------------------------------------------------
create table if not exists public.absence_board_reports (
    id uuid primary key default gen_random_uuid(),
    post_id uuid not null references public.absence_board_posts(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    reason text not null check (char_length(reason) <= 280),
    created_at timestamptz not null default now(),
    unique (post_id, user_id)
);

create index if not exists idx_board_reports_post on public.absence_board_reports (post_id);

alter table public.absence_board_reports enable row level security;

-- Read policy: Only admin or service_role can read reports
drop policy if exists "Admin read reports" on public.absence_board_reports;
create policy "Admin read reports"
on public.absence_board_reports
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
);

-- Insert policy: Authenticated users can file a report
drop policy if exists "Users can report posts" on public.absence_board_reports;
create policy "Users can report posts"
on public.absence_board_reports
for insert
to authenticated
with check (
  auth.uid() = user_id
  and (
    lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
    or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
  )
);


-- ------------------------------------------------------------------------------
-- 5. Confirmation Counter Trigger
-- ------------------------------------------------------------------------------
create or replace function public.handle_board_confirmation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if (tg_op = 'INSERT') then
    update public.absence_board_posts
    set confirmation_count = confirmation_count + 1, updated_at = now()
    where id = new.post_id;
  elsif (tg_op = 'DELETE') then
    update public.absence_board_posts
    set confirmation_count = greatest(0, confirmation_count - 1), updated_at = now()
    where id = old.post_id;
  end if;
  return null;
end;
$$;

drop trigger if exists on_board_confirmation_change on public.absence_board_confirmations;
create trigger on_board_confirmation_change
  after insert or delete on public.absence_board_confirmations
  for each row execute function public.handle_board_confirmation();


-- ------------------------------------------------------------------------------
-- 6. Report Counter & Auto-Hide Trigger
-- ------------------------------------------------------------------------------
create or replace function public.handle_board_report()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.absence_board_posts
  set
    report_count = report_count + 1,
    status = case when report_count + 1 >= 3 then 'hidden' else status end,
    updated_at = now()
  where id = new.post_id;
  return null;
end;
$$;

drop trigger if exists on_board_report_change on public.absence_board_reports;
create trigger on_board_report_change
  after insert on public.absence_board_reports
  for each row execute function public.handle_board_report();


-- ------------------------------------------------------------------------------
-- 7. Realtime Publications
-- ------------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'sync_metadata'
  ) then
    alter publication supabase_realtime add table public.sync_metadata;
  end if;

  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'absence_board_posts'
  ) then
    alter publication supabase_realtime add table public.absence_board_posts;
  end if;

  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'absence_board_confirmations'
  ) then
    alter publication supabase_realtime add table public.absence_board_confirmations;
  end if;
end $$;
