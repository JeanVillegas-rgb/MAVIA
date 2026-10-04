"""Deciding a link: the text says whether, the lesson's order says which way (v6).

Spec: docs/superpowers/specs/2026-09-30-learning-path-v6-reference-order-design.md,
section 3. Inside one topic almost every pair looks related, and PDF order agreed
with the key's direction on 145 of 156 linked pairs, so the two questions are
answered by different evidence. A link the text makes and nothing contradicts is
accepted; anything contradicted goes to the teacher.
"""

import math
from dataclasses import dataclass

CONTENT_CLUES = ("name", "terms", "meaning")
STRUCTURE_CLUES = ("heading", "order")
CLUES = CONTENT_CLUES + STRUCTURE_CLUES
# The clues v6 decides with; meaning is recorded, not counted.
DECIDING_CLUES = ("name", "terms", "heading", "order")

ACCEPTED = "accepted"
PENDING = "pending"
# No link. The name is v5's and the course level imports it.
PARALLEL = "parallel"

SHARED = "shared"
NONE_SHARED = "none_shared"
DISAGREE = "disagree"

MAX_AGREEMENT = 0.95

# v6.1 (spec 2026-10-02, C2 and C3): these no longer block a link.
CLEARED_IN_6_1 = ("figure", "no_shared_pdf")


@dataclass(frozen=True)
class PairFacts:
    """One pair of concepts, read as ``earlier`` then ``later`` in the lesson's order."""

    later_names_earlier: float = 0.0
    earlier_names_later: float = 0.0
    later_uses_earlier_terms: float = 0.0
    earlier_uses_later_terms: float = 0.0
    later_uses_earlier_word: float = 0.0
    earlier_uses_later_word: float = 0.0
    heading: int = 0
    pdf_agreement: int = 0
    parallel: bool = False
    earlier_is_figure: bool = False
    later_is_figure: bool = False
    pdf_order: str = SHARED


@dataclass(frozen=True)
class Decision:
    verdict: str
    direction: int
    direction_from: str = ""
    contradictions: tuple = ()


NO_LINK = Decision(PARALLEL, 0)


def _sign(difference):
    return (difference > 0) - (difference < 0)


def _suggested_direction(facts):
    """``(direction, source)`` for a contradicted link (spec section 3)."""
    if facts.earlier_is_figure != facts.later_is_figure:
        if facts.later_is_figure:
            figure_refers = facts.later_names_earlier > 0 or facts.later_uses_earlier_terms > 0
            if figure_refers:
                return 1, "figure"
        else:
            figure_refers = facts.earlier_names_later > 0 or facts.earlier_uses_later_terms > 0
            if figure_refers:
                return -1, "figure"
    elif facts.pdf_order != SHARED:
        name_direction = _sign(facts.later_names_earlier - facts.earlier_names_later)
        if name_direction:
            return name_direction, "name"
    return 1, "pdf_order" if facts.pdf_order == SHARED else "merged_order"


def reference_verdict(facts, cleaner_edges=False):
    """The v6 decision for one pair; direction +1 means earlier before later.

    ``cleaner_edges`` is v6.1: figures and different PDFs no longer block a link,
    and a pair linked only by single shared words is a suggestion (``weak_terms``).
    """
    if facts.parallel:
        return NO_LINK
    later_refers = facts.later_names_earlier > 0 or facts.later_uses_earlier_terms > 0
    earlier_refers = facts.earlier_names_later > 0 or facts.earlier_uses_later_terms > 0
    in_order = "pdf_order" if facts.pdf_order == SHARED else "merged_order"
    if not (later_refers or earlier_refers or facts.heading):
        if cleaner_edges and (facts.later_uses_earlier_word or facts.earlier_uses_later_word):
            return Decision(PENDING, 1, in_order, ("weak_terms",))
        if facts.pdf_agreement:
            return Decision(PENDING, facts.pdf_agreement, "pdf_agreement")
        return NO_LINK
    if facts.heading:
        return Decision(ACCEPTED, facts.heading, "heading")
    contradictions = tuple(name for name, present in (
        ("reverse_name", facts.earlier_names_later > facts.later_names_earlier),
        ("figure", facts.earlier_is_figure or facts.later_is_figure),
        ("no_shared_pdf", facts.pdf_order == NONE_SHARED),
        ("pdfs_disagree", facts.pdf_order == DISAGREE),
        ("backward_only", earlier_refers and not later_refers),
    ) if present and not (cleaner_edges and name in CLEARED_IN_6_1))
    if not contradictions:
        # v6 only gets here with a shared PDF, so its rows still read "pdf_order".
        return Decision(ACCEPTED, 1, in_order)
    direction, source = _suggested_direction(facts)
    return Decision(PENDING, direction, source, contradictions)


def confidence(votes, direction, clues=CLUES):
    """Share of the given clues that voted and agree with ``direction``."""
    voting = [clue for clue in clues if votes.get(clue)]
    if not voting or not direction:
        return 0.0
    return sum(1 for clue in voting if votes[clue] == direction) / len(voting)



def _log_odds(agreement):
    if agreement <= 0.5:
        return 0.0
    agreement = min(agreement, MAX_AGREEMENT)
    return math.log(agreement / (1 - agreement))


def learn_weights(vote_rows):
    """``(weights, agreement)`` per clue, from agreement with the other clues' majority.

    Reported by the calibration, not used for verdicts: weights cannot correct
    errors the content clues share (measured on gold 62/152, 2026-09-30).
    """
    agreement = {}
    for clue in CLUES:
        agreeing = counted = 0
        for votes in vote_rows:
            if not votes[clue]:
                continue
            others = sum(votes[other] for other in CLUES if other != clue)
            if not others:
                continue
            counted += 1
            agreeing += (votes[clue] > 0) == (others > 0)
        agreement[clue] = agreeing / counted if counted else 0.5
    return {clue: _log_odds(agreement[clue]) for clue in CLUES}, agreement
