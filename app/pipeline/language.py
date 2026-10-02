"""Recognising a request to change the reply language, typed or tapped.

Only a whole-message match counts (a long word may carry a typo). A forwarded
message that merely contains the word "language" must be fact-checked, not
hijacked into a menu.
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


def _distance(a: str, b: str) -> int:
    """Edit distance where swapping two neighbouring letters costs one ("langauge")."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


# Typos only count for long Latin-script words, so "hindu" is never taken for "hindi" and an
# ordinary short message is never pulled into the menu. A misspelling always opens the menu
# rather than setting a language: a wrong guess then costs one tap, not a wrong reply language.
_FUZZY_WORDS = sorted(
    w for w in _MENU_WORDS | {n for names in _CHOICES.values() for n in names}
    if len(w) >= 7 and w.isascii()
)


def _is_typo_of_a_command(word: str) -> bool:
    if not word.isascii() or len(word) < 7:
        return False
    allowed = 1 if len(word) < 12 else 2
    return any(abs(len(word) - len(w)) <= allowed and _distance(word, w) <= allowed for w in _FUZZY_WORDS)


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
    if _is_typo_of_a_command(word):
        return MENU
    return None


def from_button(button_id: str | None) -> str | None:
    return _BUTTON_IDS.get(button_id or "")
