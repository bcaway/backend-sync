-- ==============================================================================
-- BCAway Supabase Domain Check & Row-Level Security (RLS) Migration
-- Enforces that ONLY @bergen.org emails can sign in or access teacher absence data.
-- ==============================================================================

-- 1. Create a function to validate email domain before user creation/update in auth.users
create or replace function public.check_user_domain()
returns trigger as $$
begin
  if new.email is null or lower(new.email) not like '%@bergen.org' then
    raise exception 'Access Denied: Only @bergen.org email addresses are authorized for BCAway.';
  end if;
  return new;
end;
$$ language plpgsql security definer;

-- 2. Attach trigger to auth.users to block any non-@bergen.org user from being created
drop trigger if exists on_auth_user_created_domain_check on auth.users;
create trigger on_auth_user_created_domain_check
  before insert or update of email on auth.users
  for each row execute function public.check_user_domain();

-- 3. Enable Row-Level Security on teacher_absences table
alter table public.teacher_absences enable row level security;

-- 4. Policy: Allow SELECT only to authenticated users with @bergen.org emails
drop policy if exists "Allow select to authenticated bergen.org users" on public.teacher_absences;
create policy "Allow select to authenticated bergen.org users"
on public.teacher_absences
for select
to authenticated
using (
  lower(auth.jwt() ->> 'email') like '%@bergen.org'
);

-- Note: Backend automated sync worker (service_role) bypasses RLS automatically
-- so absence scraping continues inserting/updating rows without interruption.