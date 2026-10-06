-- ==============================================================================
-- BCAway Supabase Migration: Drop Unused user_push_tokens Table
-- Push tokens and notifications are managed via Cloudflare D1 (bcaway-notifications)
-- ==============================================================================

drop table if exists public.user_push_tokens cascade;
