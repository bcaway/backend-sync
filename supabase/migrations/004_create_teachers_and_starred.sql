-- ==============================================================================
-- BCAway Supabase Migration: Teachers, Aliases, and User Starred Teachers
-- ==============================================================================

-- 1. Create the teachers table
create table if not exists public.teachers (
    id uuid primary key default gen_random_uuid(),
    name text not null unique,
    aliases text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_teachers_name on public.teachers (name);
create index if not exists idx_teachers_aliases on public.teachers using gin (aliases);

alter table public.teachers enable row level security;

-- Read policy: Anyone authenticated (or anon) can view teachers
drop policy if exists "Allow select on teachers" on public.teachers;
create policy "Allow select on teachers"
on public.teachers
for select
to anon, authenticated
using (true);

-- Manage policy: Allow insert/update/delete on teachers (for admin app & management)
drop policy if exists "Allow manage teachers" on public.teachers;
create policy "Allow manage teachers"
on public.teachers
for all
to anon, authenticated
using (true)
with check (true);

-- 2. Create the user_starred_teachers table
create table if not exists public.user_starred_teachers (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    teacher_id uuid not null references public.teachers(id) on delete cascade,
    created_at timestamptz not null default now(),
    unique (user_id, teacher_id)
);

create index if not exists idx_user_starred_teachers_user_id
    on public.user_starred_teachers (user_id);
create index if not exists idx_user_starred_teachers_teacher_id
    on public.user_starred_teachers (teacher_id);

alter table public.user_starred_teachers enable row level security;

-- RLS for user_starred_teachers
drop policy if exists "Users can view their own starred teachers" on public.user_starred_teachers;
create policy "Users can view their own starred teachers"
on public.user_starred_teachers
for select
to authenticated
using (auth.uid() = user_id);

drop policy if exists "Users can star teachers" on public.user_starred_teachers;
create policy "Users can star teachers"
on public.user_starred_teachers
for insert
to authenticated
with check (auth.uid() = user_id);

drop policy if exists "Users can unstar teachers" on public.user_starred_teachers;
create policy "Users can unstar teachers"
on public.user_starred_teachers
for delete
to authenticated
using (auth.uid() = user_id);

-- 3. Enable Realtime publication for relevant tables
do $$
begin
  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'teacher_absences'
  ) then
    alter publication supabase_realtime add table public.teacher_absences;
  end if;
  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'teachers'
  ) then
    alter publication supabase_realtime add table public.teachers;
  end if;
  if not exists (
    select 1 from pg_publication_tables 
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'user_starred_teachers'
  ) then
    alter publication supabase_realtime add table public.user_starred_teachers;
  end if;
end $$;
