-- How each message was read the first time, so the same picture / voice note / video / text is
-- always read, and therefore answered, the same way. `msg_key` is "<kind>:<sha256>" of the media
-- bytes or of the normalised text, never the content itself. `interpretation` is the model's
-- structured reading (claims, tiers, language); the verdicts stay in `claims`. Row-level security
-- is on with no policies: only the service key (used by the bot) can read or write it.
create table if not exists public.message_index (
  msg_key text primary key,
  reply_lang text check (reply_lang in ('en', 'hi', 'mr')),
  interpretation jsonb not null,
  created_at timestamptz not null default now()
);
comment on table public.message_index is 'First reading of a message (keyed by a hash of the message), so repeats are read and answered identically.';
alter table public.message_index enable row level security;
