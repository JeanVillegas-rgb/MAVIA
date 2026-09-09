"""Deterministic text analysis used to derive prerequisite edges.

Nothing here calls an LLM. Every function is pure and order-stable so the same
learning objects always yield the same graph, which is what makes the resulting
path reproducible across students and across runs.
"""

import re
from collections import Counter
from math import ceil

# Deliberately small and explicit rather than pulled from NLTK: the corpus is
# short instructional prose, and a fixed list keeps derivation reproducible on a
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
# full sentences lifted from the PDF ("Matter is anything that has mass...") and
# matching on those would fire on almost every object.
MAX_CONCEPT_TITLE_WORDS = 4
MIN_TERM_LENGTH = 3
MIN_SHARED_TERMS_FOR_COOCCURRENCE = 3

# A word shared by many passages of the same lesson is that lesson's ordinary
# vocabulary ("definite", "particle", "shape" in a states-of-matter chapter) and
# says nothing about which passage depends on which. Only a word confined to a
# few passages is evidence of a link.
MAX_DISTINCTIVE_DOCUMENT_FREQUENCY_RATIO = 0.15
MIN_DISTINCTIVE_DOCUMENT_FREQUENCY = 2

# Verbs that open a definition. "keep", "take" and friends are deliberately
# absent: "Solids keep their shape" describes a behaviour, it does not define.
_DEFINITION_VERBS = r"is|are|means|refers\s+to|has|have"
_DEFINITION_OPENING = re.compile(
    rf"^(?:(?:an|a|the) +)?([a-z][a-z ]{{0,28}}?) +(?:{_DEFINITION_VERBS}) "
)
_SENTENCE_SPLIT = re.compile("[.!?" + chr(10) + "]")
MAX_DEFINITION_SUBJECT_WORDS = 3


def strip_part_suffix(title):
    """Drop the chunker's "(Part 1 of 3)" marker so split objects share a term."""
    return _PART_SUFFIX.sub("", title or "").strip()


def part_marker(title):
    """Return ``(base_title, part_number, part_total)`` for a split chunk.

    The content chunker cuts an oversized passage into "(Part 1 of 3)" pieces.
    Those pieces are one passage and must stay together in reading order, so the
    path builder needs to recognise them.
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


def content_terms(text):
    """Salient singularized tokens of a passage, stop words removed."""
    terms = set()
    for token in normalize(text).split():
        if len(token) < MIN_TERM_LENGTH or token in STOP_WORDS:
            continue
        stem = singular(token)
        if len(stem) >= MIN_TERM_LENGTH and stem not in STOP_WORDS:
            terms.add(stem)
    return terms


def concept_terms(title):
    """Terms a short title claims to define.

    Returns the whole normalized phrase plus its singularized significant
    tokens. A long title defines nothing — it is prose, not a concept label.
    """
    stripped = strip_part_suffix(title)
    normalized = normalize(stripped)
    if not normalized:
        return set()

    words = normalized.split()
    if len(words) > MAX_CONCEPT_TITLE_WORDS:
        return set()

    terms = {" ".join(singular(word) for word in words)}
    terms.update(
        singular(word)
        for word in words
        if len(word) >= MIN_TERM_LENGTH and word not in STOP_WORDS
    )
    return {term for term in terms if len(term) >= MIN_TERM_LENGTH}


def searchable_text(learning_object):
    """The text of an object that other objects can be found mentioned in."""
    return normalize(f"{learning_object.title} {learning_object.content}")


def mentions(haystack_normalized, term):
    """Whole-word (or whole-phrase) containment test on normalized text.

    Terms are singular (see :func:`singular`), so the regular plural endings the
    prose actually uses have to be matched back: "gases" must count as a mention
    of "gas", or a comparison passage looks independent of what it compares.
    """
    return (
        re.search(rf"(?<!\w){re.escape(term)}(?:es|s)?(?!\w)", haystack_normalized)
        is not None
    )


def scan_positions(learning_objects):
    """Map object id -> its index in the source document's own sequence."""
    scan_order = sorted(learning_objects, key=lambda lo: (lo.order, lo.id))
    return {learning_object.id: index for index, learning_object in enumerate(scan_order)}


def build_first_mention_ranks(learning_objects):
    """Map object id -> the corpus position where its concept is first mentioned.

    Objects are scanned in the source document's own sequence, so this is the
    one place the PDF's ordering enters the derivation — and only as the
    lowest-priority tie-breaker, never as the ordering itself.
    """
    scan_order = sorted(learning_objects, key=lambda lo: (lo.order, lo.id))
    normalized_by_index = [searchable_text(lo) for lo in scan_order]

    ranks = {}
    for own_index, learning_object in enumerate(scan_order):
        terms = concept_terms(learning_object.title)
        earliest = own_index
        for index, text in enumerate(normalized_by_index):
            if index >= earliest:
                break
            if any(mentions(text, term) for term in terms):
                earliest = index
                break
        ranks[learning_object.id] = earliest
    return ranks


def distinctive_document_frequency_limit(object_count):
    """How often a term may appear before it stops counting as distinctive."""
    return max(
        MIN_DISTINCTIVE_DOCUMENT_FREQUENCY,
        ceil(MAX_DISTINCTIVE_DOCUMENT_FREQUENCY_RATIO * object_count),
    )


def document_frequencies(terms_by_object):
    """Map term -> how many objects of this material contain it."""
    frequencies = Counter()
    for terms in terms_by_object.values():
        frequencies.update(terms)
    return frequencies


def definition_subject(content):
    """Return the concept a passage opens by defining, or ``None``.

    "Matter is anything that has mass" defines *matter*; "Solids keep their
    shape" does not define anything. Recognising the difference is what lets a
    lesson's opening definition be treated as groundwork for the definitions
    that follow it, even when none of them quotes it back.
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
