"""Pre-flight check before a demo: is everything the bot needs up, and how fast is it?

  python scripts/check_demo.py

Tests the real thing: your local server, the public ngrok address and Meta's handshake through it,
the database, both Gemini keys and both Gemini models, Groq, Tavily, ElevenLabs, your WhatsApp
token, ffmpeg, and (on Windows) whether the laptop will stay awake. Prints PASS / WARN / FAIL with
timings, never a secret. Uses a handful of tiny API calls. Exit code 1 if anything FAILed.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import http  # noqa: E402
from app.config import settings  # noqa: E402
from app.providers import gemini, tavily  # noqa: E402

PUBLIC_HEADERS = {"ngrok-skip-browser-warning": "1"}
rows: list[tuple[str, str, str, str]] = []


def record(status: str, name: str, detail: str = "", secs: float | None = None) -> None:
    timing = f"{secs * 1000:5.0f} ms" if secs is not None else ""
    rows.append((status, name, timing, detail))
    print(f"{status:<5} {name:<44} {timing:>9}  {detail}", flush=True)


async def timed(coro):
    t0 = time.perf_counter()
    try:
        return await coro, time.perf_counter() - t0, None
    except Exception as exc:
        return None, time.perf_counter() - t0, exc


# ------------------------------------------------------------------ the bot and its tunnel


async def check_local() -> None:
    r, dt, err = await timed(http.get("local", "http://127.0.0.1:8000/health", timeout=5))
    if err or r.status_code != 200:
        record("FAIL", "bot server on 127.0.0.1:8000", "not answering: start it with scripts/start_demo.ps1")
    else:
        record("PASS", "bot server on 127.0.0.1:8000", "healthy", dt)


async def check_tunnel() -> str | None:
    r, dt, err = await timed(http.get("local", "http://127.0.0.1:4040/api/tunnels", timeout=5))
    if err:
        record("FAIL", "ngrok tunnel", "ngrok is not running (its status page on :4040 is not answering)")
        return None
    tunnels = r.json().get("tunnels", [])
    if not tunnels:
        record("FAIL", "ngrok tunnel", "ngrok is running but has no tunnel")
        return None
    public, target = tunnels[0]["public_url"], tunnels[0]["config"]["addr"]
    record("PASS", "ngrok tunnel", f"{public} -> {target}")
    if "localhost" in target:
        record("WARN", "ngrok forwards to 'localhost'", "use 127.0.0.1:8000: 'localhost' adds ~0.2 s on Windows")

    r, dt, err = await timed(http.get("public", f"{public}/health", timeout=10, headers=PUBLIC_HEADERS))
    if err or r.status_code != 200:
        record("FAIL", "public address reaches the bot", f"{err!r}"[:90] if err else f"HTTP {r.status_code}")
    else:
        record("PASS", "public address reaches the bot", "round trip through ngrok", dt)

    challenge = "424242"
    r, dt, err = await timed(http.get(
        "public", f"{public}/webhook", timeout=10, headers=PUBLIC_HEADERS,
        params={"hub.mode": "subscribe", "hub.verify_token": settings.WA_VERIFY_TOKEN, "hub.challenge": challenge},
    ))
    if err or r.text != challenge:
        record("FAIL", "Meta's webhook handshake via the public URL", "verify token or app is wrong")
    else:
        record("PASS", "Meta's webhook handshake via the public URL", "answers the challenge", dt)
    return public


# ------------------------------------------------------------------ the services behind it


async def check_database() -> None:
    url = f"{settings.SUPABASE_URL}/rest/v1/claims"
    headers = {"apikey": settings.SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}"}
    await timed(http.get("supabase", url, timeout=10, headers=headers, params={"select": "id", "limit": "1"}))  # opens the connection
    r, dt, err = await timed(http.get("supabase", url, timeout=10, headers=headers, params={"select": "id", "limit": "1"}))
    if err or r.status_code != 200:
        record("FAIL", "database (Supabase)", f"{err!r}"[:80] if err else f"HTTP {r.status_code}")
    else:
        record("PASS", "database (Supabase)", "reachable, on a reused connection", dt)


async def check_gemini() -> None:
    keys = [k for k in (settings.GEMINI_API_KEY, settings.GEMINI_API_KEY_FALLBACK) if k]
    body = {
        "contents": [{"role": "user", "parts": [{"text": 'Return {"ok": true}'}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseJsonSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
    }
    for n, key in enumerate(keys, 1):
        for model in gemini._models():
            r, dt, err = await timed(http.post(
                "gemini", f"{gemini._API_BASE}/models/{model}:generateContent", timeout=20,
                headers={"x-goog-api-key": key}, json=body))
            label = f"Gemini key {n}, {model}"
            if err:
                record("FAIL", label, f"{err!r}"[:80])
            elif r.status_code == 200:
                record("PASS", label, "answers", dt)
            elif r.status_code == 429:
                record("WARN", label, "out of quota right now (the other key / model takes over)")
            else:
                record("FAIL" if r.status_code in (400, 401, 403, 404) else "WARN", label, f"HTTP {r.status_code}")
    out, dt, err = await timed(gemini.generate_json("Return {\"ok\": true}", {"type": "object", "properties": {"ok": {"type": "boolean"}}}))
    record("PASS" if out else "FAIL", "Gemini through the bot's own fallback chain", "" if out else f"{err!r}"[:80], dt)
    vec, dt, err = await timed(gemini.embed_text("hello"))
    record("PASS" if vec and len(vec) == 768 else "FAIL", "Gemini embeddings (semantic cache)", "" if vec else f"{err!r}"[:80], dt)


async def check_groq() -> None:
    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
    r, dt, err = await timed(http.post(
        "groq", "https://api.groq.com/openai/v1/chat/completions", timeout=10, headers=headers,
        json={"model": settings.PROMPT_GUARD_MODEL, "messages": [{"role": "user", "content": "hello"}]}))
    ok = not err and r.status_code == 200
    record("PASS" if ok else "WARN", "Groq injection screen", "" if ok else "down: patterns still protect, but the ML layer is off", dt)
    r, dt, err = await timed(http.post(
        "groq", "https://api.groq.com/openai/v1/chat/completions", timeout=15, headers=headers,
        json={"model": settings.GROQ_MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}))
    ok = not err and r.status_code == 200
    record("PASS" if ok else "WARN", "Groq text fallback model", "" if ok else (f"HTTP {r.status_code}" if r is not None else f"{err!r}"[:60]), dt)


async def check_tavily() -> None:
    rows_, dt, err = await timed(tavily.search("WhatsApp forwarded message fact check", max_results=2))
    record("PASS" if rows_ else "FAIL", f"Tavily search (depth {settings.TAVILY_SEARCH_DEPTH})", "" if rows_ else f"{err!r}"[:80], dt)


async def check_elevenlabs() -> None:
    r, dt, err = await timed(http.get("elevenlabs", "https://api.elevenlabs.io/v1/user/subscription", timeout=10,
                                      headers={"xi-api-key": settings.ELEVENLABS_API_KEY}))
    if err:
        record("FAIL", "ElevenLabs (voice notes)", f"{err!r}"[:80])
    elif r.status_code == 200:
        d = r.json()
        left = d.get("character_limit", 0) - d.get("character_count", 0)
        record("PASS" if left > 0 else "FAIL", "ElevenLabs (voice notes)", f"{left:,} characters of credit left", dt)
    else:
        record("WARN", "ElevenLabs (voice notes)", f"key works for speech but its balance cannot be read (HTTP {r.status_code})", dt)


async def check_meta() -> None:
    url = f"https://graph.facebook.com/{settings.WA_API_VERSION}/{settings.WA_PHONE_NUMBER_ID}"
    r, dt, err = await timed(http.get("meta", url, timeout=10, headers={"Authorization": f"Bearer {settings.WA_TOKEN}"},
                                      params={"fields": "display_phone_number,quality_rating"}))
    if err or r.status_code != 200:
        record("FAIL", "WhatsApp token and number", "token rejected or expired" if r is not None and r.status_code in (190, 400, 401) else f"{err!r}"[:80])
    else:
        d = r.json()
        record("PASS", "WhatsApp token and number", f"{d.get('display_phone_number', '?')}, quality {d.get('quality_rating', '?')}", dt)


# ------------------------------------------------------------------ this machine


def check_machine() -> None:
    record("PASS" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "FAIL", "ffmpeg and ffprobe", "needed for voice notes and videos")
    if sys.platform != "win32":
        return
    try:
        battery = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Battery).BatteryStatus"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if battery and battery != "2":
            record("WARN", "power source", "on battery: Windows will sleep and the bot goes offline. Plug in")
        else:
            record("PASS", "power source", "plugged in" if battery else "no battery (desktop)")
        q = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"], capture_output=True, text=True, timeout=15).stdout
        ac = next((int(line.split(":")[1].strip(), 16) for line in q.splitlines() if "Current AC Power Setting Index" in line), None)
        if ac == 0:
            record("PASS", "sleep timeout on power", "never sleeps while plugged in")
        elif ac is not None:
            record("WARN", "sleep timeout on power", f"sleeps after {ac // 60} min: powercfg /change standby-timeout-ac 0")
    except Exception as exc:
        record("WARN", "power settings", f"could not read them ({exc!r})"[:90])


async def main() -> int:
    print(f"Checking the bot at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    try:
        await check_local()
        await check_tunnel()
        await asyncio.gather(check_database(), check_meta())
        await check_gemini()
        await asyncio.gather(check_groq(), check_tavily(), check_elevenlabs())
        check_machine()
    finally:
        await http.close_all()

    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    print(f"\n{len(rows) - len(fails) - len(warns)} passed, {len(warns)} warning(s), {len(fails)} failed.")
    if fails:
        print("Fix the FAIL lines before presenting.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
