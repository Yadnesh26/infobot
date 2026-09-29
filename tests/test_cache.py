from app.db.cache import hash_claim, normalize_for_hash


def test_normalize_collapses_whitespace_and_lowercases():
    assert normalize_for_hash("  Hot   WATER  cures\n\ncovid  ") == "hot water cures covid"


def test_normalize_strips_zero_width_and_emoji():
    text = "hot water​ cures covid \U0001F600\U0001F64F"
    assert normalize_for_hash(text) == "hot water cures covid"


def test_hash_claim_is_stable_across_case_and_whitespace_variants():
    a = hash_claim("Hot water cures COVID")
    b = hash_claim("  hot   water   cures   covid  ")
    assert a == b


def test_hash_claim_differs_for_different_claims():
    assert hash_claim("hot water cures covid") != hash_claim("cold water cures covid")
