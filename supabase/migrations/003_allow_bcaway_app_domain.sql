-- ==============================================================================
-- BCAway Supabase Domain Check & RLS Migration: Allow @bcaway.app Password Users
-- ==============================================================================
-- 1. Permits @bcaway.app email addresses as an authorized administrative exception.
-- 2. @bcaway.app accounts MUST be pre-created by an administrator with a password.
--    Direct public signups/magic-link registrations for @bcaway.app are rejected.
-- 3. Grants SELECT on teacher_absences to authenticated @bcaway.app users.
-- ==============================================================================

-- 1. Update the domain validation function on auth.users
create or replace function public.check_user_domain()
returns trigger as $$
begin
  -- Validate email is provided
  if new.email is null then
    raise exception 'Access Denied: An email address is required.';
  end if;

  -- Validate authorized domains: ONLY @bergen.org and @bcaway.app are permitted
  if lower(new.email) not like '%@bergen.org' and lower(new.email) not like '%@bcaway.app' then
    raise exception 'Access Denied: Only @bergen.org and authorized @bcaway.app email addresses are permitted.';
  end if;

  -- Ensure @bcaway.app accounts cannot be created via passwordless / public magic-link signup.
  -- Accounts under @bcaway.app MUST be provisioned directly with a password by an administrator.
  if lower(new.email) like '%@bcaway.app' and tg_op = 'INSERT' then
    if new.encrypted_password is null or new.encrypted_password = '' then
      raise exception 'Access Denied: Public signups are not permitted for @bcaway.app accounts. Accounts must be provisioned by an administrator with a password.';
    end if;
  end if;

  return new;
end;
$$ language plpgsql security definer;

-- 2. Ensure the trigger is active on auth.users
drop trigger if exists on_auth_user_created_domain_check on auth.users;
create trigger on_auth_user_created_domain_check
  before insert or update of email on auth.users
  for each row execute function public.check_user_domain();

-- 3. Update Row-Level Security policy on teacher_absences to include @bcaway.app
drop policy if exists "Allow select to authenticated bergen.org users" on public.teacher_absences;
drop policy if exists "Allow select to authenticated bergen.org and bcaway.app users" on public.teacher_absences;

create policy "Allow select to authenticated bergen.org and bcaway.app users"
on public.teacher_absences
for select
to authenticated
using (
  lower(auth.jwt() ->> 'email') like '%@bergen.org'
  or lower(auth.jwt() ->> 'email') like '%@bcaway.app'
);
