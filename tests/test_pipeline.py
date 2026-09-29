import pytest

from app.pipeline import classify, verify
from app.pipeline.compose import compose_t1_reply, compose_t2_reply
from app.pipeline.confidence import confidence_label
from app.pipeline.orchestrator import run_text_pipeline


def _stub_cache_miss(monkeypatch):
    """Stub the cache/embedding calls in the orchestrator to simulate a full miss,
    so tests that only care about classify/verify routing don't need a real DB
    or a real embedding call.
    """

    async def fake_lookup_exact(claim_hash):
        return None

    async def fake_embed_text(text, output_dimensionality=768):
        return [0.0] * 768

    async def fake_lookup_semantic(embedding, threshold, limit=1):
        return None

    monkeypatch.setattr("app.pipeline.orchestrator.db_cache.lookup_exact", fake_lookup_exact)
    monkeypatch.setattr("app.pipeline.orchestrator.embed_text", fake_embed_text)
    monkeypatch.setattr("app.pipeline.orchestrator.db_cache.lookup_semantic", fake_lookup_semantic)


@pytest.mark.anyio
async def test_extract_and_classify_bumps_t1_to_t2_when_frequently_forwarded(monkeypatch):
    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "en",
            "is_verifiable_claim": True,
            "claim_original": "hot water cures covid",
            "claim_english": "hot water cures covid",
            "tier": "t1",
            "domain": "health",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await classify.extract_and_classify("some text", frequently_forwarded=True)
    assert result.tier == "t2"


@pytest.mark.anyio
async def test_extract_and_classify_leaves_t1_alone_when_not_forwarded(monkeypatch):
    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "en",
            "is_verifiable_claim": True,
            "claim_original": "x",
            "claim_english": "x",
            "tier": "t1",
            "domain": "other",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await classify.extract_and_classify("some text", frequently_forwarded=False)
    assert result.tier == "t1"


@pytest.mark.anyio
async def test_extract_and_classify_falls_back_when_claim_english_is_empty(monkeypatch):
    """Regression: the model can leave claim_english blank on an ambiguous claim
    while still filling claim_original -- neither search nor verification can
    work from an empty string, so it must fall back rather than propagate "".
    """

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "hi",
            "is_verifiable_claim": True,
            "claim_original": "चंद्रयान-3 ने पानी खोजा",
            "claim_english": "",
            "tier": "t2",
            "domain": "science",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await classify.extract_and_classify("some text", frequently_forwarded=False)
    assert result.claim_english == "चंद्रयान-3 ने पानी खोजा"


@pytest.mark.anyio
async def test_verify_t1_caps_confidence_below_high_even_when_model_is_confident(monkeypatch):
    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "false",
            "model_is_confident": True,
            "explanation_english": "no it doesn't",
            "explanation_original_language": "no it doesn't",
        }

    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t1("claim", "claim", "en")
    assert result.confidence == 60
    assert confidence_label(result.confidence) == "Medium"  # never "High" -- no retrieval happened


@pytest.mark.anyio
async def test_verify_t1_low_confidence_when_model_unsure(monkeypatch):
    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "true",
            "model_is_confident": False,
            "explanation_english": "probably",
            "explanation_original_language": "probably",
        }

    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t1("claim", "claim", "en")
    assert result.confidence == 25
    assert confidence_label(result.confidence) == "Low"


@pytest.mark.anyio
async def test_verify_t1_unverifiable_has_no_confidence(monkeypatch):
    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "unverifiable",
            "model_is_confident": False,
            "explanation_english": "no idea",
            "explanation_original_language": "no idea",
        }

    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t1("claim", "claim", "en")
    assert result.confidence is None
    assert confidence_label(result.confidence) is None


def test_confidence_label_buckets():
    assert confidence_label(None) is None
    assert confidence_label(10) == "Low"
    assert confidence_label(39) == "Low"
    assert confidence_label(40) == "Medium"
    assert confidence_label(69) == "Medium"
    assert confidence_label(70) == "High"
    assert confidence_label(100) == "High"


def test_compose_t1_reply_omits_confidence_line_when_unverifiable():
    c = classify.ClassifyResult("en", True, "x", "x", "t1", "other")
    v = verify.VerifyResult("unverifiable", None, "no idea", "no idea")
    reply = compose_t1_reply(c, v, frequently_forwarded=False)
    assert "Confidence:" not in reply
    assert "Unverifiable" in reply


def test_compose_t1_reply_adds_forwarded_notice():
    c = classify.ClassifyResult("en", True, "x", "x", "t1", "other")
    v = verify.VerifyResult("false", 60, "nope", "nope")
    reply = compose_t1_reply(c, v, frequently_forwarded=True)
    assert "forwarded many times" in reply
    assert "Confidence: Medium" in reply


@pytest.mark.anyio
async def test_orchestrator_short_circuits_non_claims(monkeypatch):
    _stub_cache_miss(monkeypatch)

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "en",
            "is_verifiable_claim": False,
            "claim_original": "",
            "claim_english": "",
            "tier": "t1",
            "domain": "other",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await run_text_pipeline("good morning", frequently_forwarded=False)
    assert "nothing to verify" in result.reply_text
    assert result.pending_claim_write is None


@pytest.mark.anyio
async def test_orchestrator_returns_stub_for_unsupported_tiers(monkeypatch):
    _stub_cache_miss(monkeypatch)

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "hi",
            "is_verifiable_claim": True,
            "claim_original": "x",
            "claim_english": "x",
            "tier": "t3b",
            "domain": "health",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await run_text_pipeline("some dosage question", frequently_forwarded=False)
    assert "t3b" in result.reply_text
    assert "doesn't support yet" in result.reply_text
    assert result.pending_claim_write is None


@pytest.mark.anyio
async def test_orchestrator_exact_cache_hit_skips_llm_entirely(monkeypatch):
    called_classify = False

    async def fake_lookup_exact(claim_hash):
        return {
            "id": "claim-123",
            "verdict": "false",
            "confidence": 60,
            "explanation_en": "cached explanation",
        }

    async def fake_increment_seen(claim_id):
        assert claim_id == "claim-123"

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        nonlocal called_classify
        called_classify = True
        raise AssertionError("classify should not run on a cache hit")

    monkeypatch.setattr("app.pipeline.orchestrator.db_cache.lookup_exact", fake_lookup_exact)
    monkeypatch.setattr("app.pipeline.orchestrator.db_cache.increment_seen", fake_increment_seen)
    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_generate_json)

    result = await run_text_pipeline("hot water cures covid", frequently_forwarded=False)
    assert result.cache_hit == "exact"
    assert "cached explanation" in result.reply_text
    assert not called_classify
    assert result.pending_claim_write is None


@pytest.mark.anyio
async def test_orchestrator_writes_pending_claim_only_on_fresh_t1_verdict(monkeypatch):
    _stub_cache_miss(monkeypatch)

    async def fake_classify_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "en",
            "is_verifiable_claim": True,
            "claim_original": "x",
            "claim_english": "x english",
            "tier": "t1",
            "domain": "other",
        }

    async def fake_verify_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "false",
            "model_is_confident": True,
            "explanation_english": "nope",
            "explanation_original_language": "nope",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_classify_json)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_verify_json)

    result = await run_text_pipeline("x", frequently_forwarded=False)
    assert result.pending_claim_write is not None
    assert result.pending_claim_write["verdict"] == "false"
    assert result.pending_claim_write["confidence"] == 60
    assert result.pending_claim_write["embedding"] == [0.0] * 768


# --- T2 (search-grounded) ---


@pytest.mark.anyio
async def test_verify_t2_unverifiable_when_no_search_results(monkeypatch):
    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return []

    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)

    result = await verify.verify_t2("claim", "claim", "en")
    assert result.verdict == "unverifiable"
    assert result.confidence is None
    assert result.sources == []


@pytest.mark.anyio
async def test_verify_t2_single_source_gives_medium_confidence(monkeypatch):
    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return [{"title": "Fact Check A", "url": "https://boomlive.in/a", "content": "..."}]

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "false",
            "used_sources": [{"url": "https://boomlive.in/a", "title": "Fact Check A", "supports_verdict": True}],
            "explanation_english": "debunked",
            "explanation_original_language": "debunked",
        }

    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t2("claim", "claim", "en")
    assert result.confidence == 55
    assert confidence_label(result.confidence) == "Medium"
    assert result.sources == [{"title": "Fact Check A", "url": "https://boomlive.in/a"}]


@pytest.mark.anyio
async def test_verify_t2_two_independent_sources_give_high_confidence(monkeypatch):
    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return [
            {"title": "A", "url": "https://boomlive.in/a", "content": "..."},
            {"title": "B", "url": "https://altnews.in/b", "content": "..."},
        ]

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "false",
            "used_sources": [
                {"url": "https://boomlive.in/a", "title": "A", "supports_verdict": True},
                {"url": "https://altnews.in/b", "title": "B", "supports_verdict": True},
            ],
            "explanation_english": "debunked by two outlets",
            "explanation_original_language": "debunked by two outlets",
        }

    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t2("claim", "claim", "en")
    assert result.confidence == 85
    assert confidence_label(result.confidence) == "High"


@pytest.mark.anyio
async def test_verify_t2_conflicting_sources_give_low_confidence(monkeypatch):
    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return [
            {"title": "A", "url": "https://boomlive.in/a", "content": "..."},
            {"title": "B", "url": "https://example.com/b", "content": "..."},
        ]

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "false",
            "used_sources": [
                {"url": "https://boomlive.in/a", "title": "A", "supports_verdict": True},
                {"url": "https://example.com/b", "title": "B", "supports_verdict": False},
            ],
            "explanation_english": "sources disagree",
            "explanation_original_language": "sources disagree",
        }

    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t2("claim", "claim", "en")
    assert result.confidence == 20
    assert confidence_label(result.confidence) == "Low"


@pytest.mark.anyio
async def test_verify_t2_forces_unverifiable_when_model_cites_nothing(monkeypatch):
    """Safety net: a verdict with an empty used_sources list must not be trusted,
    even if the model returned a confident-sounding verdict string."""

    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return [{"title": "Irrelevant", "url": "https://example.com/x", "content": "..."}]

    async def fake_generate_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "true",
            "used_sources": [],
            "explanation_english": "nothing relevant found",
            "explanation_original_language": "nothing relevant found",
        }

    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_generate_json)

    result = await verify.verify_t2("claim", "claim", "en")
    assert result.verdict == "unverifiable"
    assert result.confidence is None


def test_compose_t2_reply_includes_sources_block():
    c = classify.ClassifyResult("en", True, "x", "x", "t2", "health")
    v = verify.VerifyResult(
        "false", 85, "debunked", "debunked", sources=[{"title": "BOOM", "url": "https://boomlive.in/a"}]
    )
    reply = compose_t2_reply(c, v, frequently_forwarded=False)
    assert "Sources:" in reply
    assert "BOOM" in reply
    assert "https://boomlive.in/a" in reply
    assert "no sources searched yet" not in reply


@pytest.mark.anyio
async def test_orchestrator_routes_t2_through_search_and_writes_cache(monkeypatch):
    _stub_cache_miss(monkeypatch)

    async def fake_classify_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "hi",
            "is_verifiable_claim": True,
            "claim_original": "x",
            "claim_english": "x english",
            "tier": "t2",
            "domain": "news",
        }

    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return [{"title": "Newschecker", "url": "https://newschecker.in/y", "content": "..."}]

    async def fake_verify_json(prompt, schema, thinking_level="low"):
        return {
            "verdict": "true",
            "used_sources": [{"url": "https://newschecker.in/y", "title": "Newschecker", "supports_verdict": True}],
            "explanation_english": "confirmed",
            "explanation_original_language": "confirmed",
        }

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_classify_json)
    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)
    monkeypatch.setattr("app.pipeline.verify.generate_json", fake_verify_json)

    result = await run_text_pipeline("some recent local rumor", frequently_forwarded=False)
    assert "Newschecker" in result.reply_text
    assert result.pending_claim_write is not None
    assert result.pending_claim_write["tier"] == "t2"
    assert result.pending_claim_write["sources"] == [{"title": "Newschecker", "url": "https://newschecker.in/y"}]


@pytest.mark.anyio
async def test_orchestrator_does_not_cache_unverifiable_t2_result(monkeypatch):
    _stub_cache_miss(monkeypatch)

    async def fake_classify_json(prompt, schema, thinking_level="low"):
        return {
            "detected_language": "en",
            "is_verifiable_claim": True,
            "claim_original": "x",
            "claim_english": "x",
            "tier": "t2",
            "domain": "news",
        }

    async def fake_search_for_claim(claim_english, claim_original, detected_language):
        return []

    monkeypatch.setattr("app.pipeline.classify.generate_json", fake_classify_json)
    monkeypatch.setattr("app.pipeline.verify.search_for_claim", fake_search_for_claim)

    result = await run_text_pipeline("an obscure claim", frequently_forwarded=False)
    assert result.pending_claim_write is None
