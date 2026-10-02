# InfoBot: Architecture and Workflow Map

Derived from the source in `app/`, `db/migrations/`, `.env.example`, `requirements.txt`, the live Supabase schema (read through the Supabase MCP on 2026-10-01) and the repo docs. The code is the source of truth. `docs/` and the original plan were used only where cross-checked against code. Anything I could not prove is marked **⚠️ UNVERIFIED**.

Snapshot: branch `master`, with a large uncommitted change set (see §10). Diagrams are Mermaid; paste them into any Mermaid renderer.

---

## 1. Project summary

InfoBot is a WhatsApp fact-checking bot for India. A user forwards text, a screenshot, a voice note or a short video to a Meta Cloud API number. The bot turns the media into labelled text, screens it for prompt injection, looks for a cached answer, extracts up to 3 checkable claims, verifies each by tier (model knowledge, web search, soft health guidance or a fixed medical hard stop), and replies as a contextual reply in English, Hindi or Marathi with a verdict, a confidence label and sources.

There is **no frontend application**. The only "client" is WhatsApp. `site/` is a static landing, privacy and data-deletion site.

## 2. Technology stack

```text
Frontend:        none (WhatsApp is the UI). Static site in site/ (HTML + CSS)
Backend:         Python 3.10 (per CLAUDE.md), FastAPI, uvicorn, httpx, pydantic-settings
Database:        Supabase Postgres + pgvector, reached over REST/PostgREST with the service key (no ORM)
Authentication:  none for users. Meta HMAC-SHA256 signature on the webhook; GET verify token; API keys in headers
External APIs:   Meta WhatsApp Graph API, Gemini (Interactions + embeddings), Groq (LLM fallback, Prompt Guard,
                 Whisper fallback), Tavily (search), ElevenLabs Scribe (speech to text)
Media tooling:   ffmpeg and ffprobe run as local subprocesses
Infrastructure:  laptop + ngrok tunnel today; no Dockerfile, CI, or deploy config in the repo
Deployment:      none for the bot. site/ is stated in docs to be on Netlify (no netlify.toml in repo)
```

## 3. Component map

| Component | Location | Responsibility | Depends on |
|---|---|---|---|
| App + routes + handler | `app/main.py` | `/health`, `/trending`, `GET/POST /webhook`; per-message handler `handle_message`; media handlers; failure replies; startup sweep | everything below |
| Settings | `app/config.py` | env-driven settings (pydantic-settings, `.env`) | none |
| Signature check | `app/whatsapp/verify.py` | HMAC-SHA256 over the raw body vs `X-Hub-Signature-256` | config |
| Parser | `app/whatsapp/parser.py` | flatten Meta payload to `InboundMessage` (text, image, audio, video, reaction, interactive) | none |
| WhatsApp client | `app/whatsapp/client.py` | send text reply, reply buttons, mark read, two-step media download | Meta Graph API |
| Language commands | `app/pipeline/language.py` | exact-match language menu / choice commands and button ids | none |
| Guard | `app/pipeline/guard.py` | strip hidden chars, regex injection rules, Groq Prompt Guard 2, `wrap_untrusted`, `sanitize_output`, `guess_language`, size caps | Groq |
| Normalize | `app/pipeline/normalize.py` | image to labelled text (Gemini vision), audio/video to transcript (ffmpeg, Scribe, Whisper) | Gemini, ElevenLabs, Groq, ffmpeg |
| Orchestrator | `app/pipeline/orchestrator.py` | text pipeline: guard, exact cache, classify, per-claim cache and verify, compose | pipeline modules, db.cache |
| Classify | `app/pipeline/classify.py` + `prompts/extract_classify.txt` | one Gemini call: input kind, up to 3 claims with tier, language, domain, search queries | Gemini |
| Verify | `app/pipeline/verify.py` + `prompts/verify_t*.txt` | T1, T2, T3a verification, structural confidence, `translate_texts` | Gemini, Tavily |
| Compose | `app/pipeline/compose.py`, `messages.py`, `confidence.py` | build reply cards (verdict card, multi-claim, non-claim, static en/hi/mr text, confidence buckets) | prompts, messages |
| Gemini provider | `app/providers/gemini.py` | `generate_json`, `embed_text`, key rotation, cooldowns, concurrency cap 4 | Gemini API, fallback_llm |
| Groq fallback | `app/providers/fallback_llm.py` | text-only JSON generation when Gemini fails | Groq |
| Tavily provider | `app/providers/tavily.py` | bilingual claim search plus one fact-check-domain pass | Tavily |
| Scribe | `app/providers/elevenlabs.py` | primary speech to text | ElevenLabs |
| Whisper | `app/providers/whisper.py` | fallback speech to text (Groq, else OpenAI) | Groq / OpenAI |
| DB client | `app/db/client.py` | thin PostgREST wrapper (get / post / patch) | Supabase |
| Cache | `app/db/cache.py` | hash, exact lookup, semantic lookup (`rpc/match_claims`), `times_seen`, insert | DB client |
| Submissions | `app/db/submissions.py` | idempotency claim, status marks, reply wamid link, stale sweep | DB client |
| Rate limit | `app/db/rate_limit.py` | `rpc/increment_rate_limit`, hourly window | DB client |
| Prefs | `app/db/prefs.py` | per-user reply language; fails soft | DB client |
| Feedback / Trending | `app/db/feedback.py`, `app/db/trending.py` | reaction rows; top claims by `times_seen` | DB client |
| Util | `app/util.py` | salted hashes of phone and wamid, `ref()` log id | config |
| Scripts / tests | `scripts/`, `tests/`, `claims_test/` | live fixture and scenario runners; offline unit tests; manual test messages | app |
| Static site | `site/` | landing, privacy, data deletion | none |

## 4. Master workflow

```mermaid
flowchart TD
    USER["User on WhatsApp<br/>text / image / voice / video / reaction / button"]
    META["Meta WhatsApp Cloud API"]
    TUNNEL["ngrok tunnel<br/>(per docs)"]
    WH["POST /webhook<br/>app/main.py"]
    SIG{"HMAC signature valid?<br/>whatsapp/verify.py"}
    REJ["403, dropped"]
    ACK["200 OK immediately"]
    BG["BackgroundTask: handle_message<br/>app/main.py"]

    USER --> META --> TUNNEL --> WH --> SIG
    SIG -- no --> REJ
    SIG -- yes --> ACK
    SIG -- yes --> BG

    BG --> REACT{"Reaction?"}
    REACT -- yes --> FB[("feedback<br/>db/feedback.py")]
    REACT -- no --> IDEM{"New wamid?<br/>db/submissions.py<br/>unique wa_message_id"}
    IDEM -- duplicate --> DROP["drop"]
    IDEM -- new --> PREF["read user_prefs<br/>db/prefs.py"]
    PREF --> LANGQ{"Language command<br/>or button?<br/>pipeline/language.py"}
    LANGQ -- yes --> LANGSET["set language / show buttons<br/>no model call"]
    LANGQ -- no --> RL{"Under hourly limit?<br/>rpc/increment_rate_limit"}
    RL -- no --> RLMSG["rate-limited reply"]
    RL -- yes --> READ["mark_read (best effort)"]
    READ --> KIND{"Message type"}

    KIND -- text --> TXT["text"]
    KIND -- image --> IMG["download, Gemini vision<br/>normalize.normalize_image"]
    KIND -- "audio / video" --> AV["download, ffmpeg, Scribe or Whisper<br/>normalize.normalize_audio"]
    KIND -- other --> UNSUP["unsupported-type reply"]

    IMG --> LBL["labelled text"]
    AV --> LBL
    TXT --> PIPE
    LBL --> PIPE

    subgraph PIPE["orchestrator.run_text_pipeline"]
        direction TB
        G["guard.screen_text"] --> GB{"blocked?"}
        GB -- yes --> BLK["fixed refusal"]
        GB -- no --> EX{"exact cache hit<br/>whole-message hash?"}
        EX -- yes --> CH["cached reply<br/>translated if needed"]
        EX -- no --> CL["classify.extract_and_classify<br/>1 Gemini call"]
        CL --> NC{"verifiable claims?"}
        NC -- no --> NCR["non-claim reply"]
        NC -- yes --> PC["per claim, in parallel:<br/>embed, exact then semantic cache,<br/>else verify by tier"]
        PC --> COMP["compose_claims_reply"]
    end

    PIPE --> SEND["send_text_reply<br/>contextual reply"]
    SEND --> WRITE[("claims cache write<br/>only after send succeeds")]
    WRITE --> DONE["mark submission done<br/>offer language buttons once"]
    SEND --> META --> USER

    PC -. "T2" .-> TAV(["Tavily"])
    PC -. "T1/T2/T3a/translate" .-> GEM(["Gemini"])
    GEM -. "fails" .-> GRQ(["Groq fallback"])
    G -. "ML screen" .-> GRQ
    EX -.-> DB[("Supabase")]
    PC -.-> DB

    BG -. "any exception" .-> ERR["mark submission error<br/>apology reply: busy or generic"]
```

## 5. Detailed workflows

### 5.1 Application startup

```mermaid
flowchart LR
    S["uvicorn app.main:app<br/>(no --reload per CLAUDE.md)"] --> C["import app.config<br/>Settings() reads .env"]
    C --> P["import modules<br/>prompts/*.txt read at import<br/>in classify.py and verify.py"]
    P --> R["routes registered:<br/>/health /trending /webhook GET POST"]
    R --> L["lifespan startup:<br/>db_submissions.abandon_stale_pending()<br/>pending older than 10 min becomes abandoned"]
    L --> F{"sweep failed?"}
    F -- yes --> LOG["log exception, continue"]
    F -- no --> UP["serving"]
    LOG --> UP
```

There is no startup DB connection: each DB call opens its own `httpx` client (`app/db/client.py`). Media tools are not checked at startup.

### 5.2 Webhook and per-message handler

```mermaid
sequenceDiagram
    autonumber
    participant M as Meta Cloud API
    participant W as POST /webhook (main.py)
    participant V as valid_signature
    participant H as handle_message (background)
    participant S as Supabase
    participant WA as WhatsApp client

    M->>W: webhook payload + X-Hub-Signature-256
    W->>V: raw body, header
    alt invalid
        W-->>M: 403
    else valid
        W->>W: extract_messages(payload)
        W->>H: bg.add_task per message
        W-->>M: 200 immediately
    end
    H->>S: claim_submission (insert, on_conflict ignore)
    alt duplicate wamid
        H-->>H: drop
    else new
        H->>S: user_prefs get
        H->>S: rpc increment_rate_limit
        H->>WA: mark_read (failure tolerated)
        H->>H: _compose_reply (pipeline)
        H->>WA: send_text_reply (context.message_id)
        WA->>M: Graph API messages
        H->>S: insert claims rows (after send)
        H->>S: set reply_wamid, mark done
        opt first contact
            H->>WA: send_reply_buttons (language)
        end
    end
```

### 5.3 Message handler decisions

```mermaid
flowchart TD
    A["handle_message(msg)"] --> B{"is_reaction?"}
    B -- yes --> R["_handle_reaction:<br/>find submission by reply_wamid,<br/>insert feedback"]
    B -- no --> C["hash_phone"]
    C --> D{"claim_submission<br/>is_new?"}
    D -- no --> X["return"]
    D -- yes --> E["prefs.get → reply_lang"]
    E --> F{"interactive button<br/>or language command?"}
    F -- "button, not a language one" --> G["mark done, return"]
    F -- yes --> H["_handle_language_request<br/>menu or set_language"]
    F -- no --> I{"rate limit ok?"}
    I -- no --> J["RATE_LIMITED reply<br/>status rate_limited"]
    I -- yes --> K["mark_read"]
    K --> L["_compose_reply"]
    L --> M["send_text_reply"]
    M --> N["insert pending_claim_writes<br/>each in try/except"]
    N --> O["set_reply_wamid, mark done"]
    O --> P{"never prompted for language?"}
    P -- yes --> Q["send language buttons"]
    A -. "exception anywhere" .-> Z["mark error, send apology:<br/>GeminiError/FallbackError → compose_busy<br/>else GENERIC_ERROR"]
```

### 5.4 Media to text

```mermaid
flowchart TD
    subgraph IMAGE["image (main._compose_image_reply)"]
        I1["download_media: Graph GET url, then GET file"] --> I2{"over 6 MB?"}
        I2 -- yes --> I2R["unreadable-media reply"]
        I2 -- no --> I3["normalize_image: one Gemini vision call<br/>schema: readable text, description, kind"]
        I3 -- "GeminiError" --> I3A{"caption?"}
        I3A -- yes --> I3B["check caption as text"]
        I3A -- no --> I3C["busy reply (no vision fallback)"]
        I3 --> I4{"text found?"}
        I4 -- yes --> I5["labels: Text inside the image,<br/>Caption, What the image shows"]
        I4 -- no --> I6{"photo with description?"}
        I6 -- yes --> I7["photo-only reply:<br/>cannot judge authenticity"]
        I6 -- no --> I8["unreadable reply"]
    end

    subgraph AUDIO["audio / video (main._compose_audio_or_video_reply)"]
        A1["download_media"] --> A2{"over 17 MB?"}
        A2 -- yes --> A2R["too-long reply"]
        A2 -- no --> A3{"video?"}
        A3 -- yes --> A4["ffmpeg: audio only, 16 kHz mono WAV,<br/>first 185 s"]
        A3 -- no --> A5["audio bytes as-is"]
        A4 --> A6["ffprobe duration"]
        A5 --> A6
        A6 --> A7{"over 180 s?"}
        A7 -- yes --> A7R["too-long reply (before any paid call)"]
        A7 -- no --> A8["ElevenLabs Scribe"]
        A8 -- "fails" --> A9["Groq Whisper<br/>(else OpenAI)"]
        A8 -- "empty result" --> A10["empty: no speech"]
        A9 -- "fails" --> A11["None: busy reply"]
        A8 --> A12["clean_transcript<br/>strip sound labels"]
        A9 --> A12
        A12 --> A13{"speech left?"}
        A13 -- yes --> A14["label: Voice note / Video transcript<br/>+ Caption"]
        A13 -- no --> A15["caption only, else unreadable"]
    end
```

ffmpeg and ffprobe run through `asyncio.to_thread(subprocess.run, ...)` (see `normalize._run`).

### 5.5 Text pipeline

```mermaid
flowchart TD
    T["normalize_text (identity)"] --> G["guard.screen_text"]
    G --> G1["clean_text: strip invisible and control chars"]
    G1 --> G2{"hidden tag chars?"}
    G2 -- yes --> BLOCK
    G2 -- no --> G3{"regex injection patterns?<br/>en + hi/mr"}
    G3 -- yes --> BLOCK
    G3 -- no --> G4["Groq Prompt Guard 2 per 700-char chunk,<br/>max 6, label lines removed"]
    G4 --> G5{"score"}
    G5 -- ">= 0.5" --> BLOCK["blocked: fixed refusal, nothing cached"]
    G5 -- "0.05 to 0.5" --> SUS["suspicious: continue, never cache"]
    G5 -- "< 0.05, or ML call failed (fails open)" --> OK["clean"]
    SUS --> H
    OK --> H["hash_claim(whole text)"]
    H --> E{"claims.claim_hash match?"}
    E -- yes --> EH["increment_seen; translate headline +<br/>explanation if reply language is not en;<br/>compose_cached_reply"]
    E -- no --> C["extract_and_classify<br/>Gemini, schema-constrained"]
    C --> K{"claims found?"}
    K -- no --> NCR["compose_nonclaim_reply"]
    K -- yes --> PAR["asyncio.gather over claims (max 3)"]
    PAR --> RES["_resolve_claim per claim"]
    RES --> EMB["embed claim_english (Gemini)"]
    EMB --> CL["_cached_outcome: exact (single claim uses message hash,<br/>multi uses per-claim hash), then semantic >= 0.90"]
    CL -- hit --> OFR["_outcome_from_row<br/>translate explanation if needed"]
    CL -- miss --> VER["_verify_claim by tier"]
    VER --> W{"cacheable and verdict not<br/>unverifiable / error?"}
    W -- yes --> PW["attach pending write<br/>(written after send)"]
    OFR --> OUT
    PW --> OUT["ClaimOutcome"]
    W -- no --> OUT
    OUT --> ALLERR{"all claims errored?"}
    ALLERR -- yes --> RAISE["raise: caller apologises"]
    ALLERR -- no --> COMP["compose_claims_reply"]
```

### 5.6 Verification tiers

```mermaid
flowchart TD
    V["_verify_claim"] --> TIER{"claim.tier"}

    TIER -- "t1" --> T1["verify_t1: Gemini, model knowledge only"]
    T1 --> T1C["confidence set structurally:<br/>60 if model_is_confident else 25,<br/>None if unverifiable"]

    TIER -- "t2" --> T2S["search_for_claim (Tavily)"]
    T2S --> S1["search 1: English query<br/>(social sites excluded)"]
    S1 --> S2["search 2: original-language query<br/>if not English"]
    S2 --> S3["search 3: fact-check and health-authority domains"]
    S3 --> NR{"any results?"}
    NR -- no --> NS["fixed 'no reliable sources' reply,<br/>verdict unverifiable"]
    NR -- yes --> T2G["Gemini synthesis over retrieved results<br/>(wrapped as untrusted data)"]
    T2G --> CITE["keep only cited URLs that Tavily returned;<br/>titles come from retrieval"]
    CITE --> NU{"any valid source?"}
    NU -- no --> NS
    NU -- yes --> T2C["confidence from distinct domains:<br/>both sides 20, two or more agree 85,<br/>one agrees 55, only contradicting 20"]

    TIER -- "t3a" --> T3["verify_t3a: general health guidance"]
    T3 --> ESC{"needs_escalation?"}
    ESC -- yes --> HS
    ESC -- no --> GD["verdict 'guidance', no confidence"]

    TIER -- "t3b" --> HS["'refused': static hard stop,<br/>helpline 104, never model-written"]

    CLS["classify.py adjustments:<br/>t3a outside health becomes t2;<br/>frequently forwarded t1 becomes t2"] -.-> TIER
```

### 5.7 Language preference

```mermaid
flowchart LR
    M["incoming text"] --> P["language.parse_command:<br/>whole message, at most 40 chars, exact match"]
    B["button reply lang_en / lang_hi / lang_mr"] --> FB["language.from_button"]
    P --> A{"action"}
    FB --> A
    A -- "menu" --> BTN["send_reply_buttons"] --> MP["prefs.mark_prompted"]
    A -- "en / hi / mr" --> SET["prefs.set_language (upsert merge)"] --> CONF["confirmation in that language,<br/>or BUSY if the save failed"]
    A -- none --> PIPELINE["normal pipeline, reply_lang from user_prefs<br/>else language of the claim"]
```

### 5.8 Reaction (feedback) flow

```mermaid
flowchart LR
    R["reaction webhook"] --> P["parser: reaction_emoji, target wamid"]
    P --> Q["find_submission_id_by_reply_wamid<br/>(hash of reply wamid)"]
    Q --> F{"found?"}
    F -- yes --> I[("insert feedback row")]
    F -- no --> L["log: reaction on something else, ignore"]
```

Reactions return before the idempotency insert (see §10, finding 3).

### 5.9 Error and fallback paths

```mermaid
flowchart TD
    subgraph LLM["LLM call: gemini.generate_json"]
        G1["POST Interactions API<br/>x-goog-api-key header"] --> G2{"status"}
        G2 -- "429" --> G3["cool down key (30 s, or 10 min if daily),<br/>try GEMINI_API_KEY_FALLBACK"]
        G2 -- "other 4xx/5xx" --> G4["retry (text: 2 attempts, image: 3)"]
        G2 -- "ok, bad JSON" --> G4
        G4 -- "exhausted, text prompt" --> G5["Groq generate_json<br/>strict schema, then plain JSON mode"]
        G4 -- "exhausted, image" --> G6["GeminiError"]
        G5 -- "fails" --> G7["GeminiError (both failed)"]
    end

    G6 --> U["caller"]
    G7 --> U
    U --> UV{"where"}
    UV -- "image with caption" --> UC["check caption as text"]
    UV -- "embedding" --> UE["skip semantic cache, continue"]
    UV -- "translation" --> UT["reply in English"]
    UV -- "claim verification" --> UX["claim marked 'error'"]
    UX --> UA{"all claims errored?"}
    UA -- yes --> UAP["handle_message except:<br/>status error + compose_busy apology"]
    UA -- no --> UP["show the claims that worked"]

    subgraph OTHER["Other degradations"]
        O1["Prompt Guard ML fails: fail open,<br/>regex still applies"]
        O2["mark_read fails: ignored"]
        O3["cache write fails: logged, reply already sent"]
        O4["prefs read or write fails: defaults, no error"]
        O5["Scribe fails: Whisper; both fail: busy reply"]
        O6["no Tavily results: fixed unverifiable reply"]
    end
```

## 6. Data flow

```mermaid
flowchart LR
    subgraph IN["Request data (untrusted)"]
        RAW["raw webhook body"]
        MEDIA["image / audio / video bytes<br/>temporary, memory and temp dir"]
        TXT["message text, caption"]
    end
    subgraph XFORM["Transformation"]
        LBL["labelled text"]
        CLEAN["guard: cleaned, length-capped text"]
        CLM["Claim objects:<br/>claim_original, claim_english, tier,<br/>language, domain, search queries"]
        EMB["768-dim embedding"]
        HASH["sha256 of normalised text"]
    end
    subgraph EXT["External data"]
        SRCH["Tavily results: url, title, content"]
        LLMO["model JSON output"]
    end
    subgraph PERSIST["Persistent (Supabase)"]
        CLAIMS[("claims:<br/>English text, hash, embedding,<br/>tier, verdict, confidence,<br/>explanation_en, sources, times_seen")]
        SUBS[("submissions:<br/>hashed wamid, hashed phone,<br/>status, cache_hit, reply_wamid")]
        FBK[("feedback: emoji per submission")]
        RLM[("rate_limits: hashed user, hour, count")]
        UPF[("user_prefs: hashed user, language")]
    end
    RESP["Response: one text reply,<br/>sanitised, links only from retrieved sources"]

    RAW --> TXT
    MEDIA --> LBL
    TXT --> LBL --> CLEAN --> HASH
    CLEAN --> CLM
    CLM --> EMB
    CLM --> SRCH
    SRCH --> LLMO
    CLM --> LLMO
    LLMO --> CLAIMS
    HASH --> CLAIMS
    EMB --> CLAIMS
    LLMO --> RESP
    CLAIMS --> RESP
    RAW --> SUBS
    RAW --> RLM
    RAW --> FBK
    RAW --> UPF
```

| Data | Kind | Notes |
|---|---|---|
| Phone number | request, persistent only as hash | `hash_phone` (salted sha256) stored as `wa_user_hash`. Raw number is used only to send the reply |
| wamid | request, persistent only as hash | `hash_id` (different prefix). The raw wamid embeds the number in base64, which is why it is hashed. Logs use `ref()` |
| Message text and media | request, temporary | Never stored. Only the English claim text of cached claims is stored |
| Claims cache | persistent | English only. A cached answer for another language is translated on read (`translate_texts`) |
| Model output | external, shown to user | passes through `sanitize_output` (no links, phone numbers, control chars). Only Tavily-returned URLs reach the user |
| Config and secrets | config | `.env` (git-ignored). Keys go in headers, never URLs |

## 7. External integrations

Verified by calls in the current code.

| Service | Used by | Purpose | Direction | Credential |
|---|---|---|---|---|
| Meta Graph API | `whatsapp/client.py` | send reply, buttons, read receipt, resolve and download media | out | `WA_TOKEN` bearer, `WA_PHONE_NUMBER_ID`, `WA_API_VERSION` |
| Meta webhook | `main.py`, `whatsapp/verify.py` | inbound messages, reactions, buttons | **in** | `WA_APP_SECRET` (HMAC), `WA_VERIFY_TOKEN` (GET handshake) |
| Gemini Interactions API | `providers/gemini.py` | classify, verify, translate, image reading | out | `x-goog-api-key`: `GEMINI_API_KEY`, then `GEMINI_API_KEY_FALLBACK`; model in `GEMINI_MODEL` |
| Gemini embeddings | `providers/gemini.py` | 768-dim claim embeddings | out | same keys; `GEMINI_EMBED_MODEL` |
| Groq chat | `providers/fallback_llm.py` | text fallback, model `GROQ_MODEL` | out | `GROQ_API_KEY` bearer |
| Groq Prompt Guard | `pipeline/guard.py` | injection score | out | same key; `PROMPT_GUARD_MODEL` |
| Groq Whisper (else OpenAI) | `providers/whisper.py` | transcription fallback | out | `GROQ_API_KEY`, else `OPENAI_API_KEY` |
| Tavily | `providers/tavily.py` | web search for T2 | out | `TAVILY_API_KEY` bearer |
| ElevenLabs Scribe | `providers/elevenlabs.py` | primary transcription | out | `xi-api-key` |
| Supabase REST | `db/*.py` | all persistence | out | `SUPABASE_SERVICE_KEY` (apikey + bearer) |

```mermaid
sequenceDiagram
    participant O as orchestrator / verify
    participant G as gemini.py
    participant GM as Gemini API
    participant GQ as Groq
    participant T as Tavily
    participant DB as Supabase

    O->>DB: GET claims by claim_hash
    O->>G: embed_text
    G->>GM: embedContent (key 1, then key 2 on 429)
    O->>DB: POST rpc/match_claims
    O->>G: generate_json (classify)
    G->>GM: interactions
    alt Gemini exhausted
        G->>GQ: chat completions, strict JSON schema
    end
    O->>T: POST search (basic depth, up to 3 per claim)
    T-->>O: results
    O->>G: generate_json (T2 synthesis)
    O-->>O: compose reply
```

**Configured but not used by the code:** `OPENROUTER_API_KEY` (defined in `config.py`, `.env.example`, referenced nowhere else).

## 8. Database flow

Live schema, read from Supabase on 2026-10-01: five tables in `public`, all with RLS enabled. **Only `user_prefs` has a migration in the repo** (`db/migrations/001_user_prefs.sql`); the other four were applied directly.

```mermaid
erDiagram
    claims ||--o{ submissions : "claim_id (FK, never written by code)"
    submissions ||--o{ feedback : "submission_id (FK)"

    claims {
        uuid id PK
        text claim_text_en
        text claim_hash UK
        vector embedding
        text tier
        text verdict
        smallint confidence
        text explanation_en
        jsonb sources
        int times_seen
        timestamptz first_seen_at
        timestamptz last_seen_at
    }
    submissions {
        uuid id PK
        text wa_message_id UK "hashed wamid"
        text wa_user_hash
        uuid claim_id FK
        text input_type
        text detected_lang "never written"
        bool was_forwarded
        bool frequently_fwd
        text cache_hit
        int latency_ms "never written"
        text status
        text error
        timestamptz created_at
        text reply_wamid "hashed"
    }
    feedback {
        uuid id PK
        uuid submission_id FK
        text reply_message_id "hashed"
        text emoji
        timestamptz created_at
    }
    rate_limits {
        text wa_user_hash PK
        timestamptz window_start PK
        int count
    }
    user_prefs {
        text wa_user_hash PK
        text language "en hi mr"
        bool prompted
        timestamptz updated_at
    }
```

`rate_limits` and `user_prefs` have no foreign keys; they join to `submissions` only by the shared `wa_user_hash` value.

| Operation | Code | Table / function |
|---|---|---|
| Idempotency insert | `submissions.claim_submission` | `submissions`, `on_conflict=wa_message_id`, `ignore-duplicates` |
| Status update | `mark_submission`, `set_reply_wamid`, `abandon_stale_pending` | `submissions` (PATCH) |
| Exact cache | `cache.lookup_exact` | `claims` by `claim_hash` |
| Semantic cache | `cache.lookup_semantic` | RPC `match_claims` (threshold `SEMANTIC_MATCH_THRESHOLD`, 0.90) |
| Popularity | `cache.increment_seen` (read then patch, non-atomic) | `claims.times_seen`, `last_seen_at` |
| Cache write | `cache.insert_claim` | `claims` (after a successful send only) |
| Rate limit | `rate_limit.check_and_increment` | RPC `increment_rate_limit`, window = current UTC hour, cap `RATE_LIMIT_PER_HOUR` (20) |
| Language | `prefs.get/set_language/mark_prompted` | `user_prefs` |
| Feedback | `feedback.insert_feedback` | `feedback` |
| Trending | `trending.get_trending` | `claims` ordered by `times_seen` over 7 days, exposed at `GET /trending` |

**⚠️ UNVERIFIED:** the bodies of `match_claims` and `increment_rate_limit` as deployed (I saw tables, not functions; the plan §5.1 gives `match_claims`). Also the HNSW index and any RLS policies (RLS is on; `user_prefs` documents "no policies").

## 9. Infrastructure flow

```mermaid
flowchart LR
    DEV["Developer (Windows laptop)"] --> RUN["uvicorn app.main:app :8000<br/>.venv, no --reload"]
    RUN --> NG["ngrok http 8000<br/>(per CLAUDE.md and docs)"]
    NG --> META["Meta Cloud API webhook URL"]
    RUN --> FF["local ffmpeg / ffprobe"]
    RUN --> SB[("Supabase (hosted)")]
    RUN --> EXT(["Gemini, Groq, Tavily, ElevenLabs"])
    RUN --> GRAPH(["Meta Graph API"])

    DEV --> TESTS["pytest (offline)<br/>run_tier_fixtures.py, run_scenarios.py (live, spend quota)"]
    DEV --> SITE["site/ static files"] -. "deploy per docs" .-> NET["Netlify<br/>infobot-wsp.netlify.app"]
```

| Item | State |
|---|---|
| Container | none: no Dockerfile or compose file |
| CI/CD | none: no `.github/`, no pipeline config |
| Dependencies | `requirements.txt`: fastapi, uvicorn[standard], httpx, python-dotenv, pydantic, pydantic-settings. **Unpinned.** `requirements-dev.txt` exists (not opened) |
| System dependency | ffmpeg and ffprobe must be on PATH. Nothing in the repo declares this |
| Ports / volumes / networks | only `--port 8000`. No volumes; temp files use `tempfile.TemporaryDirectory` |
| Production host | none (docs: "no production host yet") |
| Netlify | **⚠️ UNVERIFIED** from the repo: docs say `site/` is deployed there, but no `netlify.toml` or deploy config exists here |
| ngrok | **⚠️ UNVERIFIED** as running: documented in CLAUDE.md and docs only |

## 10. Important findings

**Architecture patterns**
- Webhook returns 200 at once and does all work in a FastAPI `BackgroundTask`. No queue.
- Linear pipeline with hard stages: guard, cache, classify, tiered verify, compose. Untrusted text is wrapped and all output is sanitised.
- Confidence is computed from structure (T1 pinned at 60/25, T2 from distinct agreeing domains), never self-reported by the model.
- Cache writes happen only after a successful send; nothing suspicious, blocked, unverifiable or errored is cached.
- Every optional layer fails soft (prefs, embeddings, translation, Prompt Guard, read receipt).

**Critical dependencies and single points of failure**
- Supabase: `claim_submission` is the first call for every non-reaction message. If Supabase is down, every message ends in the apology path.
- Gemini is the only vision provider, so there is no image fallback. Text falls back to Groq.
- One Groq key serves three jobs (Prompt Guard, LLM fallback, Whisper fallback), so they share one rate limit.
- A single uvicorn process on a laptop: in-flight jobs are lost on restart. The startup sweep only marks stale rows `abandoned`; it cannot retry or notify the user.
- Key cooldown state (`_cooldown_until`) lives in process memory.

**Potential issues found in the code**
1. **Reactions skip idempotency.** `handle_message` returns from `_handle_reaction` before `claim_submission`, so a redelivered reaction webhook inserts a duplicate `feedback` row.
2. **Embedding before exact lookup.** `_resolve_claim` calls Gemini embeddings before `_cached_outcome`, so a claim that would hit the exact cache still costs an embed call (single-claim exact whole-message hits are caught earlier in `run_text_pipeline`; multi-claim exact hits are not).
3. **Sequential Tavily calls.** `search_for_claim` awaits up to three searches one after another per T2 claim, which adds latency.
4. **`increment_seen` is read-then-write** (2 round trips, non-atomic). Documented in its docstring as acceptable.
5. **Dead config and columns.** `OPENROUTER_API_KEY`, `submissions.detected_lang`, `submissions.latency_ms` and the `submissions.claim_id` link are never written by the code. The `feedback` table has 0 rows.
6. **`GET /trending` is unauthenticated** and returns cached claim text and verdicts. The plan left a public dashboard as an open decision, so this may be intentional; flagging it only.
7. **Sensitive-data disclosure gap** is already tracked in `docs/TASKS.md` (privacy page omits Groq).

**Documentation vs code**
- `docs/PROJECT_CONTEXT.md` lists "Cache hits answer in English only" under known limitations, but `orchestrator.py` translates cached explanations via `translate_texts`. The doc looks stale.
- `docs/PROJECT_CONTEXT.md` says no migration files are in the repo; `db/migrations/001_user_prefs.sql` now exists (for `user_prefs` only).
- `CLAUDE.md` says Python 3.10; the original plan assumed 3.11+.
- The plan assumed free-tier hosting; the code is currently run on a laptop with no host chosen.

**State of the repo**
- `git status` shows many modified tracked files and untracked files (`app/db/prefs.py`, `app/pipeline/language.py`, `tests/test_language.py`, `db/`, the new docs). Commit and push are waiting on the owner per `docs/TASKS.md`.
- 12 test files under `tests/` (offline). Live runners spend API quota.

---

*Verification method:* every arrow in the diagrams was traced to a function call, route, config value or DB call in the files cited; the database section was also checked against the live Supabase schema. Items that rely only on docs or the plan are marked ⚠️ UNVERIFIED.
