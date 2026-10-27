"""Check displayed numbers against explicit evidence, including counts and percentages."""

import re
from decimal import Decimal, DecimalException, InvalidOperation

TOKEN = re.compile(
    r"(?<![\d.])[-+−]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?(?:\s*(?:million|billion|thousand)\b|[kKmMbB]\b)?"
)
WORDS = {
    word: Decimal(i)
    for i, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen".split()
    )
}
WORDS.update(
    {
        word: Decimal(i * 10)
        for i, word in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)
    }
)
SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
NUMBER_WORD = "|".join([*WORDS, *SCALES])
WORD_TOKEN = re.compile(
    r"\b(?:" + NUMBER_WORD + r")(?:[- ]+(?:and[- ]+)?(?:" + NUMBER_WORD + r"))*\b", re.I
)
TOLERANCE = Decimal("0.01")


def numbers(text):
    values = []
    for match in TOKEN.finditer(text):
        raw = match.group().replace(",", "").replace("−", "-").lower()
        scale = re.search(r"\s*(thousand|million|billion|[kmb])$", raw)
        multiplier = 1
        if scale:
            multiplier = {"k": 1000, "m": 1000000, "b": 1000000000, **SCALES}[scale[1]]
            raw = raw[: scale.start()]
        # A hyphen following a digit/letter is an identifier/date separator, not a sign.
        if raw.startswith("-") and match.start() and text[match.start() - 1].isalnum():
            raw = raw[1:]
        try:
            values.append(Decimal(raw) * multiplier)
        except DecimalException:
            values.append(Decimal("Infinity"))
    for match in WORD_TOKEN.finditer(text):
        total, group = Decimal(0), Decimal(0)
        for word in re.split(r"[- ]+", match.group().lower()):
            if word in WORDS:
                group += WORDS[word]
            elif word == "hundred":
                group = (group or 1) * 100
            elif word in SCALES:
                total += (group or 1) * SCALES[word]
                group = Decimal(0)
        values.append(total + group)
    return values


def evidence_numbers(value):
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, dict):
        return [n for v in value.values() for n in evidence_numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in evidence_numbers(v)]
    if isinstance(value, (int, Decimal)):
        return [Decimal(value)]
    if isinstance(value, str):
        try:
            parsed = Decimal(value.replace(",", ""))
            return [parsed] if parsed.is_finite() else []
        except InvalidOperation:
            return numbers(value)
    return []


def matches(value, allowed):
    try:
        return value.is_finite() and any(
            other.is_finite() and abs(value - other) <= TOLERANCE for other in allowed
        )
    except DecimalException:
        return False


def grounded(text, facts):
    allowed = evidence_numbers(facts)
    return all(matches(value, allowed) for value in numbers(text))


def check_answer(answer, citations, results, question: str = ""):
    # Free-text names/descriptions cannot launder an injected number into quantitative
    # evidence. IDs/dates still contribute their displayed components when cited.
    index = {
        r["tool_call_id"]: evidence_numbers(
            [
                [
                    {
                        k: v
                        for k, v in row.items()
                        if k not in {"supplier_name", "description", "item", "message", "refs"}
                    }
                    for row in r["rows"]
                ],
                r["totals"],
            ]
        )
        for r in results
    }
    allowed = []
    for citation in citations:
        try:
            value = Decimal(citation.value.replace(",", ""))
        except InvalidOperation:
            # A cited code such as "OUT-AQK" is not a number; it neither supports nor
            # invalidates numeric claims, so it is skipped rather than failing the answer.
            continue
        if not matches(value, index.get(citation.tool_call_id, [])):
            return False
        # Compare answer text to the original values. Reapplying tolerance to a
        # rounded citation would otherwise allow twice the permitted error.
        allowed.extend(n for n in index[citation.tool_call_id] if matches(value, [n]))
    # Restating the question's own numbers ("in week 34") or the data year is not a claim
    # that needs evidence; every other number must come from a cited tool result.
    restated = [*numbers(question), Decimal(2026)]
    # Digits inside a cited row's own names ("Basmati Rice 5kg", "week 34 (17-23 Aug)") are
    # part of a label, not claims: remove those exact strings before reading numbers.
    text = strip_markers(answer)
    cited = {c.tool_call_id for c in citations}
    labels = sorted(
        {
            str(v)
            for r in results
            if r["tool_call_id"] in cited
            for row in r["rows"]
            for k, v in row.items()
            if k in {"item", "description", "supplier_name", "date_or_week"} and v
        },
        key=len,
        reverse=True,
    )
    for label in labels:
        text = text.replace(label, " ")
    return all(matches(value, allowed) or value in restated for value in numbers(text))


CITATION_MARKER = re.compile(r"\{[^{}]*call_\d+[^{}]*\}|\[[^\[\]]*call_\d+[^\[\]]*\]")
# "1. Basmati Rice" or "(2) Olive Oil": a rank followed by words, at a line start or after a
# space; "1.5 kg" and "AED 2.50" are untouched because a digit, not a space, follows the dot.
LIST_MARKER = re.compile(r"(?:^|(?<=\s))\(?\d{1,2}[.)](?=\s+[A-Za-z])", re.MULTILINE)


def strip_markers(answer: str) -> str:
    """Remove inline citation markers ("{call_1,2095.15}") and list numbering ("1. "): the
    model sometimes writes them into the text, and their digits are not claims."""
    return LIST_MARKER.sub("", CITATION_MARKER.sub("", answer))
