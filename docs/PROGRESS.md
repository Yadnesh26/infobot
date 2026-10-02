# Progress

Newest first. Only meaningful changes; no transcripts.

## 2026-10-01 (later still): AWS EC2 deployment with Docker and GitHub Actions (UNCOMMITTED, NOT YET DEPLOYED)

### Completed
- `Dockerfile` (python 3.12-slim + ffmpeg, non-root, health check, graceful 40 s stop), `.dockerignore`, `requirements.lock` (24 packages, generated inside a Linux Python 3.12 container).
- `deploy/`: `docker-compose.yml` (bot + Caddy, bot port not published, read-only root, no capabilities), `Caddyfile` (auto-HTTPS, only `/webhook` `/health` `/trending` reachable, 1 MB body cap), `remote-deploy.sh` (runs on the instance: ECR login, pull, up, wait healthy, **automatic rollback**), `aws/infobot-stack.yaml` (CloudFormation: EC2 AL2023 with no SSH, Elastic IP, ECR repo, instance role, GitHub OIDC role), `README.md` (runbook), `scripts/make_app_env.py` (builds the server's secrets file from only the settings the app reads).
- `.github/workflows/ci.yml` (tests + image build on every push/PR) and `deploy.yml` (on push to `main`: tests, build, push to ECR, deploy over AWS SSM). GitHub holds only four non-secret *variables*; AWS access is a short-lived OIDC token limited to the `production` environment.

### Files Changed
- New: `Dockerfile`, `.dockerignore`, `requirements.lock`, `deploy/*`, `.github/workflows/*`, `scripts/make_app_env.py`
- Edited: `.gitignore`

### Current State
- Verified locally: image builds (864 MB) with no `.env` inside; runs as non-root, read-only filesystem, cap-drop ALL; passes its health check; webhook handshake and signature rejection behave; bot's own ffmpeg code runs inside it; all 305 unit tests pass inside the image's Linux Python 3.12 with the lock; Caddy stack tested with its local certificate (only public paths reachable, `/docs` 404, 2 MB body 413, HTTP redirects to HTTPS, app port not published); rollback script tested against stand-in docker/aws commands (healthy, unhealthy with previous image, unhealthy first deploy, missing `app.env`); `actionlint`, `shellcheck`, `cfn-lint` clean. Testing found and fixed two real bugs: a colon in a compose `${VAR:?msg}` message broke the YAML, and a comma in a CloudFormation description broke the template.
- **Nothing has touched AWS or GitHub yet**: no AWS/GitHub CLI or remote exists on this machine. The stack, the OIDC role, SSM delivery, Caddy's real certificate and the GitHub variables are unverified until the runbook is followed.

### Remaining Work
- Commit, create the GitHub repo, `git branch -M main`, push (waiting on the user). Then follow `deploy/README.md` steps 2-6.
- After the first successful deploy: switch Meta's webhook to the new URL (could be done with the Meta MCP tools), stop the local server and ngrok.

### Important Notes
- The server's `app.env` must contain only what `app.config.Settings` reads. `scripts/make_app_env.py` enforces that; the local `.env` also holds `SUPABASE_DB_PASSWORD`, `SUPABASE_SECRET_KEY`, `SUPABASE_PUBLISHABLE_KEY`, `WA_BUSINESS_ACCOUNT_ID`, which must not go to the server.
- `PHONE_HASH_SALT` must stay the same across environments or stored hashes stop matching.
- `architecture-workflow.md` section 9 (infrastructure) and the "no Dockerfile or CI" lines are now out of date; that file was produced by another session and was not edited here.

## 2026-10-01 (later): Reply redesign: language preference, scannable formatting, mixed messages (UNCOMMITTED)

### Completed
- **Per-user reply language.** After a first-time user's first answer, the bot sends three tap buttons (English / हिन्दी / मराठी). Typing `language` / `भाषा` / `bhasha` (exact match only) reopens them; typing a language name sets it directly. Stored in new Supabase table `user_prefs` (hashed number, language, prompted flag; RLS on; SQL in `db/migrations/001_user_prefs.sql`). The chosen language drives the classifier's `friendly_reply`, every explanation, all labels and all static messages. With no choice, each answer follows its claim's language (old behaviour). Unknown language on media with no text is now English only (was all three stacked).
- **Cached answers are translated** for non-English readers (one small Gemini call, 20 s cap, falls back to English text with English labels, never a mixed reply). Fixes the old "cache hits are English only" limitation.
- **WhatsApp-formatted replies.** Verdict first (`❌ *FALSE*`, confidence dot, quoted claim, `*Why:*`, `*Sources*`, footer). Several claims open with an at-a-glance summary (`*3 claims checked*` + one line each), then divider-separated blocks. Source lists shrink before any claim is cut. Formatting characters in user/model text are stripped so they cannot break the layout.
- **Mixed messages.** Hearsay about private or local things ("my grandmother says...") is `personal_or_private`, not a claim. A message that is only chit-chat/requests/opinions gets one bulleted line per part. A message with claims plus chit-chat gets the verdicts plus a `💬 About the rest of your message` note (before, the note was silently dropped).
- **Blurred/blank images** (and images the vision model calls a "photo" while describing them as featureless) now get "send a clearer picture" instead of the can't-judge-authenticity text. If vision fails and only a bare caption is left, the user is told we could not read the image instead of "unclear".
- Image calls get a 60 s timeout (was 30 s), errors log with `repr` (timeouts used to log blank). Localised the rate-limit and generic-error messages.
- Privacy page now lists Groq, the Gemini free-tier data-use note and the language choice; data-deletion page lists the language choice (site files edited, **not yet redeployed**).
- Tests: 305 offline (was 220); harness now 102 scenarios (new groups J mixed-message, L reply-language); `tests/conftest.py` gives every test an offline `offline_prefs` fixture.

### Files Changed
- New: `app/db/prefs.py`, `app/pipeline/language.py`, `db/migrations/001_user_prefs.sql`, `tests/test_language.py`
- Rewritten: `app/pipeline/compose.py`, `app/main.py` (handler + media handlers)
- Edited: `app/pipeline/{messages,classify,orchestrator,verify,normalize}.py`, `app/prompts/{extract_classify,extract_image_text,verify_t1,verify_t2,verify_t3a}.txt`, `app/providers/gemini.py`, `app/whatsapp/{parser,client}.py`, `scripts/{scenarios,run_scenarios}.py`, `site/{privacy,data-deletion}.html`, `tests/*`

### Current State
- 305 offline tests pass; live tier fixtures 15/15 after the prompt changes; live: 15 of 17 targeted scenarios on the first pass, the 2 misses were fixed and re-verified (blurred image, vision timeout) or were Gemini timeouts (translation correctly fell back to English; passes alone).
- Server restarted on this code (no `--reload`).
- **Not verified on real WhatsApp:** the interactive buttons (payload shape follows Meta's documented format and is unit-tested, but has never reached a phone) and the first-contact flow. Test with a number that has no `user_prefs` row, or delete its row first.

### Remaining Work
- Commit (waiting on the user). Redeploy the Netlify site for the two page edits (how it is deployed: TODO - Needs confirmation).
- Full live scenario run (102) once quota allows.
- Watch Gemini latency: image scenarios took 86 s and 139 s and the log showed `ReadTimeout` on 30 s text calls while two scenarios ran at once.

### Important Notes
- `friendly_reply` in the classifier schema is now required and is also used as the side note for claim messages. A reply containing a newline is treated as multi-part and is sent without the fixed capability line.
- `ClaimOutcome.local_lang` records the language the explanation really is in; labels follow it, so untranslated English text always gets English labels.
- Tests that touch `app.main` get prefs/buttons stubbed by the autouse `offline_prefs` fixture; the real `app.db.prefs` functions are stubbed too, so data-layer tests must capture them at import time (see `tests/test_language.py`).

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
