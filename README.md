# InfoBot

A WhatsApp fact-checking bot for India. Forward it a message, a screenshot, a voice note or a short video, and it replies with a verdict, how sure it is, and its sources, in English, Hindi or Marathi.

- **Several claims at once.** Up to three per message, each checked separately and answered in a short summary plus one block per claim.
- **Text, images, voice notes, video.** Images are read with Gemini vision; audio and the sound of videos are transcribed with ElevenLabs Scribe (Groq Whisper as backup).
- **Honest about what it knows.** Well-known claims are judged from model knowledge (never shown as "High" confidence); newer or local ones are searched with Tavily and judged only from the sources found; folk-health beliefs get soft general guidance; anything about a person's own medical situation gets a fixed "see a doctor" reply instead of an answer.
- **Hardened against manipulation.** Incoming text is screened for prompt injection before any model sees it, model output is stripped of links and phone numbers, and only links the search actually returned can appear in a reply.
- **Remembers answers.** Checked claims are cached (exact and by meaning), so popular rumours are answered in a second or two.
- **Private by design.** Phone numbers and message ids are stored only as salted hashes; message text and media are not stored. See [site/privacy.html](site/privacy.html).

It is a FastAPI app on Meta's WhatsApp Cloud API, with Supabase (Postgres + pgvector) for the cache, Gemini for language work, Groq as a fallback and for the injection screen, and Tavily for search.

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                              # then fill in your own keys
.venv/Scripts/python -m pytest -q                 # the unit tests need no keys or network
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
ngrok http 127.0.0.1:8000                         # a public https address for Meta's webhook
```

ffmpeg must be on the PATH for voice notes and videos. `scripts/check_demo.py` checks that everything the bot needs is reachable and reports how fast each part is.

## Deploy it

A `Dockerfile`, a compose file with automatic HTTPS (Caddy), an AWS CloudFormation template and GitHub Actions workflows are in [deploy/](deploy/) and [.github/workflows/](.github/workflows/); [deploy/README.md](deploy/README.md) walks through putting it on an EC2 instance.

## Layout

- `app/`: the bot itself (webhook, message pipeline, providers, database access, prompts)
- `tests/`: unit tests that need no keys and no network
- `scripts/`: tools (pre-flight check, launcher, latency benchmark, live scenario runner)
- `db/migrations/`: SQL for the database tables
- `deploy/`, `.github/workflows/`, `Dockerfile`: deployment and CI
- `site/`: the public landing, privacy and data-deletion pages

## License

[MIT](LICENSE)
