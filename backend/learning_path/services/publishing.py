"""Save a topic's learning path: its prerequisite links and its step order.

Runs at the end of a successful publish (``lessons.services.topic_publish``),
so the saved path always matches the content students can see. Three rules,
agreed with the teacher who reviewed the design:

* **Only the latest path is kept.** A new publish replaces the steps.
* **Teacher decisions are never overwritten.** Re-deriving replaces only the
  rows the criteria produced; ``approved`` and ``rejected`` rows stay.
* **Prerequisite links win over document order.** Kahn's sort respects every
  ``accepted`` and ``approved`` link; ties go to the merged document order.
"""

import logging
import math
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from ..models import ConceptPrerequisite, LearningPathStep
from . import criteria
from .concept_units import concepts_for_topic

logger = logging.getLogger(__name__)


def _stored_links(node):
    """``{(prerequisite id, dependent id): row}`` for the topic's stored links."""
    # select_for_update locks the rows on PostgreSQL so a concurrent Accept waits;
    # SQLite ignores it (its IMMEDIATE transaction already holds the write lock).
    return {
        (row.prerequisite_id, row.dependent_id): row
        for row in ConceptPrerequisite.objects.select_for_update().filter(outline_node=node)
    }


def refresh_prerequisites(node, concepts=None, runtime_instance=None):
    """Re-derive this topic's links, keeping every teacher decision.

    Returns ``{"accepted": n, "pending": n, "teacher_decided": n}``.
    """
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    decisions = criteria.decide_pairs(concepts, runtime_instance)

    fresh = {}
    for decision in decisions:
        pair = (decision["prerequisite"].id, decision["dependent"].id)
        fresh[pair] = decision

    with transaction.atomic():
        # Read inside the transaction: two screens opening at once both derive,
        # and the later one must see what the earlier one stored.
        existing = _stored_links(node)
        decided_pairs = {
            pair for pair, row in existing.items()
            if row.status in ConceptPrerequisite.TEACHER_DECIDED
        }

        for pair, decision in fresh.items():
            row = existing.get(pair)
            if row is None:
                # get_or_create, not create: a concurrent request may have
                # stored this pair after the read above.
                row, created = ConceptPrerequisite.objects.get_or_create(
                    prerequisite_id=pair[0],
                    dependent_id=pair[1],
                    defaults={
                        "outline_node": node,
                        "status": decision["verdict"],
                        "source": ConceptPrerequisite.Source.DERIVED,
                        "cross_section": decision["cross_section"],
                        "evidence": decision["evidence"],
                    },
                )
                if created:
                    continue
            if row.status in ConceptPrerequisite.TEACHER_DECIDED:
                # The teacher's call stands; refresh only the explanation.
                row.evidence = decision["evidence"]
                row.cross_section = decision["cross_section"]
                row.save(update_fields=["evidence", "cross_section", "updated_at"])
                continue
            # Updated in place, not re-created: the review screen derives on
            # every load, and an Accept or Undo holds this row's id. The write
            # is conditional on the stored row: a teacher decision that landed
            # after the read above must not be overwritten by this stale copy.
            ConceptPrerequisite.objects.filter(pk=row.pk).exclude(
                status__in=ConceptPrerequisite.TEACHER_DECIDED,
            ).update(
                status=decision["verdict"],
                source=ConceptPrerequisite.Source.DERIVED,
                cross_section=decision["cross_section"],
                evidence=decision["evidence"],
                updated_at=timezone.now(),
            )

        # Derived rows the criteria no longer produce -- again only while the
        # stored row is still derived.
        stale = [
            row.pk for pair, row in existing.items()
            if pair not in fresh and row.status not in ConceptPrerequisite.TEACHER_DECIDED
        ]
        ConceptPrerequisite.objects.filter(pk__in=stale).exclude(
            status__in=ConceptPrerequisite.TEACHER_DECIDED,
        ).delete()

    counts = {"accepted": 0, "pending": 0, "teacher_decided": len(decided_pairs)}
    for decision in fresh.values():
        pair = (decision["prerequisite"].id, decision["dependent"].id)
        if pair not in decided_pairs:
            counts[decision["verdict"]] += 1
    return counts


def path_links(node, concept_ids):
    """The links that shape the order: accepted or approved, between live concepts."""
    return [
        (row.prerequisite_id, row.dependent_id)
        for row in ConceptPrerequisite.objects.filter(
            outline_node=node,
            status__in=ConceptPrerequisite.SHAPES_PATH,
        )
        if row.prerequisite_id in concept_ids and row.dependent_id in concept_ids
    ]


def _find_cycle(links):
    """The links of one loop, or ``None`` when the links form no loop."""
    successors = defaultdict(list)
    for before, after in links:
        successors[before].append(after)
    state, stack = {}, []

    def visit(concept_id):
        state[concept_id] = "open"
        stack.append(concept_id)
        for following in sorted(successors[concept_id]):
            if state.get(following) == "open":
                loop = stack[stack.index(following):] + [following]
                return list(zip(loop, loop[1:]))
            if following not in state:
                found = visit(following)
                if found:
                    return found
        state[concept_id] = "done"
        stack.pop()
        return None

    for concept_id in sorted(successors):
        if concept_id not in state:
            found = visit(concept_id)
            if found:
                return found
    return None


def break_cycles(links, confidence=None):
    """Drop the least confident derived link of each loop until none is left.

    A link missing from ``confidence`` is the teacher's and outranks any
    derived link, so a loop is always broken at something the criteria said.
    """
    confidence = confidence or {}
    kept, ignored = set(links), []
    while True:
        loop = _find_cycle(kept)
        if not loop:
            return kept, ignored
        weakest = min(loop, key=lambda link: (confidence.get(link, math.inf), link))
        kept.discard(weakest)
        ignored.append(weakest)


def redundant_links(links):
    """Links a longer chain already implies: A->C when A->B->C exists."""
    links = set(links)
    successors = defaultdict(set)
    for before, after in links:
        successors[before].add(after)
    redundant = set()
    for before, after in links:
        seen, waiting = set(), [following for following in successors[before] if following != after]
        while waiting:
            current = waiting.pop()
            if current == after:
                redundant.add((before, after))
                break
            if current not in seen:
                seen.add(current)
                waiting.extend(successors[current])
    return redundant


def order_with_links(concepts, links, confidence=None, build_on_latest=False):
    """Kahn's topological sort over the links; returns ``(ordered, depth by id, ignored links)``.

    Loops are first broken at their least confident derived link. Among
    concepts ready at the same time the earliest in the topic's merged PDF
    order goes first. ``build_on_latest`` first prefers the concept building
    on the step placed most recently; it is off because it lowered Kendall's
    tau on the development topics (79: 0.71 vs 1.00, 152: 0.73 vs 1.00, with
    examples left out; docs/learning-path-v5-evaluation-2026-09-30.md).
    """
    position = {concept.id: index for index, concept in enumerate(concepts)}
    kept, ignored = break_cycles(
        {(before, after) for before, after in links if before in position and after in position},
        confidence,
    )
    successors = {concept.id: set() for concept in concepts}
    prerequisites = {concept.id: set() for concept in concepts}
    for before, after in kept:
        successors[before].add(after)
        prerequisites[after].add(before)

    placed_at = {}
    remaining = {concept_id: len(prerequisites[concept_id]) for concept_id in position}

    def priority(concept_id):
        latest = max((placed_at[before] for before in prerequisites[concept_id]), default=-1)
        return (latest if build_on_latest else -1, -position[concept_id])

    by_id = {concept.id: concept for concept in concepts}
    ordered = []
    while len(ordered) < len(concepts):
        ready = [concept_id for concept_id, count in remaining.items() if count == 0 and concept_id not in placed_at]
        chosen = max(ready, key=priority)
        placed_at[chosen] = len(ordered)
        ordered.append(by_id[chosen])
        for following in successors[chosen]:
            remaining[following] -= 1

    depth = {concept.id: 0 for concept in ordered}
    for concept in ordered:
        for following in successors[concept.id]:
            depth[following] = max(depth[following], depth[concept.id] + 1)
    return ordered, depth, ignored


def path_link_confidence(node, concept_ids):
    """Confidence of each derived accepted link; approved links are absent, so never broken first."""
    return {
        (row.prerequisite_id, row.dependent_id): float((row.evidence or {}).get("confidence", 0.0))
        for row in ConceptPrerequisite.objects.filter(
            outline_node=node, status=ConceptPrerequisite.Status.ACCEPTED,
        )
        if row.prerequisite_id in concept_ids and row.dependent_id in concept_ids
    }


@transaction.atomic
def save_learning_path(node, concepts=None):
    """Replace the topic's saved steps with the current link-respecting order."""
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    concept_ids = {concept.id for concept in concepts}
    links = path_links(node, concept_ids)
    ordered, depth, ignored = order_with_links(concepts, links, path_link_confidence(node, concept_ids))

    now = timezone.now()
    LearningPathStep.objects.filter(outline_node=node).delete()
    LearningPathStep.objects.bulk_create([
        LearningPathStep(
            outline_node=node,
            concept_id=concept.id,
            position=position,
            depth=depth[concept.id],
            published_at=now,
        )
        for position, concept in enumerate(ordered, start=1)
    ])
    return {
        "steps": len(ordered),
        "links": len(links),
        "moved": sum(1 for index, concept in enumerate(ordered) if concepts[index].id != concept.id),
        "ignored_links": ignored,
        "published_at": now.isoformat(),
    }


def publish_learning_path(node, runtime_instance=None):
    """Derive the links, then save the path. The publish pipeline's single entry."""
    concepts = concepts_for_topic(node)
    link_counts = refresh_prerequisites(node, concepts, runtime_instance)
    saved = save_learning_path(node, concepts)
    logger.info(
        "[Publish] topic %s  learning path saved: %s steps, %s prerequisite links "
        "(%s accepted automatically, %s set by the teacher, %s suggestions waiting), "
        "%s concept(s) reordered by links",
        node.id, saved["steps"], saved["links"], link_counts["accepted"],
        link_counts["teacher_decided"], link_counts["pending"], saved["moved"],
    )
    # Cross-topic links follow the topic's new concepts. A failure here must not
    # undo a publish the teacher already sees as done.
    from . import course_links

    try:
        # A savepoint: on PostgreSQL a failed statement would otherwise abort a
        # caller's whole transaction, even though the error is caught here.
        with transaction.atomic():
            course_counts = course_links.refresh_course_links(node.course)
    except Exception as exc:  # noqa: BLE001 -- logged; the topic path is already saved
        logger.warning("[Publish] topic %s  links to other topics were not refreshed: %s", node.id, exc)
        course_counts = None
    return {**saved, **link_counts, "course_links": course_counts}
