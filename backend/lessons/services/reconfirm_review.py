"""What confirming an edited PDF again would change in the approved PDFs.

While a confirmed PDF is edited it is a draft and leaves the approved PDFs
alone. Its effects are worked out when it is confirmed again, and some of
them land on approved PDFs: a part another PDF no longer teaches is folded
into its section, a folded part another PDF now teaches is separated, a
stand-alone concept is matched into another. Those are the teacher's to
accept, so confirming is first run as a rehearsal and rolled back.
"""

import logging

from django.db import transaction

from ..models import LearningObject, LearningObjectGroup
from .concept_bundles import bundle_label
from .learning_resource_linker import record_teacher_match_decision

logger = logging.getLogger(__name__)

FIRST_CONFIRMED_KEY = "first_confirmed_at"


class _Rehearsal(Exception):
    """Raised to roll the rehearsal back once its outcome is read."""


def was_confirmed_before(material):
    return bool((material.generated_json or {}).get(FIRST_CONFIRMED_KEY))


def _approved_objects(material):
    """Objects of the other confirmed PDFs in this topic."""
    return LearningObject.objects.filter(
        material__outline_node_id=material.outline_node_id,
        material__generated_json__learning_objects_confirmed=True,
    ).exclude(material_id=material.id)


def approved_placements(material):
    return dict(_approved_objects(material).values_list("id", "group_id"))


def _concept_name(group_id):
    if group_id is None:
        return "a concept of its own"
    members = list(LearningObject.objects.filter(group_id=group_id).order_by("order", "id"))
    if len(members) <= 1:
        return "a concept of its own"
    group = LearningObjectGroup.objects.filter(pk=group_id).first()
    return (group.label if group and group.label else bundle_label(members)) or "another concept"


def rehearse(material, confirm):
    """Run ``confirm()``; undo it only if it would move approved objects.

    Returns the moves it found, each naming the object, its PDF, and the
    concept it would leave and join. An empty list means nothing approved
    moved, and the confirmation is kept as it stands -- running it a second
    time would only repeat the same work.
    """
    before = approved_placements(material)
    names_before = {group_id: _concept_name(group_id) for group_id in set(before.values())}
    changes = []
    logger.info(
        "[Reconfirm] PDF %s  checking whether confirming it again would move parts of approved PDFs",
        material.id,
    )
    try:
        with transaction.atomic():
            confirm()
            after = approved_placements(material)
            for item in _approved_objects(material).select_related("material").order_by("material_id", "order"):
                if item.id not in before or before[item.id] == after.get(item.id):
                    continue
                changes.append({
                    "learning_object_id": item.id,
                    "title": item.title,
                    "material_title": item.material.title,
                    "from_label": names_before.get(before[item.id], "a concept of its own"),
                    "to_label": _concept_name(after.get(item.id)),
                })
            if changes:
                raise _Rehearsal
    except _Rehearsal:
        logger.info(
            "[Reconfirm] PDF %s  %s approved part(s) would move; the grouping lines above were undone "
            "and nothing is applied until the teacher decides",
            material.id, len(changes),
        )
    else:
        logger.info("[Reconfirm] PDF %s  no approved part moves; confirmed", material.id)
    material.refresh_from_db()
    return changes


def keep_in_place(material, kept_ids, before):
    """Put the approved objects the teacher kept back where they were.

    Each is recorded so the next automatic pass does not redo the move: kept
    out of its section, it is not folded again; kept out of a cross-PDF
    match, the pair counts as declined.
    """
    for item in LearningObject.objects.filter(id__in=kept_ids).select_related("material"):
        old_group_id = before.get(item.id)
        if old_group_id is None or item.group_id == old_group_id:
            continue
        companions = [
            other for other in LearningObject.objects.filter(group_id=item.group_id).exclude(pk=item.pk)
            if other.material_id != item.material_id
        ]
        if not LearningObjectGroup.objects.filter(pk=old_group_id).exists():
            # It was alone and its group was cleared away once it moved.
            old_group_id = LearningObjectGroup.objects.create(
                outline_node_id=item.material.outline_node_id,
                label=bundle_label([item])[:255],
            ).id
        item.group_id = old_group_id
        item.kept_apart_from_section = True
        item.save(update_fields=["group", "kept_apart_from_section"])
        for other in companions:
            record_teacher_match_decision(item, other, accepted=False)
