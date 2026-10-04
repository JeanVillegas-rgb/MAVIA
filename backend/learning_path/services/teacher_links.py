"""A teacher adding, approving or removing prerequisite links on the review screen.

Three rules, agreed for this screen:

* **Changes reach students only at the next publish.** They are stored at once
  and the review preview follows them immediately, but the saved path is
  replaced only by a successful publish.
* **Removing a link rejects it.** A rejected pair is never proposed again by the
  criteria; a teacher can still add it back by hand.
* **A change that would create a loop is refused**, naming the concepts caught
  in it, so the path can always be produced.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lessons.models import LearningObjectGroup

from ..models import ConceptPrerequisite, LearningPathStep


class LinkError(ValueError):
    """A teacher change that cannot be applied, with a message fit to show them."""


def _group(node, concept_id):
    group = LearningObjectGroup.objects.filter(pk=concept_id, outline_node=node).first()
    if group is None:
        raise LinkError("That concept is not part of this topic.")
    return group


def _loop_through(node, prerequisite, dependent, ignore_ids=()):
    """Concept ids forming a loop if ``prerequisite -> dependent`` were added, else None."""
    successors = {}
    rows = ConceptPrerequisite.objects.filter(
        outline_node=node, status__in=ConceptPrerequisite.SHAPES_PATH,
    ).exclude(pk__in=list(ignore_ids))
    for row in rows:
        successors.setdefault(row.prerequisite_id, []).append(row.dependent_id)

    # A loop exists if the dependent already leads back to the prerequisite.
    trail = {dependent.id: None}
    stack = [dependent.id]
    while stack:
        current = stack.pop()
        if current == prerequisite.id:
            chain, step = [], current
            while step is not None:
                chain.append(step)
                step = trail[step]
            return list(reversed(chain))
        for following in successors.get(current, []):
            if following not in trail:
                trail[following] = current
                stack.append(following)
    return None


def _refuse_loop(node, prerequisite, dependent, ignore_ids=()):
    chain = _loop_through(node, prerequisite, dependent, ignore_ids)
    if chain:
        labels = {
            group.id: group.learning_objects.order_by("material_id", "order").values_list("title", flat=True).first()
            or group.label
            for group in LearningObjectGroup.objects.filter(pk__in=chain)
        }
        # The new link runs prerequisite -> dependent; the chain already runs
        # dependent -> ... -> prerequisite, which closes the loop.
        route = " → ".join(labels[cid] for cid in [prerequisite.id, *chain])
        raise LinkError(
            f"That would make a loop: {route}. Remove one of those links first."
        )


def _snapshot(node, pairs):
    """The state of each pair before a change: what Undo puts back.

    ``prior`` is None when the pair had no row.
    """
    rows = {
        (row.prerequisite_id, row.dependent_id): row
        for row in ConceptPrerequisite.objects.filter(outline_node=node)
    }
    records = []
    for prerequisite_id, dependent_id in pairs:
        row = rows.get((prerequisite_id, dependent_id))
        records.append({
            "prerequisite_id": prerequisite_id,
            "dependent_id": dependent_id,
            "prior": None if row is None else {
                "status": row.status,
                "source": row.source,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            },
        })
    return records


def _has_loop(node):
    """True when the topic's path-shaping links contain a cycle (Kahn)."""
    successors, indegree = {}, {}
    for row in ConceptPrerequisite.objects.filter(outline_node=node, status__in=ConceptPrerequisite.SHAPES_PATH):
        successors.setdefault(row.prerequisite_id, []).append(row.dependent_id)
        indegree[row.dependent_id] = indegree.get(row.dependent_id, 0) + 1
        indegree.setdefault(row.prerequisite_id, 0)
    ready = [concept_id for concept_id, count in indegree.items() if count == 0]
    seen = 0
    while ready:
        current = ready.pop()
        seen += 1
        for following in successors.get(current, []):
            indegree[following] -= 1
            if indegree[following] == 0:
                ready.append(following)
    return seen < len(indegree)


def add_link(node, prerequisite_id, dependent_id):
    """``dependent`` needs ``prerequisite`` first, by a teacher's decision. Returns the undo record."""
    prerequisite = _group(node, prerequisite_id)
    dependent = _group(node, dependent_id)
    if prerequisite.id == dependent.id:
        raise LinkError("A concept cannot be its own prerequisite.")

    existing = ConceptPrerequisite.objects.filter(prerequisite=prerequisite, dependent=dependent).first()
    _refuse_loop(node, prerequisite, dependent, ignore_ids=[existing.id] if existing else [])
    undo = _snapshot(node, [(prerequisite.id, dependent.id)])

    ConceptPrerequisite.objects.update_or_create(
        prerequisite=prerequisite,
        dependent=dependent,
        defaults={
            "outline_node": node,
            "status": ConceptPrerequisite.Status.APPROVED,
            "source": ConceptPrerequisite.Source.TEACHER,
            "decided_at": timezone.now(),
        },
    )
    return undo


def decide_link(node, link_id, status):
    """Approve a suggestion, or reject (remove) any link. Returns the undo record."""
    if status not in ConceptPrerequisite.TEACHER_DECIDED:
        raise LinkError("Choose approve or reject.")
    row = ConceptPrerequisite.objects.filter(pk=link_id, outline_node=node).select_related(
        "prerequisite", "dependent",
    ).first()
    if row is None:
        raise LinkError("That link no longer exists. Refresh the page.")
    if status == ConceptPrerequisite.Status.APPROVED:
        _refuse_loop(node, row.prerequisite, row.dependent, ignore_ids=[row.id])
    undo = _snapshot(node, [(row.prerequisite_id, row.dependent_id)])

    row.status = status
    row.source = ConceptPrerequisite.Source.TEACHER
    row.decided_at = timezone.now()
    row.save(update_fields=["status", "source", "decided_at", "updated_at"])
    return undo


@transaction.atomic
def move_link(node, prerequisite_id, dependent_id):
    """``dependent`` needs ``prerequisite`` first, and nothing it needed before.

    Every other link shaping the path into ``dependent`` is rejected, and
    ``prerequisite -> dependent`` is approved, in one step. Returns the undo
    record covering both halves.
    """
    prerequisite = _group(node, prerequisite_id)
    dependent = _group(node, dependent_id)
    if prerequisite.id == dependent.id:
        raise LinkError("A concept cannot be its own prerequisite.")

    replaced = list(
        ConceptPrerequisite.objects.filter(
            outline_node=node, dependent=dependent, status__in=ConceptPrerequisite.SHAPES_PATH,
        ).exclude(prerequisite=prerequisite)
    )
    existing = ConceptPrerequisite.objects.filter(prerequisite=prerequisite, dependent=dependent).first()
    ignore = [row.id for row in replaced] + ([existing.id] if existing else [])
    _refuse_loop(node, prerequisite, dependent, ignore_ids=ignore)
    undo = _snapshot(
        node,
        [(row.prerequisite_id, row.dependent_id) for row in replaced] + [(prerequisite.id, dependent.id)],
    )

    now = timezone.now()
    for row in replaced:
        row.status = ConceptPrerequisite.Status.REJECTED
        row.source = ConceptPrerequisite.Source.TEACHER
        row.decided_at = now
        row.save(update_fields=["status", "source", "decided_at", "updated_at"])
    ConceptPrerequisite.objects.update_or_create(
        prerequisite=prerequisite,
        dependent=dependent,
        defaults={
            "outline_node": node,
            "status": ConceptPrerequisite.Status.APPROVED,
            "source": ConceptPrerequisite.Source.TEACHER,
            "decided_at": now,
        },
    )
    return undo


def changed_since_publish(node):
    """True when a teacher decided a link after the path was last saved."""
    step = LearningPathStep.objects.filter(outline_node=node).order_by("-published_at").first()
    if step is None:
        return False
    return ConceptPrerequisite.objects.filter(
        outline_node=node, decided_at__gt=step.published_at,
    ).exists()


_INVALID_UNDO = "That undo is not valid. Refresh the page."


@transaction.atomic
def restore_links(node, records):
    """Put each pair back exactly as an undo record says it was.

    A pair with ``prior: None`` had no row, so its row is deleted; any other
    pair gets its status, source and decision time back, re-created if the row
    was deleted meanwhile. Refused, with nothing changed, if the result loops.
    """
    if not isinstance(records, list) or not records:
        raise LinkError("Nothing to undo.")
    for record in records:
        try:
            prerequisite_id = int(record["prerequisite_id"])
            dependent_id = int(record["dependent_id"])
            prior = record["prior"]
        except (KeyError, TypeError, ValueError):
            raise LinkError(_INVALID_UNDO)
        prerequisite = _group(node, prerequisite_id)
        dependent = _group(node, dependent_id)

        if prior is None:
            ConceptPrerequisite.objects.filter(prerequisite=prerequisite, dependent=dependent).delete()
            continue
        if (
            not isinstance(prior, dict)
            or prior.get("status") not in ConceptPrerequisite.Status.values
            or prior.get("source") not in ConceptPrerequisite.Source.values
        ):
            raise LinkError(_INVALID_UNDO)
        ConceptPrerequisite.objects.update_or_create(
            prerequisite=prerequisite,
            dependent=dependent,
            defaults={
                "outline_node": node,
                "status": prior["status"],
                "source": prior["source"],
                "decided_at": parse_datetime(prior["decided_at"]) if prior.get("decided_at") else None,
            },
        )
    if _has_loop(node):
        raise LinkError("Undoing that would make a loop. Refresh the page to see the current path.")
