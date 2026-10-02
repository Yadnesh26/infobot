# Tasks

Add yourself to "In Progress" (with the files you will touch) before starting; move the item when done. Nobody owns anything right now.

## In Progress
- (none)

## Planned

Deployment (files are ready and locally tested; see `deploy/README.md`)
- [ ] Commit the work, create the GitHub repository, `git branch -M main`, push.
- [ ] Create the CloudFormation stack, create `/opt/infobot/app.env` from `scripts/make_app_env.py` through Session Manager, set the four GitHub variables, run the first deploy, check `/health`.
- [ ] Switch Meta's webhook to the new `WebhookUrl`, stop the local server and ngrok, send `claims_test/claim8` to confirm.
- [ ] Decide on a domain (today's default is a free `<ip>.sslip.io` name); add the DNS A record if so.
- [ ] Follow-ups: CloudWatch alarm or an uptime ping on `/health`; move secrets to SSM Parameter Store; pin GitHub Actions to commit SHAs; ARM (t4g) image if cost matters.

Next up (from the reply-redesign session)
- [ ] Send a real WhatsApp message from a number with no `user_prefs` row and confirm the language buttons appear and a tap sets the language (the interactive payload has never reached a phone).
- [ ] Redeploy the Netlify site for the `privacy.html` and `data-deletion.html` edits. How the site is deployed: TODO - Needs confirmation.
- [ ] Investigate Gemini latency: image scenarios took 86-139 s and `ReadTimeout` appeared on 30 s text calls under light parallel load.
- [ ] Full live scenario run (102 scenarios) once quota allows.
- [ ] Data-deletion requests must also remove the `user_prefs` row (the page now promises it); there is no deletion script yet.

Verification and release
- [ ] Commit the 2026-10-01 work (waiting on the user to ask). Exclude `logo-options/` unless the user wants it.
- [ ] Confirm a real WhatsApp message is answered by the restarted server; send `claims_test/claim8` then the others; read the server log.
- [ ] Re-run the medical and non-claim live scenarios once quota allows: `run_scenarios.py F A06 D04 I01 I09 C03`, then the full suite once.
- [ ] Decide on Gemini billing (about $0.001-0.0035 per fresh message); set a budget alert first.

Product and accuracy
- [ ] Reverse image search for photos/videos (today: "I can't tell", plus a Google Lens/TinEye tip).
- [ ] Google Fact Check Tools API as the first step of T2 (cheap, high precision).
- [ ] Gate Scribe transcripts on its confidence / language probability (it invents sentences from mumble).
- [ ] Trim `extract_classify.txt` (~2,100 tokens per message) so the Groq fallback is usable and cost drops.
- [ ] Use reaction feedback (already stored) to find bad replies; grow the fixture set from real misses.

Reliability and operations
- [ ] Durable job queue and retries (background tasks are lost on restart).
- [ ] Move off laptop + ngrok to a real host; add README/Dockerfile. Host: TODO - Needs confirmation.
- [ ] Global daily spend cap and alerts; slow/block users who repeatedly trigger the injection screen; log security events.
- [ ] Fix `site/privacy.html`: it does not list Groq (receives message text for the injection screen and as the text fallback), and it does not say that on Gemini's unpaid tier Google may use submitted content to improve its products (per the Gemini API Additional Terms, outside the EEA/Switzerland/UK). Either move to paid Gemini (paid terms) or disclose it.
- [ ] If more Gemini keys are added: `GEMINI_API_KEY_FALLBACK` currently holds exactly one key; allow a list. Free quota is per project, and the Gemini terms page is silent on using many accounts to multiply it (TODO - Needs confirmation against the general Google API terms).
- [ ] Retention purge for stored data. Window: TODO - Needs confirmation.
- [ ] Add `Pillow` to `requirements-dev.txt` (used by the asset scripts). Confirm.
- [ ] Commit or store the Supabase schema as migrations (none in the repo).

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
- [x] Dockerfile, compose + Caddy, rollback script, CloudFormation stack, CI and deploy workflows (tested locally, not yet deployed; uncommitted)
- [x] Reply redesign: per-user language (buttons/commands/`user_prefs`), WhatsApp-formatted verdict-first replies, mixed-message handling, blurred-image reply, translated cache hits (uncommitted)

## Blocked
- [ ] Full live scenario re-run on the final prompt
  - Reason: Gemini key 1 is at its free daily cap, key 2's 500/day is shared with the live bot; resets daily.
