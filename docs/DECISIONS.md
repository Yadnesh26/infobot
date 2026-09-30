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

Decision: Verdict, confidence, sources and footer labels follow the claim's language (en/hi/mr). English claims always use the English explanation (the model was drifting into Hindi because of Hindi search results). Cache hits render in English because only `explanation_en` is stored.

Reason: A Hindi reader should not get an English frame; storing per-language explanations needs a schema change.

Impact: `compose.py`, `messages.py`, `verify._local_text`. See the TASKS item on localised cache hits.
