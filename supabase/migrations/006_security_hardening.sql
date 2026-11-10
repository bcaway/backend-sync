-- ==============================================================================
-- BCAway Supabase Security Hardening Migration
-- Migration: 006_security_hardening.sql
-- ==============================================================================
-- Fixes:
-- 1. VULN-01: Restricts WRITE (INSERT/UPDATE/DELETE) on public.teachers exclusively
--    to callers possessing the admin key (service_role) or verified admin claims.
-- 2. VULN-02: Fixes domain access on public.teachers and public.teacher_absences
--    so that ONLY users authenticated via admin token or logged into BCAway
--    (@bergen.org and authorized @bcaway.app) can read data. Drops legacy public
--    read policies.
-- 3. VULN-04: Enforces that @bcaway.app accounts can ONLY be created via the
--    Supabase Dashboard / Admin API and cannot be created via public self-signup.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Hardened Domain Check Function (VULN-04)
-- ------------------------------------------------------------------------------
create or replace function public.check_user_domain()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Validate email is provided
  if new.email is null then
    raise exception 'Access Denied: An email address is required.';
  end if;

  -- Validate authorized domains: ONLY @bergen.org and @bcaway.app are permitted
  if lower(new.email) not like '%@bergen.org' and lower(new.email) not like '%@bcaway.app' then
    raise exception 'Access Denied: Only @bergen.org and authorized @bcaway.app email addresses are permitted.';
  end if;

  -- Ensure @bcaway.app accounts CANNOT be created via public self-signup.
  -- Accounts under @bcaway.app MUST be provisioned directly via the Supabase Dashboard
  -- or Admin API by an administrator (where email is auto-confirmed or invited).
  -- Public signups always generate an unconfirmed user with confirmation_token set.
  if lower(new.email) like '%@bcaway.app' and tg_op = 'INSERT' then
    if (new.email_confirmed_at is null and new.invited_at is null)
       or (new.confirmation_token is not null and new.confirmation_token <> '') then
      raise exception 'Access Denied: @bcaway.app accounts cannot be created via public signup. They must be provisioned directly via the Supabase Dashboard by an administrator.';
    end if;
  end if;

  -- Prevent existing users from changing their email to @bcaway.app via public self-service
  if lower(new.email) like '%@bcaway.app' and tg_op = 'UPDATE' and old.email is distinct from new.email then
    if new.email_confirmed_at is null and new.invited_at is null then
      raise exception 'Access Denied: Changing email to an @bcaway.app address is restricted to administrators.';
    end if;
  end if;

  return new;
end;
$$;

-- Ensure the trigger is attached to auth.users
drop trigger if exists on_auth_user_created_domain_check on auth.users;
create trigger on_auth_user_created_domain_check
  before insert or update of email on auth.users
  for each row execute function public.check_user_domain();


-- ------------------------------------------------------------------------------
-- 2. Secure public.teachers Table (VULN-01 & VULN-02)
-- ------------------------------------------------------------------------------
alter table public.teachers enable row level security;

-- Drop all existing policies on public.teachers
drop policy if exists "Allow select on teachers" on public.teachers;
drop policy if exists "Allow manage teachers" on public.teachers;
drop policy if exists "Allow read teachers to authorized users" on public.teachers;
drop policy if exists "Allow admin manage teachers" on public.teachers;

-- SELECT policy: Only admin token or logged-in BCAway accounts (@bergen.org, @bcaway.app)
create policy "Allow read teachers to authorized users"
on public.teachers
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (auth.jwt() ->> 'role') = 'service_role'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
);

-- WRITE policy (INSERT / UPDATE / DELETE):
-- EXCLUSIVELY permitted for admin key (service_role) or verified admin token.
-- No anonymous users or standard authenticated users can write to teachers.
create policy "Allow admin manage teachers"
on public.teachers
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
-- 3. Secure public.teacher_absences Table (VULN-02)
-- ------------------------------------------------------------------------------
alter table public.teacher_absences enable row level security;

-- Drop all existing select policies on public.teacher_absences (including legacy public policy)
drop policy if exists "Public can read teacher absences" on public.teacher_absences;
drop policy if exists "Allow select to authenticated bergen.org users" on public.teacher_absences;
drop policy if exists "Allow select to authenticated bergen.org and bcaway.app users" on public.teacher_absences;
drop policy if exists "Allow read teacher absences to authorized users" on public.teacher_absences;

-- SELECT policy: Only admin token or logged-in BCAway accounts (@bergen.org, @bcaway.app)
create policy "Allow read teacher absences to authorized users"
on public.teacher_absences
for select
to authenticated, service_role
using (
  auth.role() = 'service_role'
  or (auth.jwt() -> 'app_metadata' ->> 'is_admin') = 'true'
  or (auth.jwt() ->> 'role') = 'service_role'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bergen.org'
  or lower(coalesce(auth.jwt() ->> 'email', '')) like '%@bcaway.app'
);
