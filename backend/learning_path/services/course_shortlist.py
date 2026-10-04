"""The top-down course level: which topics to compare, and which concepts to shortlist.

Spec: docs/superpowers/specs/2026-10-02-course-path-top-down-design.md, section 3.
Level 1 compares the outline's topic titles; level 2 ranks an earlier topic's
concepts for each concept of a later topic by concept title, or by lesson text
when a title is not a real name (a page label, a whole sentence).
"""

import numpy as np

from .relatedness import relatedness

# Design pairs, 2026-10-02: linked topic pairs scored 0.34-0.71, the same-subject
# pair with no links 0.22, unrelated pairs 0.00-0.20.
TOPIC_TITLE_CUTOFF = 0.30
SHORTLIST_SIZE = 3


def unit_vectors(strings, embed):
    vectors = np.asarray(embed(list(strings)), dtype="float32")
    if not len(vectors):
        return vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def topic_similarities(topic_titles, embed):
    """``{(earlier, later): cosine of the two outline titles}`` for every ordered pair of topics."""
    vectors = unit_vectors(topic_titles, embed)
    return {
        (first, second): round(float(vectors[first] @ vectors[second]), 3)
        for first in range(len(topic_titles))
        for second in range(first + 1, len(topic_titles))
    }


def title_vectors(texts, embed):
    """``{concept id: unit vector of its title}``."""
    vectors = unit_vectors([text.concept.title or "" for text in texts], embed)
    return {text.id: vector for text, vector in zip(texts, vectors)}


def concept_score(first, second, vectors):
    """``(score, ranked_by)``: title cosine when both titles are names, else the lesson-text score."""
    if first.name and second.name:
        return float(vectors[first.id] @ vectors[second.id]), "title"
    return float(relatedness(first, second)), "content"


def shortlist(earlier, later_text, vectors, size=SHORTLIST_SIZE):
    """The ``size`` concepts of ``earlier`` closest to ``later_text``, as ``(text, rank, score, ranked_by)``."""
    scored = []
    for text in earlier:
        if not text.sentences:
            continue
        score, ranked_by = concept_score(text, later_text, vectors)
        scored.append((score, text, ranked_by))
    scored.sort(key=lambda item: -item[0])  # stable: ties keep the earlier topic's order
    return [(text, rank, round(score, 3), ranked_by) for rank, (score, text, ranked_by) in enumerate(scored[:size], start=1)]
