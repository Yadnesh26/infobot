def confidence_label(value: int | None) -> str | None:
    """Bucket a stored numeric confidence (0-100) into the display label.

    Shared by fresh T1 results and cache-hit rows read back from the DB, so a
    claim displays the same label regardless of where it came from.
    """
    if value is None:
        return None
    if value >= 70:
        return "High"
    if value >= 40:
        return "Medium"
    return "Low"
