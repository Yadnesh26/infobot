# Tasks

Add yourself to "In Progress" (with the files you will touch) before starting; move the item when done. Nobody owns anything right now.

## In Progress
- (none)

## Planned

Verification and release
- [ ] Commit the 2026-10-01 work (waiting on the user to ask). Exclude `logo-options/` unless the user wants it.
- [ ] Confirm a real WhatsApp message is answered by the restarted server; send `claims_test/claim8` then the others; read the server log.
- [ ] Re-run the medical and non-claim live scenarios once quota allows: `run_scenarios.py F A06 D04 I01 I09 C03`, then the full suite once.
- [ ] Decide on Gemini billing (about $0.001-0.0035 per fresh message); set a budget alert first.

Product and accuracy
- [ ] Reverse image search for photos/videos (today: "I can't tell", plus a Google Lens/TinEye tip).
- [ ] Google Fact Check Tools API as the first step of T2 (cheap, high precision).
- [ ] Localised cache hits (translate the cached English explanation on a hit, or store per-language text).
- [ ] Gate Scribe transcripts on its confidence / language probability (it invents sentences from mumble).
- [ ] Trim `extract_classify.txt` (~2,100 tokens per message) so the Groq fallback is usable and cost drops.
- [ ] Use reaction feedback (already stored) to find bad replies; grow the fixture set from real misses.

Reliability and operations
- [ ] Durable job queue and retries (background tasks are lost on restart).
- [ ] Move off laptop + ngrok to a real host; add README/Dockerfile. Host: TODO - Needs confirmation.
- [ ] Global daily spend cap and alerts; slow/block users who repeatedly trigger the injection screen; log security events.
- [ ] Retention purge for stored data. Window: TODO - Needs confirmation.
- [ ] Add `Pillow` to `requirements-dev.txt` (used by the asset scripts). Confirm.
- [ ] Commit or store the Supabase schema as migrations (none in the repo).
- [ ] Localise the rate-limit and generic-error messages in `main.py` (still English only).

Owner-side items still unanswered
- [ ] Set the WhatsApp profile "about" text and website via the API.
- [ ] Confirm the Meta website/privacy-policy URL fields are accepted (Meta once said the site "seems broken").

## Completed
- [x] M1-M8 (webhook, classify/T1, cache, T2 search, safety tiers, images, audio/video, reactions/trending/rate limit)
- [x] Public site on Netlify, InfoBot logo live on the site and WhatsApp profile
- [x] Privacy hashing, Groq fallbacks, startup sweep
- [x] Multi-claim pipeline, injection guard, low-rejection replies, localisation, media hardening (uncommitted)
- [x] Gemini key rotation, concurrency cap, clearer provider-failure reply (uncommitted)
- [x] Live scenario suite (91), claims_test folders, shared-memory docs

## Blocked
- [ ] Full live scenario re-run on the final prompt
  - Reason: Gemini key 1 is at its free daily cap, key 2's 500/day is shared with the live bot; resets daily.
