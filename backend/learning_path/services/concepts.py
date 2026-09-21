"""Decide which concept, if any, each learning object owns.

Every rule downstream keys on this answer, so its failures are not local. Two
measured on real material:

* A chunk titled "Ice is a solid" matched the concept ``solid``. It is an
  *example* of a solid; treating it as the concept itself makes every edge
  drawn to or from it wrong.
* A chunk titled "Matter is anything that has mass and occupies space" is the
  definition of ``matter``, but its title is a sentence, so title matching found
  nothing at all and the lesson's root concept became invisible.

Resolution is therefore explicit rather than incidental, and a chunk is allowed
to own *nothing*. A chunk owning no concept can still depend on other chunks;
nothing can depend on it.
"""

import re

from .text_signals import (
    MAX_CONCEPT_TITLE_WORDS,
    MIN_TERM_LENGTH,
    STOP_WORDS,
    definition_subject,
    normalize,
    strip_part_suffix,
)


# Generic instructional labels. A chunk headed "Examples" or "Key Points" is
# document furniture -- it presents concepts, it does not name one, and nothing
# should ever be recorded as depending on it.
#
# Mirrors grouping's `_GENERIC_INSTRUCTIONAL_LABELS`
# (lessons/services/semantic_grouping.py) so the same headings are treated as
# furniture in both places. Kept as a separate, self-contained list rather than
# imported, so this module does not depend on `lessons`; a test in
# test_concepts.py asserts the two sets cannot drift apart.
STRUCTURAL_LABELS = frozenset({
    "introduction", "summary", "conclusion", "overview", "objectives",
    "example", "examples", "everyday examples", "key points", "key facts",
    "key points for students", "key facts to remember", "activity",
    "exercise", "review", "recap", "remember", "note", "notes",
    "additional information", "definition", "diagram", "glossary",
    "practice questions", "questions", "vocabulary", "worksheet",
})


_LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.):-]*\s*")


def strip_numbering(title):
    """Drop a heading's list number: "7. Everyday Examples" -> "Everyday Examples"."""
    return _LEADING_NUMBER.sub("", title or "").strip()


def is_structural(learning_object):
    """True for document furniture ("Everyday Examples") that orders last with no edges."""
    title = strip_numbering(strip_part_suffix(learning_object.title or ""))
    return normalize(title) in STRUCTURAL_LABELS


def _heading_name(heading):
    """A concept name taken from the heading a passage sits under, or ``None``."""
    normalized = normalize(strip_numbering(heading or ""))
    if not normalized or normalized in STRUCTURAL_LABELS:
        return None
    words = normalized.split()
    significant = [
        word for word in words
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH
    ]
    if significant and len(words) <= MAX_CONCEPT_TITLE_WORDS:
        return normalized
    return None


def heading_name(heading):
    """What a section heading names, or ``None``. Public for the criteria."""
    return _heading_name(heading)


def resolve_concept(learning_object):
    """Return the normalised concept this chunk owns, or ``None``."""
    # Split chunks are one passage the chunker cut, so they share one concept.
    # The edge attaches to the first part; the rest follow by continuation.
    title = strip_numbering(strip_part_suffix(learning_object.title or ""))
    normalized = normalize(title)

    if normalized in STRUCTURAL_LABELS:
        return None

    # A title that states something rather than naming something owns the
    # concept it is *about*, not the one it mentions. "Ice is a solid" owns
    # `ice`; reading the body first here returned `water`, because the body
    # went on to talk about melting.
    if normalized and _reads_as_sentence(normalized):
        subject = definition_subject(normalized)
        return normalize(subject) if subject else None

    if normalized:
        words = normalized.split()
        significant = [
            word for word in words
            if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH
        ]
        if significant and len(words) <= MAX_CONCEPT_TITLE_WORDS:
            return normalized

    # A long title names nothing, but the heading it sits under may.
    heading = _heading_name(getattr(learning_object, "section_title", ""))
    if heading:
        return heading

    # A prose title names nothing, but the passage may still open by defining
    # something -- "Matter is anything that has mass" owns `matter`.
    subject = definition_subject(learning_object.content or "")
    return normalize(subject) if subject else None


def _reads_as_sentence(normalized_title):
    """True when a title states something rather than naming something.

    "Solid" names a concept. "Ice is a solid" makes a claim about one, which
    means the title's subject is the concept it owns -- not the concept it
    mentions.
    """
    return definition_subject(normalized_title) is not None
