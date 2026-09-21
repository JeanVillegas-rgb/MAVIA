"""Small, deterministic text helpers the learning path relies on.

Nothing here calls a model. Every function is pure, so the same concepts always
resolve to the same names and the same split passages are always recognised.
"""

import re

# Deliberately small and explicit rather than pulled from NLTK: the corpus is
# short instructional prose, and a fixed list keeps results reproducible on a
# machine that has no NLTK data downloaded.
STOP_WORDS = frozenset("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down
during each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other others our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these they
this those through to too under until up very was we were what when where which while
who whom why will with would you your yours yourself yourselves
example examples call called uses used use using make makes made take takes
part parts one two three
""".split())

_PART_SUFFIX = re.compile(r"\s*\(\s*part\s+\d+\s+of\s+\d+\s*\)\s*$", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")

# A title only names a concept when it is short. Long titles in this corpus are
# full sentences lifted from the PDF ("Matter is anything that has mass...").
# Six, not four: "Changing From One State to Another" is a heading, and at four
# words that concept could never be referred to.
MAX_CONCEPT_TITLE_WORDS = 6
MIN_TERM_LENGTH = 3

# Verbs that open a definition. "keep", "take" and friends are deliberately
# absent: "Solids keep their shape" describes a behaviour, it does not define.
_DEFINITION_VERBS = r"is|are|means|refers\s+to|has|have"
_DEFINITION_OPENING = re.compile(
    rf"^(?:(?:an|a|the) +)?([a-z][a-z ]{{0,28}}?) +(?:{_DEFINITION_VERBS}) "
)
_SENTENCE_SPLIT = re.compile("[.!?" + chr(10) + "]")
MAX_DEFINITION_SUBJECT_WORDS = 3


def strip_part_suffix(title):
    """Drop the chunker's "(Part 1 of 3)" marker so split pieces share a name."""
    return _PART_SUFFIX.sub("", title or "").strip()


def part_marker(title):
    """Return ``(base_title, part_number, part_total)`` for a split chunk.

    The content chunker cuts an oversized passage into "(Part 1 of 3)" pieces.
    Those pieces are one passage, merged into one concept before ordering.
    """
    match = _PART_SUFFIX.search(title or "")
    if not match:
        return None
    numbers = re.findall(r"\d+", match.group(0))
    if len(numbers) != 2:
        return None
    return strip_part_suffix(title), int(numbers[0]), int(numbers[1])


def normalize(text):
    """Lowercase, drop punctuation, collapse whitespace."""
    lowered = (text or "").lower()
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", lowered)).strip()


def singular(word):
    """A conservative stemmer: only the plural forms this corpus actually uses."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def mentions(haystack_normalized, term):
    """Whole-word (or whole-phrase) containment test on normalized text.

    Terms are singular, so the regular plural endings the prose actually uses
    have to be matched back: "gases" must count as a mention of "gas".
    """
    return (
        re.search(rf"(?<!\w){re.escape(term)}(?:es|s)?(?!\w)", haystack_normalized)
        is not None
    )


def definition_subject(content):
    """Return the concept a passage opens by defining, or ``None``.

    "Matter is anything that has mass" defines *matter*; "Solids keep their
    shape" does not define anything.
    """
    first_sentence = _SENTENCE_SPLIT.split(content or "", 1)[0]
    normalized = normalize(first_sentence)
    if not normalized:
        return None

    match = _DEFINITION_OPENING.match(normalized)
    if not match:
        return None

    words = match.group(1).split()
    if not words or len(words) > MAX_DEFINITION_SUBJECT_WORDS:
        return None
    if all(word in STOP_WORDS or len(word) < MIN_TERM_LENGTH for word in words):
        return None
    return " ".join(singular(word) for word in words)
