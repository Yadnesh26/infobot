-- Applied to Supabase on 2026-10-01 (via the Supabase MCP). Kept here so the schema change is reviewable.
-- Per-user reply language. Keyed by the salted hash of the phone number, never the number itself.
-- `language` is null until the user picks one; `prompted` records that the language buttons were
-- already offered once so we never nag. Row-level security is on with no policies: only the
-- service key (used by the bot) can read or write it, like every other table.
create table if not exists public.user_prefs (
  wa_user_hash text primary key,
  language text check (language in ('en', 'hi', 'mr')),
  prompted boolean not null default false,
  updated_at timestamptz not null default now()
);
comment on table public.user_prefs is 'Per-user reply language. Keyed by the salted hash of the phone number, never the number itself.';
alter table public.user_prefs enable row level security;
