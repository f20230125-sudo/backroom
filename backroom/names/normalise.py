"""Keep meaningful name tokens while making punctuation and legal forms comparable."""

import re
import unicodedata


def normalise(value: str, entity_type: str | None = None) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\bs\s*&\s*s\b", "saffron and salt", value)
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
    if entity_type == "item":
        # Strip purchasing annotations, retaining product distinctions such as lid,
        # red, baby and sparkling. Packaging words disappear only after a size.
        value = re.sub(
            r"\b(?:\d+\s*[x×]\s*)?\d+(?:\.\d+)?\s*"
            r"(?:kg|kgm|g|gm|ml|ltr|litres?|liters?|l|pcs?|pieces?)\b"
            r"(?:\s+(?:crate|pack|box|bag|case|can|tin|tub|bottle|jar))?",
            " ",
            value,
        )
        # Case packs and counts are how an item is bought, not what it is: "1L X 12",
        # "100 tea bags", "catering pack". A bare "bag" stays, since Paper Bag is a product.
        value = re.sub(
            r"\b\d+(?:\.\d+)?\s*(?:l|ltr|ml|kg|g)\s*[x×]\s*\d+\b|\b\d+\s+(?:tea\s+)?bags?\b"
            r"|\bcatering\s+pack\b|(?:^|\s)[x×]\s*\d+\b|\(\s*(?:box|can|tin|tub|case)\s*\)",
            " ",
            value,
        )
        value = re.sub(r"\b(?:fresh|chilled|kg|kgm|ltr)\b", " ", value)
    value = re.sub(r"[^\w\s]|_", " ", value)
    words = [word for word in value.split() if word != "the"]
    if entity_type == "item":
        words = [_singular(word) for word in words]
    return " ".join(words)


def _singular(word: str) -> str:
    """Regular trade plurals are spelling evidence, unlike product synonyms."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and len(word) > 3 and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def exact_key(value: str, entity_type: str | None = None) -> str:
    """Token order and purchasing annotations do not change an entity's name."""
    return " ".join(sorted(normalise(value, entity_type).split()))


def significant_tokens(value: str, entity_type: str) -> frozenset[str]:
    ignored = {"and"}
    if entity_type == "supplier":
        ignored |= {"llc", "fze", "fzco", "trading"}
    return frozenset(normalise(value, entity_type).split()) - ignored
