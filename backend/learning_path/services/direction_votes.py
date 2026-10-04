"""Which way a prerequisite link points: three votes (criteria v7).

Spec: docs/superpowers/specs/2026-10-01-learning-path-v7-direction-votes-design.md,
sections 4-6. The lesson's order is one vote; the hierarchy and the references
are the other two. Every number here can be recomputed in a spreadsheet from the
block x concept matrix (``export_direction_sheet``).
"""

from dataclasses import dataclass
from statistics import mean

from .clues import heading_vote
from .fusion import ACCEPTED, PENDING, Decision

MIN_OWNED_TERMS = 2
MIN_BLOCKS = 3
SUBSUME_HIGH = 0.8
SUBSUME_LOW = 0.5
REFERENCE_GAP = 0.25

VOTES = ("hierarchy", "order", "reference")


@dataclass(frozen=True)
class Block:
    """One extracted chunk: the concept it belongs to, its PDF, its place there, its stems."""

    owner: int
    material_id: object
    order: float
    stems: frozenset


@dataclass(frozen=True)
class BlockMatrix:
    """Which concepts appear in which blocks; ``present[concept id][i]`` is block i."""

    blocks: tuple
    present: dict

    def count(self, concept_id):
        return sum(self.present[concept_id])

    def together(self, first_id, second_id):
        return sum(1 for a, b in zip(self.present[first_id], self.present[second_id]) if a and b)

    def own_blocks(self, concept_id):
        return [index for index, block in enumerate(self.blocks) if block.owner == concept_id]


def _chunks(concept):
    # Same fallback as concept_text.prepare, so chunks line up with ``passages``.
    return getattr(concept, "members", None) or (concept,)


def build_block_matrix(texts, term_owners):
    """The 0/1 matrix of spec section 4, from ``prepare``'s texts and ``find_term_owners``."""
    blocks = tuple(
        Block(text.id, getattr(chunk, "material_id", None), getattr(chunk, "order", 0), frozenset(stems))
        for text in texts
        for chunk, stems in zip(_chunks(text.concept), text.passages)
    )
    owned = {text.id: set() for text in texts}
    for stem, owner in term_owners.items():
        if owner in owned:
            owned[owner].add(stem)
    present = {}
    for text in texts:
        name = set(text.name)
        present[text.id] = tuple(
            block.owner == text.id
            or (bool(name) and name <= block.stems)
            or len(owned[text.id] & block.stems) >= MIN_OWNED_TERMS
            for block in blocks
        )
    return BlockMatrix(blocks, present)


def hierarchy_vote(a, b, matrix):
    """+1 when ``a`` is the broader idea: a heading naming it, else coverage subsumption
    (Sanderson & Croft 1999)."""
    blocks_a, blocks_b = matrix.count(a.id), matrix.count(b.id)
    both = matrix.together(a.id, b.id)
    record = {"from": "", "blocks_a": blocks_a, "blocks_b": blocks_b, "both": both,
              "p_a_given_b": None, "p_b_given_a": None}
    heading, _ = heading_vote(a, b)
    if heading:
        record["from"] = "heading"
        return heading, record
    if blocks_a < MIN_BLOCKS or blocks_b < MIN_BLOCKS:
        return 0, record
    a_given_b, b_given_a = both / blocks_b, both / blocks_a
    record.update(p_a_given_b=round(a_given_b, 3), p_b_given_a=round(b_given_a, 3))
    vote = 0
    if a_given_b >= SUBSUME_HIGH and b_given_a <= SUBSUME_LOW:
        vote = 1
    elif b_given_a >= SUBSUME_HIGH and a_given_b <= SUBSUME_LOW:
        vote = -1
    if vote:
        record["from"] = "subsumption"
    return vote, record


def teaching_centres(concept_id, matrix):
    """``{pdf: average position of the concept's own blocks there}``."""
    positions = {}
    for index in matrix.own_blocks(concept_id):
        block = matrix.blocks[index]
        positions.setdefault(block.material_id, []).append(block.order)
    return {material: mean(orders) for material, orders in positions.items()}


def centre_order_vote(a, b, matrix):
    """+1 when every PDF teaching both puts ``a``'s centre earlier; 0 when they disagree or none does."""
    centres_a, centres_b = teaching_centres(a.id, matrix), teaching_centres(b.id, matrix)
    shared = [material for material in centres_a if material in centres_b]
    record = {"centres": {str(material): [centres_a[material], centres_b[material]] for material in shared}}
    directions = {(centres_a[m] < centres_b[m]) - (centres_a[m] > centres_b[m]) for m in shared}
    if len(directions) == 1:
        return directions.pop(), record
    return 0, record


def _share_mentioning(holder_id, mentioned_id, matrix):
    own = matrix.own_blocks(holder_id)
    if not own:
        return 0.0
    return sum(1 for index in own if matrix.present[mentioned_id][index]) / len(own)


def reference_vote(a, b, matrix):
    """+1 when ``b``'s own blocks mention ``a`` clearly more than the reverse (RefD, Liang et al. 2015)."""
    b_refers_a = _share_mentioning(b.id, a.id, matrix)
    a_refers_b = _share_mentioning(a.id, b.id, matrix)
    record = {"a_refers_b": round(a_refers_b, 3), "b_refers_a": round(b_refers_a, 3)}
    gap = b_refers_a - a_refers_b
    if gap >= REFERENCE_GAP:
        return 1, record
    if gap <= -REFERENCE_GAP:
        return -1, record
    return 0, record


def cast_votes(a, b, matrix):
    """``(votes, records)`` for "``a`` before ``b``", each vote +1, -1 or 0."""
    hierarchy, hierarchy_record = hierarchy_vote(a, b, matrix)
    order, order_record = centre_order_vote(a, b, matrix)
    reference, reference_record = reference_vote(a, b, matrix)
    votes = {"hierarchy": hierarchy, "order": order, "reference": reference}
    records = {"hierarchy": hierarchy_record, "order": order_record, "reference": reference_record}
    return votes, records


def count_votes(votes):
    """The verdict table of spec section 6; ``direction`` +1 means ``a`` first.

    Silence is not disagreement: the order alone may accept (user decision A,
    2026-10-01), any other vote alone only suggests.
    """
    cast = {name: vote for name, vote in votes.items() if vote}
    if not cast:
        return Decision(PENDING, 1, "merged_order")
    if len(cast) == 1:
        (name, vote), = cast.items()
        if name == "order":
            return Decision(ACCEPTED, vote, "order_only")
        return Decision(PENDING, vote, name)
    total = sum(cast.values())
    if total == 0:
        return Decision(PENDING, votes["order"] or 1, "contested")
    direction = 1 if total > 0 else -1
    if all(vote == direction for vote in cast.values()):
        return Decision(ACCEPTED, direction, "votes_agree")
    if votes["order"] == -direction:
        return Decision(ACCEPTED, direction, "outvoted_order")
    return Decision(ACCEPTED, direction, "majority")
