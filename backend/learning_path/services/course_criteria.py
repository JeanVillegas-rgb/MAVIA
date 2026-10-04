"""Prerequisite links between topics of one course (the course-level path).

The topic-level v5 evidence, read across topics: relatedness gates a pair, the
name, terms and meaning clues are the content family, and the teacher's
outline order is the structure -- headings and PDF order cannot compare two
topics. See docs/superpowers/specs/2026-09-30-course-learning-path-design.md.
"""

from lessons.models import OutlineNode

from . import embeddings
from .calibration import load_calibration
from .clues import clue_records, find_term_owners, meaning_vote, name_vote, term_vote
from .concept_text import prepare
from .course_closest import closest_earlier, closest_verdict, own_topic_median
from .course_shortlist import TOPIC_TITLE_CUTOFF, shortlist, title_vectors, topic_similarities
from .fusion import ACCEPTED, PARALLEL, PENDING
from .relatedness import relatedness

# Across topics only the clues that point at something specific vote, and they
# must agree. Measured on the design pair 340-341 (2026-09-30; its key has no
# links): every link resting on the meaning clue (8 accepted) or on a single
# clue (31 suggestions) was wrong -- topics of one subject share vocabulary.
COURSE_CLUES = ("name", "terms")

STRICT = "strict"
SHORTLIST = "shortlist"
CLOSEST = "closest"
COURSE_RULES = (STRICT, SHORTLIST, CLOSEST)
# The strict rule until the closest rule passes its final check (course spec 2026-10-03, section 5).
COURSE_DEFAULT_RULE = STRICT


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


def _row(first, second, votes, outcome, direction, owners, calibration, semantic, score, extra=None):
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
    evidence.update(extra or {})
    return {"prerequisite": prerequisite.concept, "dependent": dependent.concept, "verdict": outcome, "evidence": evidence}


def _decide(first, second, owners, calibration, semantic):
    """The strict rule's link between ``first`` (earlier topic) and ``second`` (later topic), or ``None``."""
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


def _shortlisted(earlier, later, owners, calibration, vectors, similarity):
    """Course spec 2026-10-02 section 3, levels 2 and 3, for one related pair of topics."""
    rows = []
    for second in later:
        if not second.sentences:
            continue
        for first, rank, score, ranked_by in shortlist(earlier, second, vectors):
            votes = _votes(first, second, owners, calibration, True)
            outcome, direction = course_verdict(votes, True)
            confirmed = outcome == ACCEPTED
            if outcome not in (ACCEPTED, PENDING):
                outcome, direction = PENDING, 1
            extra = {"rule": "course-shortlist", "rank": rank, "ranked_by": ranked_by, "score": score,
                     "topic_similarity": similarity, "confirmed": confirmed}
            rows.append(_row(first, second, votes, outcome, direction, owners, calibration, True,
                             relatedness(first, second), extra))
    return rows


def _closest(earlier, later, owners):
    """Course spec 2026-10-03 section 3: each later concept against its closest earlier concept."""
    rows = []
    for second in later:
        if not second.sentences:
            continue
        first, score = closest_earlier(earlier, second)
        if first is None:
            continue
        verdict, evidence = closest_verdict(first, second, score, own_topic_median(second, later), owners)
        if verdict is not None:
            rows.append({"prerequisite": first.concept, "dependent": second.concept,
                         "verdict": verdict, "evidence": evidence})
    return rows


def decide_course_pairs(topic_concepts, calibration=None, embed=None, rule=None, topic_titles=None):
    """Every cross-topic pair the evidence accepts or sends to the teacher.

    ``topic_concepts`` lists each topic's concepts, topics in outline order;
    ``topic_titles`` their outline titles. ``rule`` is ``STRICT`` (name and
    terms must agree), ``SHORTLIST`` (outline gate, concept shortlist, strict
    confirmation) or ``CLOSEST`` (each later concept's closest earlier concept,
    course spec 2026-10-03); default ``COURSE_DEFAULT_RULE``. The shortlist
    needs the encoder and the titles, the closest rule the encoder; without
    them the strict rule runs.
    Term ownership is read over the two topics of a pair together, so a term a
    later topic introduces can point back at an earlier topic's concept.
    """
    calibration = calibration or load_calibration()
    rule = rule or COURSE_DEFAULT_RULE
    encode = embed or embeddings.embed
    concepts = [concept for topic in topic_concepts for concept in topic]
    semantic = True
    try:
        texts = prepare(concepts, embed=encode)
    except embeddings.EncoderUnavailable:
        texts, semantic = prepare(concepts), False
    by_topic, start = [], 0
    for topic in topic_concepts:
        by_topic.append(texts[start:start + len(topic)])
        start += len(topic)

    use_shortlist = rule == SHORTLIST and semantic and topic_titles is not None
    if use_shortlist:
        try:
            similarities = topic_similarities(topic_titles, encode)
            vectors = title_vectors(texts, encode)
        except embeddings.EncoderUnavailable:
            # The lesson sentences came from the vector cache but the titles could
            # not be embedded: run the strict rule, suggestions only.
            use_shortlist, semantic = False, False

    use_closest = rule == CLOSEST and semantic

    decisions = []
    for first_index, earlier in enumerate(by_topic):
        for second_index in range(first_index + 1, len(by_topic)):
            later = by_topic[second_index]
            owners = find_term_owners(earlier + later)
            if use_closest:
                decisions.extend(_closest(earlier, later, owners))
                continue
            if use_shortlist:
                similarity = similarities[(first_index, second_index)]
                if similarity >= TOPIC_TITLE_CUTOFF:
                    decisions.extend(_shortlisted(earlier, later, owners, calibration, vectors, similarity))
                continue
            for first in earlier:
                for second in later:
                    row = _decide(first, second, owners, calibration, semantic)
                    if row:
                        decisions.append(row)
    return decisions
