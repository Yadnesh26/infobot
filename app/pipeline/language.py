"""Recognising a request to change the reply language, typed or tapped.

Only an exact, whole-message match counts. A forwarded message that merely
contains the word "language" must be fact-checked, not hijacked into a menu.
"""

import re

MENU = "menu"

_MENU_WORDS = {
    "language", "lang", "/language", "/lang", "change language", "choose language", "set language",
    "भाषा", "भाषा बदलें", "भाषा बदलो", "भाषा बदला", "भाषा निवडा", "भाषा बदलणे",
    "bhasha", "bhasa", "bhasha badlo", "bhasha badle",
}

_CHOICES = {
    "en": {"english", "eng", "इंग्लिश", "अंग्रेज़ी", "अंग्रेजी", "इंग्रजी"},
    "hi": {"hindi", "हिंदी", "हिन्दी"},
    "mr": {"marathi", "मराठी"},
}

_BUTTON_IDS = {"lang_en": "en", "lang_hi": "hi", "lang_mr": "mr"}


def _norm(text: str) -> str:
    return re.sub(r"[\s.!?,;:]+", " ", text.casefold()).strip()


def parse_command(text: str | None) -> str | None:
    """'menu' to show the language buttons, 'en'/'hi'/'mr' to set that language
    directly, or None if the text is not a language command."""
    if not text or len(text) > 40:
        return None
    word = _norm(text)
    if word in _MENU_WORDS:
        return MENU
    for code, names in _CHOICES.items():
        if word in names:
            return code
    return None


def from_button(button_id: str | None) -> str | None:
    return _BUTTON_IDS.get(button_id or "")
