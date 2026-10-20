"""Save a topic's learning path: its prerequisite links and its step order."""

import logging
import math
from collections import Counter, defaultdict

from django.db import transaction
from django.utils import timezone

from ..models import ConceptPrerequisite, LearningPathStep
from . import criteria, tracing
from .concept_units import concepts_for_topic

logger = logging.getLogger(__name__)


def _stored_links(node):
    """{(prerequisite id, dependent id): row} for the topic's stored links, locked."""
    return {
        (row.prerequisite_id, row.dependent_id): row
        for row in ConceptPrerequisite.objects.select_for_update().filter(outline_node=node)
    }


def refresh_prerequisites(node, concepts=None, runtime_instance=None):
    """Re-derive this topic's links, keeping every teacher decision."""
    concepts = list(concepts if concepts is not None else concepts_for_topic(node))
    decisions = criteria.decide_pairs(concepts, runtime_instance)

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


def _find_cycle(links):
    """The links of one loop, or None when there is no loop."""
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
    """Drop the least confident derived link of each loop until none is left."""
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


def order_with_links(concepts, links, confidence=None):
    """Kahn's sort over the links; returns (ordered, depth by id, ignored links)."""
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

    by_id = {concept.id: concept for concept in concepts}
    ordered = []
    while len(ordered) < len(concepts):
        ready = [concept_id for concept_id, count in remaining.items() if count == 0 and concept_id not in placed_at]
        chosen = min(ready, key=position.get)
        placed_at[chosen] = len(ordered)
        ordered.append(by_id[chosen])
        for following in successors[chosen]:
            remaining[following] -= 1

    depth = {concept.id: 0 for concept in ordered}
    for concept in ordered:
        for following in successors[concept.id]:
            depth[following] = max(depth[following], depth[concept.id] + 1)
    tracing.log_order(ordered, depth, ignored)
    return ordered, depth, ignored


def path_link_confidence(node, concept_ids):
    """Confidence of each derived link; approved links are absent, so never broken first."""
    return {
        (row.prerequisite_id, row.dependent_id): float((row.evidence or {}).get("confidence", 0.0))
        for row in ConceptPrerequisite.objects.filter(
            outline_node=node, status=ConceptPrerequisite.Status.ACCEPTED,
        )
        if row.prerequisite_id in concept_ids and row.dependent_id in concept_ids
    }


@transaction.atomic
def save_learning_path(node, concepts=None):
    """Replace the topic's saved steps with the current order."""
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
    return {**saved, **link_counts}
