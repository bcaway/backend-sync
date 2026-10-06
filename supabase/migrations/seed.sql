-- ==============================================================================
-- BCAway Supabase Seed: Initial Known BCA Faculty & Aliases
-- (Optional to apply: run manually via Supabase SQL Editor or psql)
-- ==============================================================================

insert into public.teachers (name, aliases) values
  ('John Doe', array[]::text[]),
  ('Jane Smith', array['Smith, Jane']::text[]),
on conflict (name) do update set aliases = excluded.aliases;
