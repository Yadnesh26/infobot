"""Run the tier-classification fixture set against the real Gemini API.

Not part of the pytest suite deliberately -- it costs real quota and hits a
live provider, matching the plan's Phase 12 distinction between fast unit
tests (mocked) and the fixture-set accuracy pass (live). Run manually:

    python scripts/run_tier_fixtures.py

The dosage/t3b cases are the safety-critical bar M5 is built around: per the
plan's risk register, a t3b claim misrouted to t3a is the single most
dangerous failure mode in this whole system, so those failures are called out
separately and make the script exit non-zero even if everything else passes.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.classify import extract_and_classify  # noqa: E402

FIXTURE_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "tier_fixtures.json"
DELAY_BETWEEN_CALLS = 1.5  # stay well clear of per-minute rate limits


async def main() -> int:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    results = []

    for case in fixtures:
        for attempt in range(2):
            try:
                result = await extract_and_classify(case["text"], frequently_forwarded=False)
                break
            except Exception as exc:
                if attempt == 0:
                    print(f"  (retrying after error: {exc})")
                    await asyncio.sleep(10)
                else:
                    result = None
        expected_is_claim = case.get("expected_is_claim", True)
        if result is None:
            passed = False
        else:
            ok_claim = result.is_verifiable_claim == expected_is_claim
            ok_tier = (result.tier == case["expected_tier"]) if expected_is_claim else True
            passed = ok_claim and ok_tier
        results.append((case, result, passed))
        await asyncio.sleep(DELAY_BETWEEN_CALLS)

    failures = [(c, r) for c, r, passed in results if not passed]

    print(f"\n{'=' * 70}\n{len(results) - len(failures)}/{len(results)} passed\n{'=' * 70}\n")
    for case, result, passed in results:
        status = "PASS" if passed else "FAIL"
        expected = case.get("expected_tier") or "not-a-claim"
        got = "ERROR" if result is None else (result.tier if result.is_verifiable_claim else "not-a-claim")
        print(f"[{status}] expected={expected:<12} got={got:<12} :: {case['text'][:65]}  ({case['note']})")

    dosage_failures = [c for c, _ in failures if c.get("expected_tier") == "t3b"]
    if dosage_failures:
        print(f"\n*** SAFETY-CRITICAL: {len(dosage_failures)} dosage/t3b case(s) misclassified ***")
        for c in dosage_failures:
            print(" -", c["text"])

    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
