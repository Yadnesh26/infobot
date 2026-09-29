# WhatsApp Misinformation Verification Bot — Implementation Plan

A forward-and-verify WhatsApp bot. User forwards any suspicious message (text, image, audio, video); the bot normalizes it to text, verifies the claim with an LLM, and replies with a verdict and confidence, quoted against the original message.

---

## 0. Assumptions and open decisions

**Assumed (change if you disagree — most of the plan is unaffected):**

| Decision | Assumption | Why |
|---|---|---|
| Backend language | Python 3.11+ | Best SDK support for Gemini, Supabase, audio handling |
| Web framework | FastAPI + Uvicorn | Async-native, needed for fast webhook ack |
| Background jobs | FastAPI `BackgroundTasks` for MVP | Zero infra; upgrade to a real queue only if it becomes a bottleneck |
| Deployment | Render / Railway / Fly.io free tier | Needs a persistent HTTPS process, not just serverless functions |
| Embedding model | `gemini-embedding-001`, truncated to 768 dims via `output_dimensionality` | Matches the `vector(768)` already committed in the Phase 3 schema — no migration needed |
| Text reasoning model | `gemini-3.1-flash-lite` (pinned, not a `-latest` alias), via the newer Interactions API (`/v1beta/interactions`, not `generateContent`) | The flagship `gemini-3.8-flash` measured at only 20 requests/day on this account's Free Tier — unusable for iterative dev, let alone production. Flash-lite has a separate, much larger free quota and is plenty capable for structured extraction/classification/verdict JSON. Re-check both models' actual limits in AI Studio's rate-limit dashboard before scaling past dev |

**Locked in from earlier decisions:**

- WhatsApp Business Platform — Meta Cloud API, direct (no BSP)
- Gemini Flash for OCR, claim extraction, classification, verification, verdict generation
- ElevenLabs Scribe for transcription (paid, metered — ~$0.22/hr of audio, ~30 free min/month), with Whisper / self-hosted IndicConformer as fallback when quota or budget runs out. Chosen over Bhashini for reliability and setup speed — Bhashini's government registration process was the single slowest, least certain step in the whole credential list, and Scribe's published word error rate on Hindi/Marathi beats Whisper's in third-party benchmarks
- Groq as free text-reasoning overflow when Gemini's daily quota is hit (OpenRouter deferred for now — see credential setup notes)
- Tavily for T2 search grounding (1,000 free credits/month, no card required). Chosen over Gemini's built-in Google Search grounding tool, which returned "quota exceeded" immediately on this account even on a fresh request — strongly suggesting it now requires a linked Google Cloud billing account, not just free-tier usage. Tavily also returns clean, pre-summarized content rather than raw HTML SERPs, which is a better fit for feeding straight into the verification prompt
- Supabase (Postgres + pgvector) for caching, logging, trending feed
- Languages: Hindi, English, Marathi (plus code-mixed Hinglish/Romanized input)
- Tier system: T1 confident, T2 search-grounded, T3a soft, T3b hard stop
- No video-native analysis. Audio track only.

**Still genuinely open (decide during Phase 2):**

- Exact free-tier host
- Whether to add a public trending dashboard in v1 or defer

**Findings from the pre-build plan review (fold into design, not yet decided in code):**

- Background jobs need a durability story beyond `BackgroundTasks` — see §13.1a.
- Free-tier hosts cold-start on idle, which collides with the "ack in milliseconds" webhook requirement — see §15.
- Blocking calls (ffmpeg, sync SDK clients) must never run inline on the event loop — see §7.5.
- The embedding model's dimensionality must be confirmed *before* running the Phase 3 migration, not after — see §5.
- Quota sizing (Gemini + Groq + OpenRouter combined) hasn't been checked against a plausible viral-spike load — see §13.2.
- Compliance/liability (health-adjacent verdicts, retained hashed user activity under India's DPDP Act) needs an explicit policy, not just engineering mitigation — see §13.5.
- Rate limiting and multi-message ordering need explicit concurrency handling — see §13.2, §13.6.

---

## 1. Architecture overview

```
WhatsApp user
    │ forwards message
    ▼
Meta Cloud API ──webhook POST──► FastAPI /webhook
                                     │
                                (1) verify signature
                                (2) return 200 immediately  ◄── must be fast
                                (3) enqueue background job
                                     │
                                     ▼
                         ┌────── Pipeline worker ──────┐
                         │ a. download media (if any)  │
                         │ b. normalize → raw text     │
                         │ c. cache lookup             │
                         │ d. extract + classify claim │
                         │ e. verify (per tier)        │
                         │ f. compose reply            │
                         │ g. cache write              │
                         └──────────┬──────────────────┘
                                    │
                          POST /messages (contextual reply)
                                    ▼
                             WhatsApp user
```

Everything after step (2) is off the request path. The user gets a 200 in milliseconds; the actual verdict arrives seconds later as a separate message.

---

## 2. Phase 0 — Accounts and credentials

Complete all of these before writing code. Only ElevenLabs requires payment details on file (metered usage beyond its small free tier); everything else is free-tier, no card needed.

| Service | What to create | What you walk away with |
|---|---|---|
| Google AI Studio | API key | `GEMINI_API_KEY` |
| Supabase | New project | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` |
| Meta for Developers | App + WhatsApp product | `WA_PHONE_NUMBER_ID`, `WA_TOKEN`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET` |
| ElevenLabs | API key | `ELEVENLABS_API_KEY` — primary transcription (Scribe), paid/metered beyond ~30 free min/month |
| Tavily | API key | `TAVILY_API_KEY` — T2 search grounding, 1,000 free credits/month, no card required |
| Groq | API key | `GROQ_API_KEY` (fallback) |
| OpenRouter | API key | `OPENROUTER_API_KEY` (fallback — deferred for now) |

**Checkpoint:** you can `curl` Gemini and get a response; you can connect to Supabase from `psql` or the dashboard SQL editor.

---

## 3. Phase 1 — WhatsApp Business Account setup

### 3.1 Create the app
1. developers.facebook.com → My Apps → Create App → type **Business**.
2. Add the **WhatsApp** product to the app.
3. Meta auto-provisions a test WhatsApp Business Account and a **free test phone number**.

### 3.2 Add test recipients
- In the WhatsApp → API Setup panel, add your own number (and up to 4 more) as verified test recipients.
- **No Business Verification is required at this stage.** Defer it until the bot works end to end.

### 3.3 Get a non-expiring token
The token shown in API Setup expires in 24 hours. For anything beyond first-day poking:
1. business.facebook.com → Business Settings → Users → **System Users** → Add.
2. Assign the app with full control; assign the WhatsApp Business Account asset.
3. Generate token → select `whatsapp_business_messaging` and `whatsapp_business_management` scopes → choose **Never expires**.
4. Store as `WA_TOKEN`.

### 3.4 Grab the app secret
App Settings → Basic → App Secret. Store as `WA_APP_SECRET`. You need it to validate incoming webhook signatures.

### 3.5 Send one manual test message
Use the curl snippet Meta shows in API Setup to send a template message to your own number. Confirm it arrives before going further. This isolates credential problems from code problems.

**Checkpoint:** a message from Meta's servers reaches your phone.

---

## 4. Phase 2 — Project skeleton and local development

### 4.1 Directory layout

```
whatsapp-verify-bot/
├── app/
│   ├── main.py                 # FastAPI app, webhook routes
│   ├── config.py               # env var loading
│   ├── whatsapp/
│   │   ├── client.py           # send message, mark read, download media
│   │   ├── verify.py           # signature validation
│   │   └── parser.py           # webhook payload → internal Message object
│   ├── pipeline/
│   │   ├── orchestrator.py     # the whole flow, top to bottom
│   │   ├── normalize.py        # media → text
│   │   ├── classify.py         # claim extraction + tier assignment
│   │   ├── verify.py           # tiered verification
│   │   └── compose.py          # verdict → WhatsApp-ready reply text
│   ├── providers/
│   │   ├── gemini.py
│   │   ├── tavily.py           # T2 search grounding
│   │   ├── elevenlabs.py       # primary transcription (Scribe)
│   │   ├── whisper.py          # fallback
│   │   └── fallback_llm.py     # Groq (OpenRouter deferred)
│   ├── db/
│   │   ├── client.py
│   │   ├── cache.py            # exact + semantic lookup, write
│   │   └── models.py
│   └── prompts/
│       ├── extract_classify.txt
│       ├── verify_t1.txt
│       ├── verify_t2.txt
│       ├── verify_t3a.txt
│       └── refuse_t3b.txt
├── tests/
│   └── fixtures/               # sample webhook payloads, sample forwards
├── .env.example
├── requirements.txt
└── README.md
```

Keeping prompts as separate text files (not inline strings) matters — you will iterate on them far more than on the code.

### 4.2 Dependencies

```
fastapi
uvicorn[standard]
httpx
google-genai
supabase
python-dotenv
pydantic
```

Add later as needed: `openai` (Whisper fallback), `groq`.

### 4.3 Local tunnel
WhatsApp webhooks require a public HTTPS URL. For local dev:

```bash
ngrok http 8000
```

Take the `https://xxxx.ngrok-free.app` URL. Note it changes on every ngrok restart unless you have a reserved domain — re-registering the webhook each session is normal during development.

### 4.4 Register the webhook
1. App Dashboard → WhatsApp → Configuration → Webhook → Edit.
2. Callback URL: `https://xxxx.ngrok-free.app/webhook`
3. Verify token: any random string you choose; store the same value as `WA_VERIFY_TOKEN`.
4. Subscribe to the `messages` field. **This subscription is the single most common setup mistake** — without it, Meta receives your messages but forwards nothing to you, and everything looks silently broken.

**Checkpoint:** Meta's verification GET succeeds and your server logs an inbound webhook when you message the test number.

---

## 5. Phase 3 — Supabase schema

Run in the Supabase SQL editor.

**Before running this migration:** confirm the exact Gemini embedding model ID and its output dimensionality in AI Studio. The `vector(768)` below is a placeholder that matches common embedding sizes — do not run this against production with a guessed dimension, since changing it later means re-embedding every cached claim.

```sql
create extension if not exists vector;

-- One row per distinct claim we have ever verified.
create table claims (
  id              uuid primary key default gen_random_uuid(),
  claim_text_en   text not null,          -- normalized English form
  claim_hash      text not null unique,   -- sha256 of normalized text, exact-match key
  embedding       vector(768),            -- confirm dims against your embedding model
  tier            text not null,          -- 't1' | 't2' | 't3a' | 't3b'
  verdict         text not null,          -- 'true' | 'false' | 'unverifiable' | 'guidance' | 'refused'
  confidence      smallint,               -- 0-100, null for t3a/t3b
  explanation_en  text,
  sources         jsonb default '[]'::jsonb,
  times_seen      integer not null default 1,
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now()
);

create index claims_embedding_idx on claims
  using hnsw (embedding vector_cosine_ops);
create index claims_last_seen_idx on claims (last_seen_at desc);

-- One row per inbound message. Analytics + debugging + feedback join target.
create table submissions (
  id               uuid primary key default gen_random_uuid(),
  wa_message_id    text not null unique,   -- wamid, also our idempotency key
  wa_user_hash     text not null,          -- sha256 of phone number, never store raw
  claim_id         uuid references claims(id),
  input_type       text not null,          -- 'text' | 'image' | 'audio' | 'video'
  detected_lang    text,
  was_forwarded    boolean default false,
  frequently_fwd   boolean default false,
  cache_hit        text,                   -- null | 'exact' | 'semantic'
  latency_ms       integer,
  status           text not null default 'pending',
  error            text,
  created_at       timestamptz not null default now()
);

create index submissions_created_idx on submissions (created_at desc);

-- Emoji reactions on our verdict messages.
create table feedback (
  id               uuid primary key default gen_random_uuid(),
  submission_id    uuid references submissions(id),
  reply_message_id text,
  emoji            text,
  created_at       timestamptz not null default now()
);
```

### 5.1 Semantic search function

```sql
create or replace function match_claims(
  query_embedding vector(768),
  match_threshold float default 0.90,
  match_count int default 1
)
returns table (id uuid, claim_text_en text, verdict text, confidence smallint,
               explanation_en text, sources jsonb, tier text, similarity float)
language sql stable as $$
  select c.id, c.claim_text_en, c.verdict, c.confidence,
         c.explanation_en, c.sources, c.tier,
         1 - (c.embedding <=> query_embedding) as similarity
  from claims c
  where 1 - (c.embedding <=> query_embedding) > match_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;
```

**Tune `match_threshold` empirically.** Start at 0.90. Too low and you'll serve a verdict for a different claim — a correctness bug that is worse than a cache miss. Too high and you catch nothing but exact repeats. Log every semantic hit with its similarity score during testing so you can pick the threshold from real data rather than guessing.

### 5.2 Privacy note
Never store raw phone numbers. Hash with a salt held in env. You lose nothing analytically — you can still count distinct users and detect repeats.

### 5.3 Keeping the project awake
Supabase free projects pause after 7 days of inactivity. Add a GitHub Actions cron that hits a trivial endpoint daily:

```yaml
on:
  schedule:
    - cron: '0 6 * * *'
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -sS "$SUPABASE_URL/rest/v1/claims?select=id&limit=1" -H "apikey: $KEY"
```

**Checkpoint:** you can insert and semantically query a dummy claim from Python.

---

## 6. Phase 4 — Webhook layer

### 6.1 GET handler (one-time verification)

```python
@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == settings.WA_VERIFY_TOKEN):
        return PlainTextResponse(params.get("hub.challenge"))
    return PlainTextResponse("forbidden", status_code=403)
```

### 6.2 POST handler
Three responsibilities, in strict order:

1. **Validate the signature.** Meta signs every request with `X-Hub-Signature-256`. Compute `HMAC-SHA256(app_secret, raw_body)` and compare with `hmac.compare_digest`. Use the *raw* body bytes, not the re-serialized JSON — re-serialization changes whitespace and breaks the hash.
2. **Return 200 immediately.** Meta retries on non-200 and on slow responses. Your pipeline takes seconds; the ack must take milliseconds.
3. **Enqueue the job.**

```python
@app.post("/webhook")
async def receive(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        return Response(status_code=403)
    payload = json.loads(raw)
    for msg in extract_messages(payload):
        bg.add_task(handle_message, msg)
    return Response(status_code=200)
```

### 6.3 Idempotency
Meta retries. You will receive the same `wamid` more than once. Before processing, attempt an insert into `submissions` on the unique `wa_message_id`; on conflict, drop the job. This is the difference between one reply and three identical replies.

### 6.4 What to extract from the payload

| Field | Path | Use |
|---|---|---|
| Message ID | `messages[0].id` | Idempotency key + `context.message_id` on the reply |
| Sender | `messages[0].from` | Hash it, use as reply target |
| Type | `messages[0].type` | Routes to text / image / audio / video branch |
| Text | `messages[0].text.body` | Direct input |
| Media ID | `messages[0].{image,audio,video}.id` | Two-step download |
| Caption | `messages[0].image.caption` | Often carries the actual claim — do not ignore |
| Forwarded | `messages[0].context.forwarded` | Forwarded ≤5 times |
| Frequently forwarded | `messages[0].context.frequently_forwarded` | Forwarded >5 times — virality signal |
| Reaction | `messages[0].type == "reaction"` | Route to feedback handler, skip the pipeline entirely |

### 6.5 Immediate acknowledgement to the user
Optional but worth it: mark the message read and send a one-line "Checking this…" so the user isn't staring at silence for 8 seconds. Costs nothing (service window).

**Checkpoint:** forwarding a text message logs a parsed message object with the correct `wamid` and forward flags.

---

## 7. Phase 5 — Normalization (media → text)

All four input types converge on a single `raw_text` string.

### 7.1 Text
Pass through unchanged. No language detection, no preprocessing, no translation. The LLM handles script and code-mixing downstream.

### 7.2 Media download (two-step, all media types)
```
GET https://graph.facebook.com/v21.0/{MEDIA_ID}     → returns a short-lived url
GET {url}  with Authorization: Bearer WA_TOKEN      → returns the bytes
```
The second call **must** carry the auth header. Media is retrievable for 7 days server-side, but download it immediately.

### 7.3 Image → text
Single Gemini Flash call. Do not add a separate OCR stage — fold extraction into the same call that reads the image. Prompt it to return the visible text verbatim in its original script, plus any caption text you pass alongside. On the Interactions API this means passing a multimodal `input` array (`[{"type": "text", ...}, {"type": "image", "data": <base64>, "mime_type": ...}]`) rather than a plain string, with the image inlined as base64 — fine at WhatsApp's media sizes, well under the API's 20MB inline limit.

Handle "no readable text" explicitly — memes with only a photo and no words are common. That path should produce a graceful reply, not an exception. Trust the model's own `has_readable_text` flag over the text it fills in, not the other way around — a model that occasionally puts stray text in the field despite flagging false shouldn't get to override its own signal.

Once you have `raw_text` (OCR'd text plus caption, joined), hand it straight to the same text pipeline used for forwarded text messages (extract/classify/verify/compose) — an image is just a slower way to arrive at text, not a separate pipeline.

### 7.4 Audio / video → text
1. If video, extract the audio track (`ffmpeg -i in.mp4 -vn -acodec pcm_s16le out.wav`). Discard the video. You are not analyzing visuals.
2. Send audio to ElevenLabs Scribe.
3. On error, timeout, empty result, or free/paid quota exhausted → fall back to Whisper (API or self-hosted).
4. On both failing → reply honestly that the audio couldn't be processed.

Cap duration (e.g. 3 minutes) — this also caps per-message ElevenLabs cost, not just latency. A 40-minute forwarded audio file is a cost and latency problem with no upside.

### 7.5 Concurrency: never block the event loop
`ffmpeg` invocation and any non-async provider SDK calls (Bhashini, some Gemini/Groq clients) are blocking. If they run inline inside an `async def` handler, they stall the entire event loop — including the webhook ACK path for *other* users' concurrent requests, silently violating the "200 in milliseconds" requirement from §6.2. Run every blocking call through `asyncio.to_thread(...)` or as a subprocess with `asyncio.create_subprocess_exec`, never as a direct synchronous call inside request-handling code.

**Checkpoint:** each of the four input types produces a plausible `raw_text` string in a local test.

---

## 8. Phase 6 — Cache lookup

Runs *before* any verification work. Two stages:

**Stage 1 — exact.** `sha256(normalize(raw_text))` where `normalize` lowercases, collapses whitespace, and strips emoji/zero-width characters. Look up `claim_hash`. Near-zero cost, catches literal re-forwards.

**Stage 2 — semantic.** Only on exact miss. Embed the text, call `match_claims()`. Catches the far more common case: the same myth retyped or lightly edited.

On hit:
- Increment `times_seen`, update `last_seen_at`
- Record `cache_hit` on the submission
- Compose and send the reply from stored data
- **Skip the entire LLM pipeline**

This is what protects your Gemini free-tier quota during a viral spike, and it's why the most-forwarded (most harmful) claims get the fastest replies.

**Checkpoint:** forwarding the same message twice produces an instant second reply and a `cache_hit='exact'` row.

---

## 9. Phase 7 — Extract and classify

One Gemini Flash call does four jobs at once. Splitting these into separate calls quadruples your quota consumption for no accuracy gain.

**Draft prompt (`extract_classify.txt`):**

```
You are analyzing a message forwarded on WhatsApp in India. It may be in
English, Hindi, Marathi, Hinglish, or a mix, in any script.

Return ONLY a JSON object, no markdown fences, no commentary:

{
  "detected_language": "<bcp47 code of the dominant language>",
  "is_verifiable_claim": <true|false>,
  "claim_original": "<the core factual claim, in the original language>",
  "claim_english": "<the same claim translated to English>",
  "tier": "<t1|t2|t3a|t3b>",
  "domain": "<health|science|news|finance|social|other>"
}

Tier definitions:
- t1: a widely known claim you can assess confidently from general knowledge.
- t2: niche, recent, local, or unfamiliar; needs external sources to assess.
- t3a: health-adjacent but general and non-actionable (folk beliefs, food
  combinations, everyday habits). Answerable at a general level.
- t3b: specific and consequential medical content — dosages, drug
  interactions, treatment choices for a named condition, or urgent symptoms.
  Anything where a correct answer depends on the individual person.

If the message contains no verifiable factual claim (a greeting, a joke, a
personal message), set is_verifiable_claim to false and leave claim fields empty.
```

**Implementation notes:**
- Request JSON output mode if the SDK supports it; still wrap parsing in try/except and strip stray fences.
- On parse failure, retry once, then fail gracefully.
- `is_verifiable_claim: false` short-circuits to a friendly "nothing to check here" reply. Do not run verification on "good morning" forwards.
- Bump a t1 to t2 when `frequently_forwarded` is true. High virality justifies the extra search cost.

---

## 10. Phase 8 — Verification

Route on tier. Four distinct prompts, four distinct output shapes.

### 10.1 Tier 1 — model knowledge
Verify from the model's own knowledge. Output verdict + confidence + a short explanation in English. No search.

### 10.2 Tier 2 — search-grounded
1. Run searches in **both** English and the original language via Tavily (`app/providers/tavily.py`). A Marathi-specific rumor may only ever have been addressed in Marathi coverage; an English-only search will miss it and you'll wrongly return "unverifiable". Skip the second-language pass when the claim is already in English.
2. Prioritize: Indian fact-check outlets (BOOM, Alt News, Vishvas News, Newschecker, Factly), health authorities (WHO, ICMR, AIIMS), primary scientific sources, mainstream reporting — implemented as an extra `include_domains`-biased search pass over those outlets, merged with the general results rather than restricting to them exclusively.
3. Feed results to Gemini; require it to mark, per cited source, whether it supports or contradicts the verdict — this is what makes the confidence derivation below structural instead of a guess.
4. Return `unverifiable` honestly when nothing credible is found. A confident guess is worse than an admitted gap. Enforce this in code too, not just the prompt: if the model returns an empty source list, force the verdict to `unverifiable` regardless of what it claimed — never trust a verdict with nothing behind it.
5. Don't cache an `unverifiable` result. Unlike a real verdict, it describes today's available sources, not the claim itself — a breaking rumor's coverage improves over time, and caching the gap would make it permanent instead of letting the next forward try again.

**Confidence must be structural, not self-reported.** LLM-stated percentages are not calibrated. Derive it:

| Signal | Effect | Implemented as |
|---|---|---|
| Multiple independent credible sources agree | High | ≥2 distinct-domain sources marked `supports_verdict: true` → 85 |
| Single credible source | Medium | exactly 1 supporting domain → 55 |
| Sources conflict | Low, and say so in the reply | both supporting and contradicting domains present → 20 |
| No sources found | `unverifiable`, no number | empty source list → forced `unverifiable`, confidence `null` |
| Tier 1 (no retrieval) | Cap below what a well-sourced T2 can reach | 60 (confident) / 25 (unsure) — never reaches the ≥70 "High" bucket |

"Independent" is approximated as *distinct domain* — a cheap proxy that won't catch two outlets syndicating the same wire copy. Worth revisiting if that turns out to matter in practice.

### 10.3 Tier 3a — soft answer
Constraints, enforced in the prompt:
- **No verdict vocabulary.** Not "true", "false", "myth busted". Use "commonly believed", "not well supported by evidence", "generally considered low risk".
- **No personalization, no imperatives.** Never "you should". Describe the general understanding.
- **Always close with a referral** — as the last line, not the whole message.
- **No numeric confidence.** Label it as general guidance so it reads visibly differently from a T1 verdict.

The self-test to encode in the prompt: *if the correct answer would change based on the person's age, conditions, or medications, this is not t3a — reclassify as t3b.*

### 10.4 Tier 3b — hard stop
No content. No "generally speaking". A short, warm, clear redirect to a doctor or relevant professional. Optionally point to a national health helpline. This path never produces a verdict, never a confidence score, and never a hedged version of the answer.

---

## 11. Phase 9 — Reply composition and sending

### 11.1 Reply shape

```
🔍 Verdict: <one line>
Confidence: <High | Medium | Low>

<2–3 sentences, plain language, in the user's language>

Sources:
• <name> — <url>
• <name> — <url>

— Verified by <BotName>
```

Design rules:
- **Self-contained.** It must make sense when forwarded back into the group it came from, with no bot-conversation context. That's the whole point — you're using the same mechanism that spreads the rumor to spread the correction.
- **Short.** WhatsApp truncates long messages behind a "Read more" tap.
- **Translated back** into the user's original language.
- **Sources as real links**, only when actually retrieved. Never fabricate.
- Add a line when `frequently_forwarded` is set: *"This message has been forwarded many times."*

### 11.2 Sending as a contextual reply

```json
POST /{PHONE_NUMBER_ID}/messages
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "<user number>",
  "context": { "message_id": "<wamid of THEIR message>" },
  "type": "text",
  "text": { "preview_url": true, "body": "<reply>" }
}
```

The `context.message_id` is what threads the reply to the specific forward — essential when someone dumps five messages at once. Carry each message's own `wamid` through the pipeline alongside its text; never reply using the most recent one. The quoted bubble renders as long as the original is under 30 days old, which it always will be here.

Store the returned reply `wamid` on the submission row so reaction webhooks can be joined back to it.

### 11.3 Cache write
On a cache miss, after a successful reply: insert into `claims` with text, hash, embedding, tier, verdict, confidence, explanation, sources. Do this *after* sending so a DB hiccup never costs the user their answer.

---

## 12. Phase 10 — Feedback and trending

### 12.1 Reactions
A `reaction`-type inbound message carries the emoji and the `wamid` it was applied to. Match that against stored reply IDs, insert into `feedback`, and return without running the pipeline. Free usage signal: *X% of verdicts were reacted to positively.*

### 12.2 Trending feed
Already free from the cache table:

```sql
select claim_text_en, verdict, times_seen, last_seen_at
from claims
where last_seen_at > now() - interval '7 days'
order by times_seen desc
limit 20;
```

Expose as a read-only endpoint. A small public dashboard on top of this is the strongest demo asset in the project — most fact-check tools are one-off and reactive; an aggregate view of what is spreading right now is not.

---

## 13. Phase 11 — Resilience

### 13.1 Failure ladder

| Failure | Response |
|---|---|
| Gemini quota exhausted | Route text-only reasoning to Groq (OpenRouter deferred). Vision has no fallback — reply asking for the text version |
| ElevenLabs fails/times out/quota exhausted | Whisper → self-hosted IndicConformer → honest failure message |
| Search returns nothing | Return `unverifiable` with low confidence. Do not invent |
| JSON parse failure | Retry once, then generic failure reply |
| Media download fails | Ask the user to resend |
| Total pipeline failure | Always send *something*. Silence is the worst outcome |
| Process restarts mid-job (deploy, crash, host recycle) | The in-memory `BackgroundTasks` job is lost with no exception ever raised — see §13.1a |

**No path may end in silence.** Wrap the orchestrator in a top-level try/except whose handler sends a plain apology message and logs the error to `submissions.error`.

### 13.1a Background job durability
`BackgroundTasks` jobs live only in the worker process's memory. A redeploy, crash, or free-tier host recycle mid-job kills it with no exception path — the submission row is left at `status='pending'` forever, and the user gets nothing. This is a silent failure mode that the try/except in §13.1 cannot catch because the process itself is gone.

Mitigation for MVP (no new infra required): add a scheduled sweep (cron, or a check on app startup) that finds `submissions` rows with `status='pending'` older than a few minutes and either re-enqueues them or sends an apology reply. This is the minimum needed to make "no path may end in silence" actually true. If job volume grows, promote this to a real queue (e.g. a Postgres-backed queue table with `FOR UPDATE SKIP LOCKED`, or Redis/RQ) rather than patching `BackgroundTasks` further.

### 13.2 Rate limiting
Cap per-user submissions (e.g. 20/hour, keyed on hashed number). One user forwarding their whole chat history should not exhaust the daily Gemini quota for everyone.

**Make the check atomic.** A check-then-write pattern (`SELECT count`, then `INSERT` if under the cap) races under concurrent bursts — a user firing several messages within the same second can slip past the cap before the first insert lands. Use a single atomic statement instead, e.g. an `UPDATE ... SET count = count + 1 WHERE window = current_window() RETURNING count`, and reject only if the returned count exceeds the cap.

**Combined quota, not just per-user.** Per-user capping doesn't protect against many distinct users hitting a genuinely novel viral claim at once — the cache only helps once a claim has been seen. Before launch, size the combined free-tier throughput of Gemini + Groq against a plausible spike (e.g. N forwards/hour at X% cache-miss rate) and decide the degrade path (a "high volume right now, please retry shortly" reply) rather than discovering the ceiling live. ElevenLabs adds a cost dimension on top of throughput — a spike in audio forwards has a direct dollar cost, not just a quota risk, so alerting on ElevenLabs spend is worth setting up before any public push.

### 13.3 Timeouts
Set explicit timeouts on every outbound call (Gemini 30s, ElevenLabs 60s, search 15s). A hung provider call is an invisible job that never replies.

### 13.4 Service window
Replies are free only inside the 24-hour service window opened by the user's message. Within seconds you are always inside it — but if you ever add delayed or proactive messaging, that becomes a paid template message. Log a warning rather than silently failing if a send is attempted outside the window.

### 13.5 Compliance and liability
This bot renders verdicts on health-adjacent claims and retains hashed-but-linkable user activity (submissions, claim history) indefinitely by default. Treat this as a named risk alongside T3b misclassification, not only an engineering concern:
- Define a data retention/deletion window for `submissions` and `feedback` rather than keeping them forever — India's DPDP Act 2023 imposes real obligations around health-adjacent personal data even when hashed.
- Carry a visible disclaimer in every reply (this is informational, not medical advice) so the T3b hard-stop isn't the only place the bot signals its limits.
- Consider what happens if a user phrases a T3b question in a way that slips past classification into T2/T1 — the fixture-set adversarial pass in §14.3 should explicitly try this, and a false negative here should be tracked as a launch blocker, not a bug to fix later.

### 13.6 Multi-message ordering
Background jobs for a burst of messages from the same user run concurrently with no ordering guarantee — replies can arrive out of sequence. `context.message_id` threading (§11.2) keeps each reply attached to the right original message, which covers the confusion this would otherwise cause, but don't assume replies will arrive in the order the originals were sent.

---

## 14. Phase 12 — Testing

### 14.1 Build a real fixture set
Collect 40–60 actual forwards. Skew toward what will actually arrive:
- Low-resolution meme screenshots with Devanagari text
- Hinglish and Romanized Marathi typed in Latin script
- Voice notes with background noise
- Known-true claims (guard against a bot that calls everything false)
- Known-false claims with existing fact-checks
- Folk health beliefs → should land t3a
- Dosage/treatment questions → **must** land t3b
- Non-claims (greetings, jokes) → should short-circuit

A first, text-only slice of this lives at `tests/fixtures/tier_fixtures.json` (15 cases), run via `scripts/run_tier_fixtures.py` against the live classifier — not part of the pytest suite, since it costs real quota. At M5, all 15 passed, including every dosage/drug-interaction/urgent-symptom case landing t3b (with a Hinglish variant), both true and false T1 claims correctly distinguished, and both greetings short-circuiting. Extend this fixture file as image/audio input types land in M6/M7, per the full 40–60 target above.

### 14.2 What to measure

| Metric | Target |
|---|---|
| Tier classification accuracy | Highest priority. A t3b misrouted to t3a is the only genuinely dangerous failure in this system |
| OCR usable-text rate | On real degraded screenshots, not clean images — clean images fail to discriminate |
| Marathi transcription WER | Expect worse than Hindi. Decides whether ElevenLabs Scribe stays primary |
| Semantic cache precision | Manually review every hit. A wrong-claim hit serves a wrong verdict |
| End-to-end latency | Text < 8s, image < 15s, audio < 30s |

### 14.3 Adversarial pass
Try to make it fail: mixed languages mid-sentence, sarcasm, claims with a true premise and false conclusion, "asking for a friend" phrasing on a t3b question, prompt-injection text embedded in an image. Fix what breaks; document what doesn't.

---

## 15. Phase 13 — Deployment and going live

1. Deploy to your chosen host. Set all env vars in the dashboard, never in the repo.
2. Update the Meta webhook URL from the ngrok URL to the deployed URL. Re-subscribe to the `messages` field.
3. Enable the Supabase keep-alive cron.
4. **Also keep the app host itself warm, not just Supabase.** Free-tier web services (Render, Railway, Fly) spin down on idle and can take 20–50s to cold-start, which collides with the webhook's "ack in milliseconds" requirement and risks Meta treating repeated slow/failed acks as an unhealthy endpoint. Extend the keep-alive cron to also ping the app's own health endpoint, or budget for an always-on tier before inviting real users.
5. Add structured logging (a request ID per submission, carried through every stage) — debugging a multi-provider async pipeline without correlated logs is miserable.
6. **Only now**, if you want real users beyond the 5 test numbers: complete Meta Business Verification (business documents / ID), then add and verify a real phone number. The number must not have an active WhatsApp account on it.

---

## 16. MVP slicing

Do not build all of this before the first end-to-end run. Each milestone is demoable.

| Milestone | Scope | Done when |
|---|---|---|
| **M1 — Echo** | Webhook in, contextual reply out. No AI at all | You forward a message and get it quoted back |
| **M2 — Text T1** | Extract + classify + T1 verdict, English + Hindi | Forwarded text myth returns a correct verdict |
| **M3 — Cache** | Hash + embedding cache, `times_seen` | Second identical forward returns instantly |
| **M4 — T2 grounding** | Bilingual search, source links, structural confidence | A recent local rumor returns a cited verdict |
| **M5 — Safety tiers** | t3a soft path, t3b hard stop, full fixture pass | Every dosage question in the fixture set hits t3b |
| **M6 — Images** | Gemini vision + caption handling | A meme screenshot returns a verdict |
| **M7 — Audio/video** | ElevenLabs Scribe + fallback + ffmpeg extraction | A voice note returns a verdict |
| **M8 — Polish** | Reactions, trending endpoint, forward-count prioritization, rate limits | Dashboard shows top claims of the week |

M1–M3 is the smallest thing worth showing anyone. M5 is the point where it is safe to put in front of people who aren't you.

---

## 17. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| t3b misclassified as t3a | Medium | Highest-priority test case. Prompt the model to default to t3b when uncertain. Manual review of every t3a in the fixture set |
| Semantic cache serves wrong verdict | Medium | Conservative threshold, log every hit with similarity, review before raising the match rate |
| Gemini free quota exhausted mid-demo | Medium | Cache + Groq fallback. Warm the cache with known claims before any live demo |
| Marathi transcription too weak to be useful | Medium | Benchmark early in M7. If both ElevenLabs and Whisper underperform, ship Marathi text-only and say so |
| ElevenLabs cost scales with audio-forward volume | Medium | Metered by the minute, not free like the rest of the stack — cap audio duration (already planned), set spend alerts before any public push, and keep Whisper/self-hosted IndicConformer as a free floor when quota or budget runs out |
| Supabase project paused | Low | Keep-alive cron |
| Model confidently wrong on a T1 claim | Medium | Never present certainty you don't have; reactions surface it; consider forcing high-stakes-domain T1s into T2 |
| Prompt injection inside a forwarded image | Low | Treat extracted text strictly as data. Never let it alter instructions |
| Background job lost on process restart/redeploy | Medium | Stale-`pending` sweep job (§13.1a); no path should stay silent just because the process died mid-job |
| Free-tier host cold start delays webhook ack | Medium | Keep the app host warm too, not just Supabase (§15); budget for always-on before public launch |
| Combined free-tier quota (Gemini+Groq+OpenRouter) insufficient for a distinct-claim spike | Medium | Size against plausible load before launch; degrade gracefully with a "high volume, please retry" reply (§13.2) |
| Compliance/liability exposure from retained health-adjacent user data | Medium | Define retention/deletion policy, visible disclaimer, track T3b bypass as a launch blocker (§13.5) |

---

## Appendix — environment variables

```
WA_PHONE_NUMBER_ID=
WA_TOKEN=
WA_VERIFY_TOKEN=
WA_APP_SECRET=
WA_API_VERSION=v21.0

GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_EMBED_MODEL=gemini-embedding-001

ELEVENLABS_API_KEY=

TAVILY_API_KEY=

GROQ_API_KEY=
OPENROUTER_API_KEY=
OPENAI_API_KEY=

SUPABASE_URL=
SUPABASE_SERVICE_KEY=

PHONE_HASH_SALT=
SEMANTIC_MATCH_THRESHOLD=0.90
MAX_AUDIO_SECONDS=180
RATE_LIMIT_PER_HOUR=20
```
