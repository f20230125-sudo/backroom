"""Keep meaningful name tokens while making punctuation and legal forms comparable."""

import re
import unicodedata


def normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    for pattern, replacement in (
        (r"\bl\s*\.\s*l\s*\.\s*c\b\.?", "llc"),
        (r"\bf\s*\.\s*z\s*\.\s*c\s*\.\s*o\b\.?", "fzco"),
        (r"\bf\s*\.\s*z\s*\.\s*e\b\.?", "fze"),
        (r"\blimited liability company\b", "llc"),
        (r"\bfree zone company\b", "fzco"),
        (r"\bfree zone establishment\b", "fze"),
        (r"\btrdg\b", "trading"),
    ):
        value = re.sub(pattern, replacement, value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^\w\s]|_", " ", value)
    return " ".join(word for word in value.split() if word != "the")


def exact_key(value: str) -> str:
    """Token order changes do not change identity; product qualifiers still matter."""
    return " ".join(sorted(normalise(value).split()))
