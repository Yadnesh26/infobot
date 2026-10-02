"""Pre-answer the messages you plan to demo, so they come back from the cache in about a second.

  python scripts/prewarm_cache.py              # DRY RUN: shows each answer, stores nothing
  python scripts/prewarm_cache.py --write      # stores the answers so the bot serves them instantly
  python scripts/prewarm_cache.py --show       # print the full replies, not just the first lines

Messages used: every text file in claims_test/ named message.txt, plus any lines you add to
scripts/demo_messages.txt (one message per line, blank lines and # comments ignored).

A single-claim message is cached under the exact text, so send it to the bot unchanged (copy and
paste). Several-claim messages are cached claim by claim, so each claim is instant wherever it
shows up. Read the dry run first: what is stored is what real users will be told. Unverifiable
answers and anything the injection screen flags are never stored, same as in normal use.
Images and voice notes are not pre-answered (their text is only known after they are read).
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import http  # noqa: E402
from app.db import cache as db_cache  # noqa: E402
from app.pipeline.orchestrator import run_text_pipeline  # noqa: E402


def collect_messages() -> list[str]:
    texts: list[str] = []
    for path in sorted((ROOT / "claims_test").glob("*/message.txt")):
        texts.append(path.read_text(encoding="utf-8").strip())
    extra = ROOT / "scripts" / "demo_messages.txt"
    if extra.exists():
        for line in extra.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                texts.append(line.strip())
    seen, unique = set(), []
    for t in texts:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


async def prewarm(texts: list[str], write: bool, show: bool) -> None:
    stored = skipped = 0
    for text in texts:
        t0 = time.perf_counter()
        result = await run_text_pipeline(text, False)
        took = time.perf_counter() - t0
        head = text.replace("\n", " ")[:70]
        print(f"\n--- {head!r}  ({took:.1f} s, input_kind={result.meta.get('input_kind')}, from cache: {result.cache_hit or 'no'})")
        reply = result.reply_text if show else "\n".join(result.reply_text.splitlines()[:6])
        print("    " + reply.replace("\n", "\n    "))
        writes = result.pending_claim_writes
        if not writes:
            print("    -> nothing to store (already cached, a non-claim, unverifiable, or flagged)")
            skipped += 1
            continue
        if not write:
            print(f"    -> would store {len(writes)} claim(s)")
            continue
        for w in writes:
            try:
                await db_cache.insert_claim(w)
                stored += 1
            except Exception as exc:  # most likely a duplicate hash: someone cached it meanwhile
                print(f"    -> could not store one claim: {exc!r}"[:120])
        print(f"    -> stored {len(writes)} claim(s)")
    if write:
        print(f"\nStored {stored} claim(s); {skipped} message(s) needed nothing.")
    else:
        print("\nDry run only. Re-run with --write to store these answers.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    texts = collect_messages()
    print(f"{len(texts)} message(s) to pre-answer ({'WRITING to the database' if args.write else 'dry run'})")
    try:
        await prewarm(texts, args.write, args.show)
    finally:
        await http.close_all()


if __name__ == "__main__":
    asyncio.run(main())
