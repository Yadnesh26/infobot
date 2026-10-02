# Decisions

Important technical decisions only. Do not reverse one without a reason and a new entry.

## DECISION-001: ElevenLabs Scribe as primary speech-to-text

Date: 2026-09-29 (`0a3a03a`)

Decision: Transcribe audio/video with ElevenLabs `scribe_v1`, Groq `whisper-large-v3` as fallback, replacing Bhashini.

Reason: The owner has a large ElevenLabs credit balance and asked for it to be the first choice wherever it fits.

Alternatives: Bhashini (original plan), Whisper only.

Impact: `app/providers/elevenlabs.py`, `whisper.py`, `normalize.py`.

## DECISION-002: Confidence is structural, never self-reported

Date: 2026-09-29

Decision: T1 confidence is fixed at 60 (model sure) or 25, never "High". T2 confidence comes from how many distinct domains back the verdict (85 / 55 / 20); no retrieved sources forces "unverifiable" and a fixed message.

Reason: Model-stated confidence is unreliable; retrieval-free checks must not look authoritative.

Alternatives: Ask the model for a number.

Impact: `verify.py`, `confidence.py`, labels in `compose.py`.

## DECISION-003: Medical safety tiers with a static hard stop

Date: 2026-09-29

Decision: Health-adjacent folk beliefs get soft guidance without a verdict (T3a); anything depending on the individual (doses, interactions, treatment choices, urgent symptoms, personal requests) gets a fixed text pointing to a doctor and the 104 helpline (T3b). T3a escalates to T3b at runtime if the answer depends on the person. The T3b text is never model-generated. Viral "X cures <disease>" claims are fact-checked (T1/T2), not refused.

Reason: Wrong personal medical output is the worst failure mode; refusing viral cure myths was bad UX and inconsistent.

Alternatives: Refuse all health content; answer everything.

Impact: `extract_classify.txt`, `verify_t3a.txt`, `refuse_t3b.txt`, `orchestrator._verify_claim`. A change to `extract_classify.txt` must keep `run_tier_fixtures.py` at 15/15.

## DECISION-004: Cache only what is safe and durable

Date: 2026-09-29, extended 2026-10-01

Decision: Write cache rows only after the reply was successfully sent. Never cache `unverifiable`, errors, blocked input, or input flagged suspicious by the injection screen. A single-claim message is keyed by the whole-message hash (so identical forwards skip classification); with several claims each is keyed by its own English text.

Reason: An unverifiable result reflects today's sources, not the claim; a poisoned cache entry would be served to every user who sends that claim.

Alternatives: Cache everything with a TTL.

Impact: `orchestrator.py`, `main.handle_message`.

## DECISION-005: Hash phone numbers and message IDs

Date: 2026-09-30 (`0462c77`)

Decision: Store and log only salted hashes of phone numbers and wamids; logs use `ref()`.

Reason: WhatsApp message IDs embed the sender's number in base64.

Impact: `app/util.py`, `app/db/submissions.py`, all logging.

## DECISION-006: Text fallback to Groq; no vision fallback

Date: 2026-09-30 (`ba13c84`)

Decision: Text model calls fall back from Gemini to Groq `gpt-oss-120b`. Image calls have no fallback; if vision fails, only the caption (if any) is checked.

Reason: Groq has no vision model in use; avoid a second paid vision provider.

Impact: `providers/gemini.py`, `fallback_llm.py`, `main._compose_image_reply`.

## DECISION-007: Multi-claim replies, capped at 3

Date: 2026-10-01

Decision: Extract up to 3 claims per message (more are dropped with a notice), each with its own tier and language; verify in parallel; one failing claim does not sink the others, but if all fail the error propagates.

Reason: Real forwards bundle several claims and mix languages. The cap bounds cost and keeps replies under WhatsApp's 4096 characters.

Alternatives: One claim only (old behaviour); unlimited.

Impact: `classify.py`, `orchestrator.py`, `compose.py`, `settings.MAX_CLAIMS_PER_MESSAGE`.

## DECISION-008: Layered injection defence; screen user segments only

Date: 2026-10-01

Decision: Before any model call: strip invisible/tag characters, apply regex rules (en/hi/mr), then Groq Prompt Guard 2 (block >= 0.5, flag/no-cache >= 0.05). Later stages wrap untrusted text, validate cited URLs against retrieved results, and sanitise model output. The ML layer fails open; pattern rules and the structural defences still apply. The ML check scores only the user's own segments, never our `[Text inside the image]`-style labels.

Reason: The worst realistic injection outcome is a poisoned cache entry. Prompt Guard scored stacked bracket headers at ~1.0 on harmless posters, which would have refused nearly every image.

Alternatives: Fail closed if the guard is down (would make outages total); regex only.

Impact: `guard.py`, `orchestrator.py`, tests in `tests/test_guard.py`.

## DECISION-009: Non-claim messages get a contextual reply, with a static per-kind floor

Date: 2026-10-01

Decision: The classifier must write a short reply about the specific message for greetings, bot questions, opinions, predictions, private matters and out-of-scope requests; if it returns none, a fixed per-kind text (en/hi/mr) is used; a fixed capability line follows. Only `health_advice_request`, `media_authenticity`, `abusive_or_manipulation` and `unclear` use fully static text. Claims must come from the user's own words, never from an image description alone; future announcements ("petrol will cost Rs 200 tomorrow") are claims, guesses about outcomes ("India will win") are not.

Reason: The owner wants a very low rejection rate without the bot stepping outside fact-checking.

Impact: `extract_classify.txt`, `compose.compose_nonclaim_reply`, `messages.py`.

## DECISION-010: Run subprocesses in a thread

Date: 2026-10-01

Decision: Run ffmpeg/ffprobe with `asyncio.to_thread(subprocess.run, ..., timeout=...)`, not `asyncio.create_subprocess_exec`.

Reason: Under `uvicorn --reload` on Windows the selector event loop raises `NotImplementedError` for async subprocesses; every live voice note and video failed.

Impact: `normalize._run`.

## DECISION-011: Two Gemini keys with rotation; capped concurrency

Date: 2026-10-01

Decision: `GEMINI_API_KEY` first, then `GEMINI_API_KEY_FALLBACK` (a different Google account, so a separate quota) on a 429, with a per-key cooldown; at most 4 concurrent Gemini calls; keys travel in the `x-goog-api-key` header.

Reason: Testing exhausted the free 500/day cap and took the live bot down; the Groq fallback is too small (8,000 tokens/min) to cover it.

Alternatives: Enable billing (still recommended before real traffic; see `docs/TASKS.md`).

Impact: `providers/gemini.py`, `config.py`, `.env`.

## DECISION-012: Localised labels; cached answers stay English

Date: 2026-10-01

Decision: Verdict, confidence, sources and footer labels follow the claim's language (en/hi/mr). English claims always use the English explanation (the model was drifting into Hindi because of Hindi search results). Cache hits were rendered in English because only `explanation_en` is stored (superseded by DECISION-015: they are now translated).

Reason: A Hindi reader should not get an English frame; storing per-language explanations needs a schema change.

Impact: `compose.py`, `messages.py`, `verify._local_text`. See the TASKS item on localised cache hits.

## DECISION-013: Reply language is the user's explicit choice, offered once by buttons

Date: 2026-10-01

Decision: After a new user's first answer, send one message with three reply buttons (English / हिन्दी / मराठी). A tap, or typing `language` / `भाषा` / `bhasha`, or typing a language name, sets the language; it is stored in `user_prefs` keyed by the hashed phone number and used for every later reply. The offer is made once (`prompted` flag) and never repeated. The menu is the one deliberately trilingual message. Until a user chooses, each answer follows the language of its own claim. If the language is unknown (media with no text), the reply is English only, never three languages stacked.

Reason: Language guessing fails on Hinglish, images and voice notes, and stacking all three languages read as a mess. Buttons cost nothing (they fit WhatsApp's 3-button limit and are free inside the 24-hour window), need one small table, and do not block the first answer.

Alternatives: Ask before answering (blocks the first message and needs pending-message state); silently remember the last detected language (no table needed, but wrong for mixed-language users and gives them no way to correct it); always reply in all languages.

Impact: `app/db/prefs.py`, `app/pipeline/language.py`, `app/main.py`, `reply_lang` through the orchestrator, compose, classify and verify. Only exact whole-message matches count as commands, so a forwarded claim that mentions a language is still fact-checked.

## DECISION-014: Replies use WhatsApp formatting, verdict first

Date: 2026-10-01

Decision: Only WhatsApp's own markup is used (`*bold*`, `_italic_`, emoji, a divider line); there are no headings or tables. A single claim is a card: verdict line with emoji, a confidence dot line, the quoted claim, `*Why:*`, `*Sources*`, footer. Several claims start with an at-a-glance summary (one line per claim), then one divider-separated block each. Source lists shrink (and titles shorten) before any claim is cut to fit 4096 characters. `*`, `_`, `~` and backticks are stripped from user-derived headlines and model text so they cannot break or hijack the layout. The medical hard stop text is unchanged.

Reason: Users scan; the answer must be readable from the first line without scrolling, and several claims in one message need a visible separation.

Alternatives: Plain text as before; one long block per claim without a summary.

Impact: `compose.py`, `messages.py`; every test that asserted the old `Verdict:` layout.

## DECISION-015: Cached answers are translated, not shown in English

Date: 2026-10-01 (supersedes the cache-hit part of DECISION-012)

Decision: The cache still stores English only. For a reader of another language, one small Gemini call (20 s cap) translates the explanation (and headline on the whole-message path). If it fails or times out, the English text is shown with English labels; a reply never mixes a translated frame with untranslated text.

Reason: Popular rumours are exactly the cached ones, and a language preference would be meaningless for them otherwise.

Alternatives: Store per-language explanations (schema change, more write cost); leave hits in English.

Impact: `orchestrator._outcome_from_row`, the exact-hit path, `verify.translate_texts`, `ClaimOutcome.local_lang`.

## DECISION-016: What counts as a claim

Date: 2026-10-01

Decision: Second-hand anecdotes and local lore ("my grandmother says our village well is magical") are `personal_or_private`; hearsay about a public matter (a scheme, price, law, disease) is a claim. A claim must come from the user's own words, never from an image description. A message with claims plus chit-chat or requests keeps its claims and gets a one-sentence side note about the rest; a message with no claim gets one bulleted line per part. Blurred or blank images (by the vision model's `content_kind`, or by words like "blurred"/"featureless" in its description) are "unreadable" and get a request for a clearer picture, not the can't-judge-authenticity text.

Reason: The owner wants a very low rejection rate, answered in a way that shows the message was understood; a village story run through a web search ended in "couldn't find reliable sources, don't forward it".

Impact: `extract_classify.txt`, `compose`, `normalize.normalize_image`. Changes to the classifier prompt must keep `run_tier_fixtures.py` at 15/15.

## DECISION-017: Deploy as two containers on one EC2 instance, from GitHub Actions, without stored keys

Date: 2026-10-01

Decision: One EC2 instance (Amazon Linux 2023, x86_64, t3.small) runs `docker compose` with the bot and Caddy (automatic HTTPS). Images live in a private ECR registry. `deploy.yml` (push to `main`) runs the tests, builds and pushes the image, then uses AWS SSM Run Command to make the instance pull and restart it; `remote-deploy.sh` waits for the bot's health check and rolls back on failure. GitHub authenticates to AWS with OIDC for the `production` environment only, so no AWS keys are stored in GitHub. The instance has no SSH port; Session Manager is the way in. The bot's secrets are created once by hand in `/opt/infobot/app.env` and are never in GitHub, the image or CloudFormation. Dependencies are pinned in `requirements.lock`, generated inside a Linux container. Without a domain the host name is `<elastic-ip-dashed>.sslip.io`.

Reason: Meta requires a public HTTPS webhook; the bot needs ffmpeg and an always-on process (replies are sent from background tasks, which serverless platforms starve). This gives reproducible builds, deploys with automatic rollback, and the smallest credential surface for a first production deployment.

Alternatives: SSH deploy (needs port 22 open and a long-lived key in GitHub); GHCR instead of ECR (needs a pull token on the server); secrets in SSM Parameter Store (better auditing, more setup, listed as an upgrade); Render/Railway/Oracle free tiers (cheaper, but no AWS account integration and, for Render free, sleeping); ECS/Fargate or Lambda (more moving parts; Lambda suits neither ffmpeg nor post-response work); ARM (t4g) for lower cost (needs an arm64 image build).

Impact: `Dockerfile`, `deploy/`, `.github/workflows/`, `requirements.lock` (regenerate it inside `python:3.12-slim` when dependencies change, not from the Windows venv). One instance means a brief gap during each deploy (Meta retries, message ids de-duplicate) and no automatic recovery if the instance itself fails.
