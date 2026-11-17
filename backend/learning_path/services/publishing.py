"""Save a topic's learning path: its prerequisite links and its step order."""

import heapq
import logging
from collections import Counter, defaultdict

from django.db import transaction
from django.utils import timezone

from ..models import ConceptPrerequisite, LearningPathStep
from . import criteria, tracing
from .concept_units import concepts_for_topic
from .fusion import ACCEPTED, PENDING

logger = logging.getLogger(__name__)


class LoopError(ValueError):
    """The links form a loop, which saving and approving links never allow."""


def _reaches(successors, start, goal):
    """True when goal can be reached from start by following links."""
    seen, waiting = set(), [start]
    while waiting:
        current = waiting.pop()
        if current == goal:
            return True
        if current not in seen:
            seen.add(current)
            waiting.extend(successors[current])
    return False


def hold_loop_links(decisions, fixed_links=()):
    """Make each accepted link that would close a loop pending; links against the document order are held first."""
    successors = defaultdict(set)
    for before, after in fixed_links:
        successors[before].add(after)
    accepted = sorted(
        (decision for decision in decisions if decision["verdict"] == ACCEPTED),
        key=lambda decision: (
            decision["prerequisite"].order > decision["dependent"].order,
            decision["prerequisite"].order,
            decision["dependent"].order,
        ),
    )
    held = []
    for decision in accepted:
        before, after = decision["prerequisite"].id, decision["dependent"].id
        if _reaches(successors, after, before):
            decision["verdict"] = PENDING
            decision["evidence"]["contradictions"].append("loop")
            held.append(decision)
        else:
            successors[before].add(after)
    return held


def _stored_links(node):
    """{(prerequisite id, dependent id): row} for the topic's stored links, locked."""
    return {
        (row.prerequisite_id, row.dependent_id): row
        for row in ConceptPrerequisite.objects.select_for_update().filter(outline_node=node)
    }


def refresh_prerequisites(node, concepts=None):
    """Re-derive this topic's links, keeping every teacher decision."""
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    decisions = criteria.decide_pairs(concepts)

    fresh = {}
    for decision in decisions:
        pair = (decision["prerequisite"].id, decision["dependent"].id)
        fresh[pair] = decision

    written = Counter()
    with transaction.atomic():
        existing = _stored_links(node)
        decided_pairs = {
            pair for pair, row in existing.items()
            if row.status in ConceptPrerequisite.TEACHER_DECIDED
        }
        approved = [
            pair for pair, row in existing.items()
            if row.status == ConceptPrerequisite.Status.APPROVED
        ]
        hold_loop_links([decision for pair, decision in fresh.items() if pair not in decided_pairs], approved)

        for pair, decision in fresh.items():
            row = existing.get(pair)
            if row is None:
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
                    written["new"] += 1
                    continue
            same_evidence = (
                row.evidence == decision["evidence"] and row.cross_section == decision["cross_section"]
            )
            if row.status in ConceptPrerequisite.TEACHER_DECIDED:
                written["unchanged" if same_evidence else "updated"] += 1
                if not same_evidence:
                    row.evidence = decision["evidence"]
                    row.cross_section = decision["cross_section"]
                    row.save(update_fields=["evidence", "cross_section", "updated_at"])
                continue
            if (
                same_evidence
                and row.status == decision["verdict"]
                and row.source == ConceptPrerequisite.Source.DERIVED
            ):
                written["unchanged"] += 1
                continue
            ConceptPrerequisite.objects.filter(pk=row.pk).exclude(
                status__in=ConceptPrerequisite.TEACHER_DECIDED,
            ).update(
                status=decision["verdict"],
                source=ConceptPrerequisite.Source.DERIVED,
                cross_section=decision["cross_section"],
                evidence=decision["evidence"],
                updated_at=timezone.now(),
            )
            written["updated"] += 1

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
    logger.info(
        "[Learning path] topic %s  links: %s accepted, %s pending, %s set by the teacher | "
        "saved: %s new, %s updated, %s unchanged, %s removed",
        node.id, counts["accepted"], counts["pending"], counts["teacher_decided"],
        written["new"], written["updated"], written["unchanged"], len(stale),
    )
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


def order_with_links(concepts, links):
    """Kahn's sort over the links; returns (learning path, depth by id)."""
    position = {concept.id: index for index, concept in enumerate(concepts)}
    kept = {(before, after) for before, after in links if before in position and after in position}
    successors = {concept.id: set() for concept in concepts}
    in_degree = {concept.id: 0 for concept in concepts}
    for before, after in kept:
        successors[before].add(after)
        in_degree[after] += 1

    queue = [(position[concept_id], concept_id) for concept_id, count in in_degree.items() if count == 0]
    heapq.heapify(queue)

    by_id = {concept.id: concept for concept in concepts}
    learning_path = []
    while queue:
        _, current = heapq.heappop(queue)
        learning_path.append(by_id[current])
        for following in successors[current]:
            in_degree[following] -= 1
            if in_degree[following] == 0:
                heapq.heappush(queue, (position[following], following))
    if len(learning_path) < len(concepts):
        raise LoopError("The topic's prerequisite links form a loop.")

    depth = {concept.id: 0 for concept in learning_path}
    for concept in learning_path:
        for following in successors[concept.id]:
            depth[following] = max(depth[following], depth[concept.id] + 1)
    tracing.log_order(learning_path, depth, kept)
    return learning_path, depth


@transaction.atomic
def save_learning_path(node, concepts=None):
    """Replace the topic's saved steps with the current order."""
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    concept_ids = {concept.id for concept in concepts}
    links = path_links(node, concept_ids)
    ordered, depth = order_with_links(concepts, links)

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
        "published_at": now.isoformat(),
    }


def publish_learning_path(node):
    """Derive the links, then save the path. The publish pipeline's single entry."""
    concepts = concepts_for_topic(node)
    link_counts = refresh_prerequisites(node, concepts)
    saved = save_learning_path(node, concepts)
    logger.info(
        "[Publish] topic %s  learning path saved: %s steps, %s prerequisite links "
        "(%s accepted automatically, %s set by the teacher, %s suggestions waiting), "
        "%s concept(s) reordered by links",
        node.id, saved["steps"], saved["links"], link_counts["accepted"],
        link_counts["teacher_decided"], link_counts["pending"], saved["moved"],
    )
    return {**saved, **link_counts}
