"""Where does a message's time go? Runs representative messages through the real pipeline
(real Gemini, Groq, Tavily, Supabase reads; nothing is written to the database and nothing is
sent to WhatsApp) and breaks each one down by service.

  python scripts/bench_latency.py              # warm-cache behaviour as it is today
  python scripts/bench_latency.py --cold       # force every cache lookup to miss: the worst case
  python scripts/bench_latency.py hot t2       # only cases whose name starts with these

Spends real API quota (about 3-5 model calls per case).
"""

import argparse
import asyncio
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.pipeline.orchestrator as orch  # noqa: E402

CASES = {
    "greeting": "Good morning! Have a nice day",
    "t1-single": "Humans only use 10% of their brain",
    "t1-repeat": "Humans only use 10% of their brain",  # same text again: the exact-cache path
    "t2-single": "Is it true that the government will give Rs 5000 to every girl child from next month?",
    "three-claims": "1) Lightning never strikes the same place twice. 2) Petrol will cost Rs 200 per litre from tomorrow. 3) Mount Everest is the tallest mountain above sea level.",
    "hindi-claim": "क्या यह सच है कि गर्म पानी पीने से कोरोना ठीक हो जाता है?",
    "mixed-chitchat": "Petrol will cost Rs 200 per litre from tomorrow! Also can you write me a poem about rain?",
}

calls: list[tuple[str, str, float]] = []  # (service, label, seconds)
_orig_send = httpx.AsyncClient.send


def service_of(host: str) -> str:
    for needle, name in (
        ("supabase", "supabase"), ("googleapis", "gemini"), ("groq", "groq"),
        ("tavily", "tavily"), ("facebook", "meta"), ("elevenlabs", "elevenlabs"),
    ):
        if needle in host:
            return name
    return host


async def timed_send(self, request, *args, **kwargs):
    t0 = time.perf_counter()
    try:
        return await _orig_send(self, request, *args, **kwargs)
    finally:
        url = urlparse(str(request.url))
        calls.append((service_of(url.netloc), f"{request.method} {url.path.rsplit('/', 1)[-1][:28]}", time.perf_counter() - t0))


httpx.AsyncClient.send = timed_send


async def run_case(name: str, text: str, cold: bool) -> dict:
    calls.clear()
    t0 = time.perf_counter()
    result = await orch.run_text_pipeline(text, False, allow_cache=False)
    wall = time.perf_counter() - t0
    by_service: dict[str, list[float]] = defaultdict(list)
    for service, _label, secs in calls:
        by_service[service].append(secs)
    return {"name": name, "wall": wall, "by_service": dict(by_service), "kind": result.meta.get("input_kind"),
            "cache": result.cache_hit, "calls": list(calls)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("selectors", nargs="*")
    ap.add_argument("--cold", action="store_true", help="make every cache lookup miss")
    ap.add_argument("--detail", action="store_true", help="list every call")
    args = ap.parse_args()

    if args.cold:
        async def miss(*a, **k):
            return None

        orch.db_cache.lookup_exact = miss
        orch.db_cache.lookup_semantic = miss

    async def no_seen(_id):
        return None

    orch.db_cache.increment_seen = no_seen  # a read-only benchmark must not edit popularity counts

    chosen = {k: v for k, v in CASES.items() if not args.selectors or any(k.startswith(s) for s in args.selectors)}
    rows = []
    for name, text in chosen.items():
        rows.append(await run_case(name, text, args.cold))

    print(f"\n{'case':<16} {'total':>7}  {'kind':<10} {'cache':<9} per-service: seconds spent in calls (count)")
    for r in rows:
        parts = [f"{s} {sum(v):.1f}s({len(v)})" for s, v in sorted(r["by_service"].items(), key=lambda kv: -sum(kv[1]))]
        print(f"{r['name']:<16} {r['wall']:6.1f}s  {str(r['kind']):<10} {str(r['cache']):<9} {'  '.join(parts)}")
        if args.detail:
            for service, label, secs in r["calls"]:
                print(f"    {service:<10} {label:<34} {secs:5.2f}s")
    walls = [r["wall"] for r in rows]
    print(f"\nmedian {statistics.median(walls):.1f}s, slowest {max(walls):.1f}s over {len(rows)} cases (pipeline only: add ~1-2 s for receipt, prefs, rate limit and the reply send)")


async def _run() -> None:
    from app import http

    try:
        await main()
    finally:
        await http.close_all()  # close pooled connections before the event loop does


if __name__ == "__main__":
    asyncio.run(_run())
