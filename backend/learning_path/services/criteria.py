"""Prerequisite links inside one topic (criteria v6.3). Writes nothing."""

from . import embeddings, tracing
from .calibration import load_calibration
from .clues import (
    MIN_SHARED_TERMS, find_term_owners, heading_vote, order_vote,
    presented_in_parallel, read_clues, reference_uses, shared_pdf_order,
)
from .concept_text import material_positions, prepare
from .fusion import (
    ACCEPTED, DECIDING_CLUES, DISAGREE, NONE_SHARED, PENDING, SHARED,
    PairFacts, confidence, reference_verdict,
)
from .relatedness import relatedness

RULE = "reference-order"
VERSION = "6.3"

_PDF_ORDER = {1: SHARED, -1: SHARED, 0: DISAGREE, None: NONE_SHARED}


def crosses_sections(first, second):
    """True when both concepts sit under headings and share none."""
    return bool(first.sections and second.sections and not (first.sections & second.sections))


def _is_figure(text):
    return getattr(text.concept, "kind", "text") == "image"


def _facts(first, second, owners, positions):
    """(facts, earlier, later) for a pair, in the topic's merged order."""
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


def decide_pairs(concepts, runtime_instance=None, calibration=None, embed=None):
    """Every pair the text links: accepted, or pending when something contradicts it."""
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
    tracing.log_concepts(texts, owners)
    positions = material_positions(concepts)
    meaning_cutoff = calibration["meaning_cutoff"]

    decisions = []
    for index, first in enumerate(texts):
        for second in texts[index + 1:]:
            if not (first.sentences and second.sentences):
                continue
            facts, earlier, later = _facts(first, second, owners, positions)
            decision = reference_verdict(facts)
            tracing.log_pair(earlier, later, facts, decision)
            if decision.verdict not in (ACCEPTED, PENDING):
                continue
            prerequisite, dependent = (earlier, later) if decision.direction > 0 else (later, earlier)
            votes, records = read_clues(
                prerequisite, dependent, owners, positions, meaning_cutoff, semantic, min_terms=MIN_SHARED_TERMS,
            )
            decisions.append({
                "prerequisite": prerequisite.concept,
                "dependent": dependent.concept,
                "verdict": decision.verdict,
                "evidence": {
                    "rule": RULE,
                    "direction_from": decision.direction_from,
                    "contradictions": list(decision.contradictions),
                    "votes": votes,
                    "records": records,
                    "relatedness": round(relatedness(first, second), 3) if semantic else None,
                    "confidence": round(confidence(votes, 1, DECIDING_CLUES), 3),
                    "semantic": semantic,
                    "version": VERSION,
                },
                "cross_section": crosses_sections(prerequisite, dependent),
            })
    return decisions
