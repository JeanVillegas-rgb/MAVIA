"""Prerequisite links for the learning path (criteria v6.2).

For each pair of concepts in a topic the text decides whether a link exists (a
name, terms one concept explains, or a heading) and the lesson's order decides
which way. Relatedness and meaning are recorded, not counted. Nothing here
writes to the database. See backend/learning_path/CRITERIA.md.
"""

from . import embeddings
from .calibration import load_calibration
from .clues import (
    MIN_SHARED_TERMS, clue_records, find_term_owners, heading_vote, meaning_vote, name_vote, order_vote,
    presented_in_parallel, reference_uses, shared_pdf_order, term_vote,
)
from .concept_text import material_positions, prepare
from .fusion import (
    ACCEPTED, DECIDING_CLUES, DISAGREE, NONE_SHARED, PENDING, SHARED,
    PairFacts, confidence, reference_verdict,
)
from .relatedness import relatedness

__all__ = ["ACCEPTED", "PENDING", "crosses_sections", "decide_pairs"]

# The rule's name in each link's evidence; the review screen's reason text reads it.
RULE = "reference-order"
VERSION = "6.2"

_PDF_ORDER = {1: SHARED, -1: SHARED, 0: DISAGREE, None: NONE_SHARED}


def section_headings(concept):
    """The lesson headings a concept sits under, across every file teaching it."""
    members = getattr(concept, "members", None) or (concept,)
    return {
        (getattr(member, "section_title", "") or "").strip().casefold()
        for member in members
        if (getattr(member, "section_title", "") or "").strip()
    }


def crosses_sections(first, second):
    """True when both concepts sit under headings and share none; shown to the teacher, never decisive."""
    left, right = section_headings(first), section_headings(second)
    return bool(left and right and not (left & right))


def _is_figure(text):
    return getattr(text.concept, "kind", "text") == "image"


def _facts(first, second, owners, positions):
    """``(facts, earlier, later)``; ``first`` precedes ``second`` in the topic's merged order."""
    order, _ = shared_pdf_order(first, second, positions)
    earlier, later = (second, first) if order == -1 else (first, second)
    facts = PairFacts(
        **reference_uses(earlier, later, owners, MIN_SHARED_TERMS),
        heading=heading_vote(earlier, later)[0],
        pdf_agreement=order_vote(earlier, later, positions)[0],
        parallel=presented_in_parallel(first, second),
        earlier_is_figure=_is_figure(earlier),
        later_is_figure=_is_figure(later),
        pdf_order=_PDF_ORDER[order],
    )
    return facts, earlier, later


def _votes(prerequisite, dependent, owners, positions, meaning_cutoff, semantic):
    """Each clue's vote, +1 when it supports ``prerequisite`` first."""
    return {
        "name": name_vote(prerequisite, dependent)[0],
        "terms": term_vote(prerequisite, dependent, owners, MIN_SHARED_TERMS)[0],
        "meaning": meaning_vote(prerequisite, dependent, meaning_cutoff)[0] if semantic else 0,
        "heading": heading_vote(prerequisite, dependent)[0],
        "order": order_vote(prerequisite, dependent, positions)[0],
    }


def decide_pairs(concepts, runtime_instance=None, calibration=None, embed=None):
    """Every pair the text links: accepted when nothing contradicts it, pending otherwise.

    ``concepts`` arrive in the topic's merged order (``concepts_for_topic``).
    ``runtime_instance`` is kept for callers and ignored. Without the encoder
    the verdicts are the same; only relatedness and meaning are not recorded.
    """
    concepts = list(concepts)
    if len(concepts) < 2:
        return []
    calibration = calibration or load_calibration()
    semantic = True
    try:
        texts = prepare(concepts, embed=embed or embeddings.embed)
    except embeddings.EncoderUnavailable:
        texts, semantic = prepare(concepts), False
    owners = find_term_owners(texts)
    positions = material_positions(concepts)
    meaning_cutoff = calibration["meaning_cutoff"]

    decisions = []
    for index, first in enumerate(texts):
        for second in texts[index + 1:]:
            # A concept with no full sentence has nothing to compare.
            if not (first.sentences and second.sentences):
                continue
            facts, earlier, later = _facts(first, second, owners, positions)
            decision = reference_verdict(facts)
            if decision.verdict not in (ACCEPTED, PENDING):
                continue
            prerequisite, dependent = (earlier, later) if decision.direction > 0 else (later, earlier)
            votes = _votes(prerequisite, dependent, owners, positions, meaning_cutoff, semantic)
            decisions.append({
                "prerequisite": prerequisite.concept,
                "dependent": dependent.concept,
                "verdict": decision.verdict,
                "evidence": {
                    "rule": RULE,
                    "direction_from": decision.direction_from,
                    "contradictions": list(decision.contradictions),
                    "votes": votes,
                    "records": clue_records(prerequisite, dependent, owners, positions, meaning_cutoff, semantic,
                                            min_terms=MIN_SHARED_TERMS),
                    "relatedness": round(relatedness(first, second), 3) if semantic else None,
                    "confidence": round(confidence(votes, 1, DECIDING_CLUES), 3),
                    "semantic": semantic,
                    # 6.2: a concept is also named by its parts' titles, within a shared file.
                    "version": VERSION,
                },
                "cross_section": crosses_sections(prerequisite.concept, dependent.concept),
            })
    return decisions
