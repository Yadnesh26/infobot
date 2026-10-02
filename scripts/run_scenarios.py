"""Run every scenario in scripts/scenarios.py through the real pipeline.

Live: real Gemini/Groq (classify, verify, prompt guard), real Tavily, real
ElevenLabs Scribe, real ffmpeg, and the real cache *reads*. Only WhatsApp
download is replaced (media comes from tests/scenarios/assets/) and nothing is
written to the database. Needs `python scripts/make_scenario_assets.py` first.

  python scripts/run_scenarios.py                 # everything
  python scripts/run_scenarios.py G B02 I0        # ids or id prefixes
  python scripts/run_scenarios.py --show          # also print every reply

Writes a full transcript to tests/scenarios/last_run.md.
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app.main as main  # noqa: E402
from app.whatsapp.parser import InboundMessage  # noqa: E402
from scenarios import S, Scenario  # noqa: E402

ASSETS = ROOT / "tests" / "scenarios" / "assets"
REPORT = ROOT / "tests" / "scenarios" / "last_run.md"

_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".ogg": "audio/ogg; codecs=opus", ".mp4": "video/mp4"}
_SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{10,}|gsk_[0-9A-Za-z]{10,}|sk-[0-9A-Za-z]{10,}|eyJ[0-9A-Za-z_\-]{20,}|tvly-|xi-api|EAA[0-9A-Za-z]{20,}", re.I)
_LEAK = re.compile(r"untrusted_message|input_kind|system prompt|GEMINI_API_KEY|Prompt Guard", re.I)
_URL = re.compile(r"https?://\S+")
_PHONE = re.compile(r"\+?\d[\d\s\-]{9,}\d")
_DEVA = re.compile(r"[ऀ-ॿ]")
_SOURCES_BLOCK = re.compile(r"^\*(?:Sources|स्रोत)\*\n")

FALLBACKS = None  # filled in main()


def build_message(sc: Scenario, files: dict[str, bytes]) -> InboundMessage:
    base = dict(wamid=f"wamid.scn-{sc.id}", sender="910000000000", frequently_forwarded=sc.ff, forwarded=sc.ff)
    for kind, name in (("image", sc.image), ("audio", sc.audio), ("video", sc.video)):
        if name:
            path = ASSETS / name
            files[name] = path.read_bytes()
            return InboundMessage(type=kind, media_id=name, media_mime_type=_MIME[path.suffix], caption=sc.caption, **base)
    return InboundMessage(type="text", text=sc.text, **base)


def devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(1 for c in letters if _DEVA.match(c)) / len(letters) if letters else 0.0


def check(sc: Scenario, reply: str, meta: dict, writes: list[dict]) -> list[str]:
    problems = []
    kind = meta.get("input_kind")
    n = meta.get("n_claims", 0)

    # invariants that hold for every message
    if not reply.strip():
        problems.append("empty reply")
    if len(reply) > 4096:
        problems.append(f"reply is {len(reply)} chars (> 4096)")
    if _SECRET.search(reply):
        problems.append("reply contains something that looks like a credential")
    if _LEAK.search(reply):
        problems.append("reply leaks internal wording")
    # Links and long numbers are only legitimate inside a *Sources* list built from
    # retrieved results, so judge everything else. Blocks are separated by blank lines.
    prose = "\n\n".join(b for b in reply.split("\n\n") if not _SOURCES_BLOCK.match(b))
    stray = _URL.findall(prose)
    if stray:
        problems.append(f"link outside a Sources list: {stray[0][:60]}")
    if _PHONE.search(prose):
        problems.append("reply contains a long number / phone number")

    if sc.blocked is True and not meta.get("blocked") and kind != "abusive_or_manipulation":
        problems.append("expected the input to be blocked, but it was not")
    if sc.blocked is False and meta.get("blocked"):
        problems.append(f"legitimate input was blocked ({meta['blocked']})")
    if sc.kinds is not None and kind not in sc.kinds and not (sc.blocked and meta.get("blocked")):
        problems.append(f"input_kind={kind!r}, expected one of {sorted(sc.kinds)}")
    if sc.claims is not None and not (sc.claims[0] <= n <= sc.claims[1]):
        problems.append(f"{n} claim(s), expected {sc.claims[0]}-{sc.claims[1]}")
    if sc.verdicts is not None:
        bad = [v for v in meta.get("verdicts", []) if v not in sc.verdicts]
        if bad:
            problems.append(f"verdicts {meta.get('verdicts')} not all in {sorted(sc.verdicts)}")
    for rx in sc.must:
        if not re.search(rx, reply, re.I | re.S):
            problems.append(f"reply lacks /{rx}/")
    if sc.must_any and not any(re.search(rx, reply, re.I | re.S) for rx in sc.must_any):
        problems.append(f"reply matches none of {sc.must_any}")
    for rx in sc.must_not:
        if re.search(rx, reply, re.I | re.S):
            problems.append(f"reply contains forbidden /{rx}/")
    if sc.devanagari and devanagari_share(prose) < 0.3:
        problems.append(f"reply is only {devanagari_share(prose):.0%} Devanagari, expected the user's language")
    # The reply must be in the language the claim was written in. Cached answers
    # are stored in English only, so they are exempt; several claims may mix
    # languages on purpose, so only single-claim replies are judged.
    lang = (meta.get("reply_lang") or meta.get("language") or "").lower()
    if kind == "claims" and n == 1 and not any(meta.get("cached") or []) and meta.get("verdicts") not in (["refused"],):
        share = devanagari_share(prose)
        if lang.startswith("en") and share > 0.15:
            problems.append(f"English message answered {share:.0%} in Devanagari")
        if lang[:2] in ("hi", "mr") and share < 0.3:
            problems.append(f"{lang} message answered only {share:.0%} in Devanagari")
    if sc.no_write and writes:
        problems.append(f"{len(writes)} cache write(s) for input that must never be cached")
    if sc.contextual:
        generic = {v for table in FALLBACKS for v in table.values()}
        if any(g in reply for g in generic) and kind not in {"no_speech", "unclear"}:
            problems.append("generic 'no checkable claim' text instead of a reply about this message")
        if len(reply) < 80:
            problems.append("reply too short to be helpful")
    return problems


async def run_one(sc: Scenario, sem: asyncio.Semaphore) -> dict:
    files: dict[str, bytes] = {}

    async def fake_download(media_id: str) -> bytes:
        return files[media_id]

    async with sem:
        t0 = time.time()
        try:
            msg = build_message(sc, files)
            # download_media is looked up by name in app.main at call time
            main.download_media = fake_download
            result = await main._compose_reply(msg, sc.reply_lang)
            reply, meta, writes = result.reply_text, result.meta, result.pending_claim_writes
            error = None
        except Exception as exc:  # the real handler would apologise; record it as a failure
            reply, meta, writes, error = "", {}, [], repr(exc)[:300]
        elapsed = time.time() - t0

    problems = [f"EXCEPTION {error}"] if error else check(sc, reply, meta, writes)
    return {"sc": sc, "reply": reply, "meta": meta, "writes": writes, "problems": problems, "secs": elapsed}


async def amain(selectors: list[str], show: bool):
    chosen = [s for s in S if not selectors or any(s.id.startswith(x) for x in selectors)]

    from app.pipeline import messages

    global FALLBACKS
    FALLBACKS = [messages.NOT_A_CLAIM_FALLBACK]

    # Cache reads are real; don't let hits bump times_seen on real rows.
    async def noop(_id):
        return None

    main.db_cache.increment_seen = noop
    import app.pipeline.orchestrator as orch

    orch.db_cache.increment_seen = noop

    sem = asyncio.Semaphore(2)
    results = []
    for coro in asyncio.as_completed([run_one(sc, sem) for sc in chosen]):
        r = await coro
        sc = r["sc"]
        status = "PASS" if not r["problems"] else "FAIL"
        print(f"{status}  {sc.id:<42} {r['secs']:5.1f}s  kind={r['meta'].get('input_kind')} n={r['meta'].get('n_claims', '-')}", flush=True)
        for p in r["problems"]:
            print(f"        - {p}", flush=True)
        if show:
            print("        " + r["reply"].replace("\n", "\n        "), flush=True)
        results.append(r)

    results.sort(key=lambda r: r["sc"].id)
    passed = sum(1 for r in results if not r["problems"])
    print(f"\n{passed}/{len(results)} scenarios passed")

    by_cat: dict[str, list[int]] = {}
    for r in results:
        c = by_cat.setdefault(r["sc"].cat, [0, 0])
        c[1] += 1
        c[0] += 0 if r["problems"] else 1
    for cat, (ok, total) in by_cat.items():
        print(f"  {cat:<24} {ok}/{total}")

    lines = [f"# Scenario run: {passed}/{len(results)} passed\n"]
    for r in results:
        sc = r["sc"]
        inp = sc.text or f"[{sc.image or sc.audio or sc.video}]" + (f" + caption: {sc.caption}" if sc.caption else "")
        lines += [
            f"## {'✅' if not r['problems'] else '❌'} {sc.id}  ({sc.cat}, {r['secs']:.1f}s)",
            f"**Input:** {inp[:300]}",
            f"**Meta:** `{json.dumps(r['meta'], ensure_ascii=False)}`",
            *[f"**Problem:** {p}" for p in r["problems"]],
            "```", r["reply"], "```", "",
        ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFull transcript: {REPORT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(asyncio.run(amain(args, "--show" in sys.argv)))
