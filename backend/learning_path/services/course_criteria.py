"""Prerequisite links between topics of one course (the course-level path).

Relatedness gates a pair; the name and terms clues must agree, and the
teacher's outline order is the structure -- headings and PDF order cannot
compare two topics. The meaning clue is recorded, not counted.
"""

from lessons.models import OutlineNode

from . import embeddings
from .calibration import load_calibration
from .clues import clue_records, find_term_owners, meaning_vote, name_vote, term_vote
from .concept_text import prepare
from .fusion import ACCEPTED, PARALLEL, PENDING
from .relatedness import relatedness

# Across topics only the clues that point at something specific vote, and they
# must agree. Measured on the design pair 340-341 (2026-09-30; its key has no
# links): every link resting on the meaning clue (8 accepted) or on a single
# clue (31 suggestions) was wrong -- topics of one subject share vocabulary.
COURSE_CLUES = ("name", "terms")


def course_topics(course, with_content=True):
    """The course's topics in the order its outline teaches them (unit order, then topic order)."""
    nodes = {node.id: node for node in OutlineNode.objects.filter(course=course)}
    with_children = {node.parent_id for node in nodes.values() if node.parent_id}
    if with_content:
        topic_ids = set(
            OutlineNode.objects.filter(course=course, learning_object_groups__isnull=False)
            .values_list("id", flat=True)
        )
    else:
        # Leaves, plus any node holding content itself -- a PDF with no matching
        # subtopic is placed on its unit, and its links must stay visible.
        topic_ids = {node_id for node_id in nodes if node_id not in with_children} | set(
            OutlineNode.objects.filter(course=course, learning_object_groups__isnull=False)
            .values_list("id", flat=True)
        )

    def outline_position(node):
        chain = []
        while node is not None:
            chain.append((node.order, node.id))
            node = nodes.get(node.parent_id)
        return tuple(reversed(chain))

    return sorted((nodes[node_id] for node_id in topic_ids), key=outline_position)


def course_verdict(votes, semantic=True):
    """``(verdict, direction)`` for concepts of two topics; direction +1 means "first before second".

    Name and terms must agree. With the outline -> accepted; against it ->
    pending, flagged for the teacher. The meaning clue is recorded, not counted.
    """
    direction = votes["name"]
    if not direction or votes["terms"] != direction:
        return PARALLEL, 0
    if direction == -votes["outline"]:
        return PENDING, direction
    if semantic:
        return ACCEPTED, direction
    return PENDING, direction


def _votes(first, second, owners, calibration, semantic):
    return {
        "name": name_vote(first, second)[0],
        "terms": term_vote(first, second, owners)[0],
        "meaning": meaning_vote(first, second, calibration["meaning_cutoff"])[0] if semantic else 0,
        "outline": 1,
    }


def _row(first, second, votes, outcome, direction, owners, calibration, semantic, score):
    prerequisite, dependent = (first, second) if direction > 0 else (second, first)
    oriented = {clue: vote * direction for clue, vote in votes.items()}
    voting = [clue for clue in oriented if oriented[clue]]
    records = clue_records(prerequisite, dependent, owners, {}, calibration["meaning_cutoff"], semantic)
    records.pop("heading", None)
    records.pop("order", None)
    evidence = {
        "rule": "course",
        "relatedness": None if score is None else round(score, 3),
        "confidence": round(sum(1 for clue in voting if oriented[clue] == 1) / len(voting), 3) if voting else 0.0,
        "votes": oriented,
        "records": records,
        "contradicts_outline": direction < 0,
        "semantic": semantic,
    }
    return {"prerequisite": prerequisite.concept, "dependent": dependent.concept, "verdict": outcome, "evidence": evidence}


def _decide(first, second, owners, calibration, semantic):
    """The link between ``first`` (earlier topic) and ``second`` (later topic), or ``None``."""
    if not (first.sentences and second.sentences):
        return None
    score = relatedness(first, second) if semantic else None
    if semantic and score < calibration["related_cutoff"]:
        return None
    votes = _votes(first, second, owners, calibration, semantic)
    outcome, direction = course_verdict(votes, semantic)
    if outcome not in (ACCEPTED, PENDING):
        return None
    return _row(first, second, votes, outcome, direction, owners, calibration, semantic, score)


def decide_course_pairs(topic_concepts, calibration=None, embed=None):
    """Every cross-topic pair the evidence accepts or sends to the teacher.

    ``topic_concepts`` lists each topic's concepts, topics in outline order.
    Term ownership is read over the two topics of a pair together, so a term a
    later topic introduces can point back at an earlier topic's concept.
    """
    calibration = calibration or load_calibration()
    concepts = [concept for topic in topic_concepts for concept in topic]
    semantic = True
    try:
        texts = prepare(concepts, embed=embed or embeddings.embed)
    except embeddings.EncoderUnavailable:
        texts, semantic = prepare(concepts), False
    by_topic, start = [], 0
    for topic in topic_concepts:
        by_topic.append(texts[start:start + len(topic)])
        start += len(topic)

    decisions = []
    for first_index, earlier in enumerate(by_topic):
        for later in by_topic[first_index + 1:]:
            owners = find_term_owners(earlier + later)
            for first in earlier:
                for second in later:
                    row = _decide(first, second, owners, calibration, semantic)
                    if row:
                        decisions.append(row)
    return decisions
