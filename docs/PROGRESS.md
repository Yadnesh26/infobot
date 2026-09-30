# Progress

Newest first. Only meaningful changes; no transcripts.

## 2026-10-01: Multi-claim redesign, injection guard, low-rejection replies, provider resilience (UNCOMMITTED)

### Completed
- Multi-claim pipeline: up to 3 claims per message, each with its own tier/language, verified in parallel; numbered reply blocks; per-claim cache.
- Non-claim handling rewritten for a low refusal rate: model-written `friendly_reply` about the user's actual message, static per-kind fallbacks (en/hi/mr), plus a capability line.
- Injection/abuse defence before any model call (`app/pipeline/guard.py`): invisible-character stripping, pattern rules (en/hi/mr), Groq Prompt Guard 2 on user segments, untrusted-text delimiters, cited-URL validation, output sanitising, media size caps. Suspicious input is answered but never cached.
- Media: ffmpeg/ffprobe run via `asyncio.to_thread(subprocess.run)` (Windows fix: all audio/video had been failing), audio trimmed to the duration cap, sound labels stripped from transcripts, images return labelled text + description, photo-only replies, corrupt/oversized/silent media handled.
- Replies localised (verdict/confidence/sources/footer labels in hi/mr); English claims always get English explanations.
- Gemini: key moved from URL to `x-goog-api-key` header (the key had been appearing in logs), 4-call concurrency cap, Retry-After handling, second key `GEMINI_API_KEY_FALLBACK` with per-key cooldown, embeddings rotate too. Groq fallback retries in plain JSON mode if schema mode 400s.
- When providers are the cause of a failure the user now gets the localised "at capacity, try again" message instead of "something went wrong" (`main._failure_reply`).
- Test tooling: `scripts/run_scenarios.py` + `scenarios.py` (91 live scenarios), `make_scenario_assets.py`, `make_claims_test.py` -> `claims_test/` (10 manual test messages), `run_tier_fixtures.py` updated to treat `health_advice_request` as T3B.
- Shared-memory docs created (`CLAUDE.md`, `docs/`).

### Files Changed
- New: `app/pipeline/guard.py`, `app/pipeline/messages.py`, `scripts/{run_scenarios,scenarios,make_scenario_assets,make_claims_test}.py`, `tests/test_{guard,multiclaim,gemini_keys}.py`, `claims_test/`, `CLAUDE.md`, `docs/`
- Rewritten: `app/pipeline/{classify,compose,orchestrator,normalize,verify}.py`, `app/prompts/{extract_classify,extract_image_text}.txt`
- Edited: `app/main.py`, `app/config.py`, `app/providers/{gemini,fallback_llm}.py`, `app/prompts/verify_t{1,2,3a}.txt`, `scripts/run_tier_fixtures.py`, `tests/*`, `.gitignore`, `.env.example`

### Current State
- 220 offline tests pass. 15/15 live tier fixtures. Last full live scenario run was 91/91, before the final edit to `extract_classify.txt` (folk-belief vs viral-cure vs personal-medical boundaries); the medical/non-claim subset re-run after that edit was cut short by the Gemini daily quota and has not been repeated.
- Server was restarted on this code 2026-10-01; it runs **without `--reload`**. The previous `--reload` server had silently stopped reloading and served stale code (old error message, old key-in-URL behaviour) for hours, so earlier "it fails" reports reflected old code plus the exhausted quota.
- No real WhatsApp message has been confirmed answered since the restart.

### Remaining Work
- Commit this work (waiting on the user; nothing in this entry is in git).
- Send `claims_test/claim8` (audio) then the rest to the live number and read the server log; re-run the medical/non-claim scenario subset once quota allows (`run_scenarios.py F A06 D04 I01 I09 C03`).
- See `docs/TASKS.md` for the rest.

### Important Notes
- Gemini free tier: 500 requests/day per key; key 1 was exhausted by testing. Groq fallback: 8,000 tokens/min (about 3 classify calls/min). The live bot and the test runners share these.
- Classify prompt is ~1,300 words (~2,100 input tokens per message); it dominates cost and is too big for Groq's per-minute cap.
- `clean_transcript` strips any `[bracketed]` label and short `(... -ing ...)` phrases from Scribe output; ElevenLabs Scribe can still hallucinate sentences from mumbled audio.
- ElevenLabs' image API refuses misinformation-style posters; Devanagari posters are rendered with headless Edge instead.
- The old log file of the stale server contains the Gemini key in URLs; rotate that key when convenient. The second key was pasted into a chat.

## 2026-09-30: Public site, logo, resilience, privacy hardening

### Completed
- Static public site (landing, privacy, data deletion) on Netlify; operator details filled in (`c653f91`, `9e0eb0e`).
- InfoBot logo (option 2, "सत्य" chat bubble) on site and as the WhatsApp profile picture (`9986649`).
- Groq text fallback, Groq-hosted Whisper fallback, startup sweep of stale pending submissions (`ba13c84`).
- Phone numbers and wamids stored/logged only as salted hashes (`0462c77`); `mark_read` made best-effort (`64f531a`).
- T2 fixes: short search queries, social-domain exclusion, uncited explanations replaced by a fixed "no sources" message (`07171c6`).

### Current State
- Superseded in detail by the entry above; committed and live at the time.

## 2026-09-29: Milestones M2-M8

- M2 extract/classify/T1 (`1008c4e`), M3 exact + semantic cache (`ce9a8d0`), M4 T2 via Tavily (`8dac84b`), M5 safety tiers T3a/T3b (`075f99c`), M6 image verification (`5d02b27`), M7 audio/video via ElevenLabs Scribe + ffmpeg (`a291026`), M8 reactions/feedback, trending, atomic rate limit (`996aac6`).
- Bhashini replaced by ElevenLabs Scribe as primary transcription (`0a3a03a`).

## 2026-09-28: M1

- Webhook skeleton with echo reply; review findings folded into the plan (`8e6adb1`).
