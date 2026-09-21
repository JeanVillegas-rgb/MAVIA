"""Save a topic's learning path: its prerequisite links and its step order.

Runs at the end of a successful publish (``lessons.services.topic_publish``),
so the saved path always matches the content students can see. Three rules,
agreed with the teacher who reviewed the design:

* **Only the latest path is kept.** A new publish replaces the steps.
* **Teacher decisions are never overwritten.** Re-deriving replaces only the
  rows the criteria produced; ``approved`` and ``rejected`` rows stay.
* **Prerequisite links win over document order.** The order respects every
  ``accepted`` and ``approved`` link; wherever links say nothing, the topic's
  merged document order decides.
"""

import logging

from django.db import transaction
from django.utils import timezone

from ..models import ConceptPrerequisite, LearningPathStep
from . import criteria
from .concept_units import concepts_for_topic
from .concepts import is_structural

logger = logging.getLogger(__name__)


def refresh_prerequisites(node, concepts=None, runtime_instance=None):
    """Re-derive this topic's links, keeping every teacher decision.

    Returns ``{"accepted": n, "pending": n, "teacher_decided": n}``.
    """
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    decisions = criteria.decide_pairs(concepts, runtime_instance)

    existing = {
        (row.prerequisite_id, row.dependent_id): row
        for row in ConceptPrerequisite.objects.filter(outline_node=node)
    }
    decided_pairs = {
        pair for pair, row in existing.items()
        if row.status in ConceptPrerequisite.TEACHER_DECIDED
    }

    fresh = {}
    for decision in decisions:
        pair = (decision["prerequisite"].id, decision["dependent"].id)
        fresh[pair] = decision

    with transaction.atomic():
        # Derived rows the criteria no longer produce, or produce differently,
        # are rebuilt from scratch below.
        ConceptPrerequisite.objects.filter(outline_node=node).exclude(
            status__in=ConceptPrerequisite.TEACHER_DECIDED,
        ).delete()

        for pair, decision in fresh.items():
            if pair in decided_pairs:
                # The teacher's call stands; refresh only the explanation.
                row = existing[pair]
                row.evidence = decision["votes"]
                row.cross_section = decision["cross_section"]
                row.save(update_fields=["evidence", "cross_section", "updated_at"])
                continue
            ConceptPrerequisite.objects.create(
                outline_node=node,
                prerequisite_id=pair[0],
                dependent_id=pair[1],
                status=decision["verdict"],
                source=ConceptPrerequisite.Source.DERIVED,
                cross_section=decision["cross_section"],
                evidence=decision["votes"],
            )

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


def order_with_links(concepts, links):
    """Order concepts so every link is respected, otherwise keeping their order.

    ``concepts`` arrive in the topic's merged document order; that order breaks
    every tie the links leave open. Returns ``(ordered concepts, depth by id,
    ignored links)``. A link is ignored only if links contradict each other in
    a loop -- then the concept earliest in document order is taught first and
    the links it could not satisfy are reported rather than silently dropped.
    """
    position = {concept.id: index for index, concept in enumerate(concepts)}
    structural = {concept.id for concept in concepts if is_structural(concept)}

    def rank(concept_id):
        # Structural concepts ("Everyday Examples") always close the path.
        return (concept_id in structural, position[concept_id])

    successors = {concept.id: set() for concept in concepts}
    indegree = {concept.id: 0 for concept in concepts}
    for before, after in set(links):
        if after not in successors[before]:
            successors[before].add(after)
            indegree[after] += 1

    by_id = {concept.id: concept for concept in concepts}
    ordered, placed, ignored = [], set(), []
    while len(ordered) < len(concepts):
        ready = [cid for cid in indegree if cid not in placed and indegree[cid] == 0]
        if not ready:
            # A loop. Teach the earliest remaining concept and record which of
            # its incoming links could not be honoured.
            chosen = min((cid for cid in indegree if cid not in placed), key=rank)
            ignored.extend(
                (before, chosen) for before, afters in successors.items()
                if chosen in afters and before not in placed
            )
        else:
            chosen = min(ready, key=rank)
        ordered.append(by_id[chosen])
        placed.add(chosen)
        for after in successors[chosen]:
            indegree[after] -= 1

    placed_index = {concept.id: index for index, concept in enumerate(ordered)}
    depth = {concept.id: 0 for concept in ordered}
    for concept in ordered:
        for after in successors[concept.id]:
            if placed_index[after] > placed_index[concept.id]:
                depth[after] = max(depth[after], depth[concept.id] + 1)
    return ordered, depth, ignored


@transaction.atomic
def save_learning_path(node, concepts=None):
    """Replace the topic's saved steps with the current link-respecting order."""
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    links = path_links(node, {concept.id for concept in concepts})
    ordered, depth, ignored = order_with_links(concepts, links)

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
        "[Learning path topic %s] saved %s steps, %s links shape the order "
        "(accepted %s, pending %s hidden, teacher-decided %s), %s moved by links",
        node.id, saved["steps"], saved["links"], link_counts["accepted"],
        link_counts["pending"], link_counts["teacher_decided"], saved["moved"],
    )
    return {**saved, **link_counts}
