# Project context

Current high-level picture of InfoBot. Update when the architecture changes. Last reviewed 2026-10-01.

## What it does

A WhatsApp bot (Meta Cloud API, test number +91 82753 26028) that fact-checks forwarded content for Indian users. Accepts text, images, voice notes and videos. Replies in English, Hindi or Marathi with, per claim: a verdict (true / false / misleading / unverifiable), a confidence label, a short explanation and sources. Medical-advice requests get a fixed hard stop instead. Messages with nothing to check get a friendly reply about what the user wrote plus what the bot can do (goal: a very low refusal rate).

Public site (landing, privacy policy, data deletion) in `site/`, deployed on Netlify at infobot-wsp.netlify.app.

## Request flow

```
Meta webhook POST /webhook  (HMAC signature check, 200 immediately, work in a background task)
  -> idempotency (hashed wamid unique) -> load the user's reply language (user_prefs)
  -> language request? (tapped button, or exact text `language` / `भाषा` / a language name): handle it and stop
  -> per-user hourly rate limit (Postgres function) -> read receipt
  -> media to labelled text (main.py + normalize.py):
       image : Gemini vision -> [Text inside the image] / [Caption...] / [What the image shows]
       audio : ElevenLabs Scribe -> cleaned transcript -> [Voice note transcript] (+ caption)
       video : ffmpeg extracts audio -> same as audio
  -> orchestrator.run_text_pipeline:
       guard.screen_text  (strip hidden chars, pattern rules, Groq Prompt Guard 2)   -> blocked: fixed refusal, nothing else runs
       exact cache lookup by whole-message hash                                       -> hit: reply from cache
       classify.extract_and_classify  (one Gemini call): input_kind + up to 3 claims, each with tier, language, domain, search queries
       non-claim kinds -> compose_nonclaim_reply (model's friendly_reply, else static per-kind text, + capability line)
       claims -> per claim, in parallel: cache (exact, then semantic) -> verify by tier -> ClaimOutcome
  -> compose (one verdict card, or numbered blocks for several claims; localised labels; fits 4096 chars)
  -> send as a contextual reply -> only then write cache rows -> mark submission done
  -> first contact only: send the three language buttons (once)
  reply_lang (the user's choice, or None = follow each claim's own language) flows to classify, verify, cache-hit translation and compose
```

Tiers: T1 model knowledge (confidence capped 60/25, never High); T2 Tavily search with structural confidence from distinct agreeing domains (85/55/20; no sources forces "unverifiable"); T3a soft general health guidance, no verdict, escalates to T3b at runtime if the answer depends on the person; T3b static hard stop. Input kinds that are not claims: greeting, question_about_bot, opinion_or_prediction, personal_or_private, out_of_scope_request, health_advice_request (-> T3b text), media_authenticity, unclear, abusive_or_manipulation.

## External services and fallbacks

| Job | Primary | Fallback |
|---|---|---|
| Injection screen | regex rules + Groq `meta-llama/llama-prompt-guard-2-86m` | ML layer fails open; regex still applies |
| Classify, T1, T3a, T2 synthesis | Gemini `gemini-3.1-flash-lite` (key 1, then `GEMINI_API_KEY_FALLBACK`) | Groq `openai/gpt-oss-120b` (text only) |
| Image reading | same Gemini model (vision) | none; a caption alone is still checked |
| Web search | Tavily (basic depth; bilingual + one fact-check-domain pass, up to 3 searches per claim) | none; no results -> fixed "no reliable sources" reply |
| Speech-to-text | ElevenLabs `scribe_v1` | Groq `whisper-large-v3` |
| Embeddings (semantic cache) | Gemini `gemini-embedding-001`, 768-dim | none; semantic lookup skipped |
| Database | Supabase (REST, service key) | none |

Gemini keys rotate on a 429 with a cooldown (30 s per-minute, 10 min daily); at most 4 concurrent Gemini calls. Settings and env names: `app/config.py`, `.env.example`.

## Data (Supabase)

`user_prefs` (hashed number -> chosen reply language, `prompted` flag; SQL in `db/migrations/001_user_prefs.sql`), `claims` (cache: hash, English text, embedding, tier, verdict incl. `guidance`/`refused`, confidence, explanation_en, sources, times_seen), `submissions` (hashed wamid and phone, status, reply wamid, cache_hit, error), `feedback` (reactions joined to replies), rate-limit counters behind an `increment_rate_limit` RPC, and a trending query. `match_claims` RPC does the pgvector search (threshold 0.90). Schema details: TODO - Needs confirmation (no migration files are in the repo; the schema was applied to Supabase directly).

## Privacy design

Raw phone numbers and message IDs are never stored or logged, only salted hashes. User text is not retained beyond what the cache stores (the English claim text). Data-retention purge is not implemented (TODO - Needs confirmation of the intended window).

## Current technical state (2026-10-01)

- Still runs locally (laptop) behind ngrok. A deployment to one EC2 instance (Docker + Caddy, images in ECR, GitHub Actions over OIDC/SSM) is built and tested locally but **not yet deployed**; see `deploy/README.md` and DECISION-017. AWS region and domain: TODO - Needs confirmation.
- Gemini key 1 is at its free daily cap; key 2 is live. Billing is not enabled. Cost estimate measured on 5 messages: about $0.001-0.0035 per fresh message at $0.25/$1.50 per million tokens.
- The reply redesign (language preference, WhatsApp formatting, mixed-message handling, blurred-image reply) is **uncommitted**; see `docs/PROGRESS.md`.
- Tests: 305 offline. Live: 15/15 tier fixtures; last full scenario run 91/91 (before the final classify-prompt edit; the medical/non-claim subset re-run was cut short by quota).

## Known limitations

- Cache hits are translated on the fly for non-English readers (20 s cap; falls back to English text with English labels).
- The interactive language buttons have not yet been seen on a real phone.
- Photos/videos cannot be judged for authenticity (no reverse image search); the bot says so and points to Google Lens/TinEye.
- Scribe can invent words from mumbled audio; there is no confidence gate yet.
- Pending work and ideas: `docs/TASKS.md`.
