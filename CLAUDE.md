# InfoBot: CLAUDE.md

Permanent project rules. Temporary progress does not belong here: it goes in `docs/PROGRESS.md`.

## Start of every session

Do not assume your conversation has the whole picture; other sessions may have changed things. Read, in this order, only as far as the task needs:

1. This file
2. `docs/PROJECT_CONTEXT.md` (what the system is and how it fits together)
3. `docs/TASKS.md` (what is in progress, so you do not collide with it)
4. `docs/PROGRESS.md` (what was done and what to know)
5. `docs/DECISIONS.md` (why things are the way they are; do not undo without reason)
6. `git status` and `git log --oneline -10`, then the source files you will touch

The filesystem and git are the source of truth for code. `docs/` is the source of truth for things code cannot say (decisions, open work, gotchas). Conversation history is neither.

## What this is

InfoBot is a WhatsApp fact-checking bot for India. Users forward a message, screenshot, voice note or short video; it replies with a verdict, a confidence label and sources, in English, Hindi or Marathi. The original plan is `whatsapp-verification-bot-implementation-plan.md` (milestones M1-M8 are built; it is background, not current truth: where it disagrees with `docs/`, `docs/` wins).

## Stack

Python 3.10, FastAPI + uvicorn, httpx, pydantic-settings. Supabase Postgres with pgvector, reached through its REST API (no ORM). Gemini (Interactions API), Groq, Tavily, ElevenLabs Scribe, ffmpeg. Static public site in `site/` (Netlify). Model names and fallbacks per task are in `docs/PROJECT_CONTEXT.md`.

## Layout

```
app/main.py            webhook, per-message handler, media handlers, failure replies
app/config.py          all settings (env-driven); .env.example lists every variable
app/pipeline/          guard.py (injection screen) -> classify.py -> verify.py -> compose.py;
                       orchestrator.py ties them; normalize.py (media->text); messages.py (static en/hi/mr text);
                       language.py (recognising a language request)
app/prompts/           every LLM prompt and the static medical hard-stop text
app/providers/         gemini.py (key rotation), fallback_llm.py (Groq), tavily.py, elevenlabs.py, whisper.py
app/db/                claims cache, submissions, rate limit, feedback, trending, prefs (reply language)
db/migrations/         SQL for schema changes made after the first setup (apply via Supabase, then keep the file)
app/whatsapp/          inbound parser, Graph API client, signature check
scripts/               live runners: run_tier_fixtures.py, run_scenarios.py (+ scenarios.py), asset/claims generators
tests/                 unit tests (no network), fixtures, scenarios/assets (git-ignored)
claims_test/           ten ready-to-send manual test messages (see its README.md)
site/                  public landing page, privacy policy, data-deletion page
Dockerfile, deploy/    production image, compose + Caddy, rollback script, CloudFormation, runbook (deploy/README.md)
.github/workflows/     ci.yml (tests + image build), deploy.yml (push to main -> ECR -> EC2 via SSM)
```

## Commands

Windows; use the venv interpreter.

```
.venv/Scripts/python -m pytest -q                                 # unit tests, offline (~10 s)
.venv/Scripts/python scripts/run_tier_fixtures.py                 # 15 live safety fixtures; MUST be 15/15 after any change to extract_classify.txt
.venv/Scripts/python scripts/run_scenarios.py [ID-prefixes] [--show]   # ~91 live scenarios -> tests/scenarios/last_run.md
.venv/Scripts/python scripts/make_scenario_assets.py              # builds the scenario media (needs ElevenLabs)
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # the server
ngrok http 8000                                                   # public tunnel for Meta's webhook (local dev only)
.venv/Scripts/python scripts/make_app_env.py                      # builds deploy/app.env (only the settings the app reads) for the server
docker build -t infobot:test .                                    # the production image
```

**The dev server is run without `--reload`. Restart it after any code or `.env` change.** An earlier `--reload` silently stopped reloading and the live bot ran stale code for hours.

Live runners spend real API quota. Gemini's free tier is 500 requests/day per key and Groq's fallback is 8,000 tokens/minute, and the running bot shares those quotas. Run the smallest subset that answers your question; prefer the fixtures over the full scenario suite.

## Conventions

- Match the surrounding code: comment density, naming, idiom. Comments explain why, not what.
- Reuse existing helpers (`guard.sanitize_output`, `messages.pick`, `gemini.generate_json`, ...) rather than adding parallel ones.
- Every user-facing string in Hindi/Marathi lives in `app/pipeline/messages.py` or a prompt, never inline in logic.
- Replies use only WhatsApp markup (`*bold*`, `_italic_`, emoji, a divider); build them in `compose.py`, which strips `*_~` from user-derived and model text. A reply must be in one language: an untranslated explanation gets English labels (`ClaimOutcome.local_lang`).
- Tests: `tests/conftest.py` stubs reply-language preferences and button sends for every test (`offline_prefs` fixture). The real `app.db.prefs` functions are stubbed too, so capture them at import time if you test the data layer.
- New behaviour needs a unit test; a bug fix needs a regression test that fails without the fix.
- Dependencies are pinned in `requirements.lock`, which production installs. After changing `requirements.txt`, regenerate it inside Linux, not from the Windows venv: `docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip install -q -r requirements.txt && pip freeze > requirements.lock"`.
- Do not add dependencies without a reason. `Pillow` is used only by the asset scripts and is not in `requirements.txt` (TODO - Needs confirmation: add to `requirements-dev.txt`).
- Shell tooling trap: backslash escapes (`\n`) inside bash heredoc Python snippets get mangled into real newlines and break string literals. Use the Write/Edit tools for any code or data containing backslashes.

## Security rules (do not weaken)

- All user-derived text (message, OCR text, transcript, caption) is untrusted. It goes through `guard.screen_text` before any model sees it, is wrapped with `guard.wrap_untrusted` in prompts, and every model output shown to a user goes through `guard.sanitize_output` (strips links, phone numbers, control characters).
- Only URLs that Tavily actually returned may appear in a reply; titles come from the retrieval, not the model.
- The medical hard stop (`refuse_t3b.txt`, helpline 104) is static text. Never let a model write it or vary it.
- Never cache anything suspicious, blocked, `unverifiable` or errored. Cache writes happen only after a successful WhatsApp send.
- Phone numbers and WhatsApp message IDs are stored only as salted hashes (`app/util.py`); logs use `ref()`.
- No secret in a URL, log line, reply or commit. API keys go in headers. `.env` is git-ignored. Do not print key values in tool output.
- Do not make the injection screen fail closed or open without recording it in `docs/DECISIONS.md`; today the ML layer fails open and the pattern layer still applies.

## Working with other sessions

- Before starting, check `docs/TASKS.md` and `git status` for work touching the same files. If there is overlap or anything contradicts these docs, stop and say so rather than picking silently.
- Add your task to "In Progress" in `docs/TASKS.md` with the files you will touch; move it when done.
- Keep changes focused. Do not rewrite working code because you would write it differently; preserve behaviour unless the task says otherwise.
- Never reset, revert, clean or discard changes you did not make (`git reset --hard`, `git clean`, `git checkout -- .` and similar) without explicit instruction.
- Commit or push only when the user asks.
- If something you learned would change how another session works, write it to the right `docs/` file, not just to the chat.

## Finishing a task

1. Run the tests (and the live fixtures if classification or prompts changed).
2. Review `git diff` and `git status`.
3. Update `docs/PROGRESS.md` (what changed, current state, remaining work, notes), `docs/TASKS.md`, and `docs/DECISIONS.md` / `docs/PROJECT_CONTEXT.md` if a decision or the architecture changed.
4. Say plainly what was and was not verified.

Keep the docs short and true. No transcripts, no debugging output, nothing readable from the code. Unknowns are marked `TODO - Needs confirmation`; never invent.
