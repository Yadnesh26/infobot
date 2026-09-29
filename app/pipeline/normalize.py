def normalize_text(text: str) -> str:
    """Text passes through unchanged -- no preprocessing, no translation.

    The LLM handles script and code-mixing downstream.
    """
    return text
