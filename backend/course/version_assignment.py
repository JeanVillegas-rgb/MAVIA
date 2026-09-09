"""Decide which grouped text becomes which version of a learning object.

A group's members are alternative presentations of one concept, written by
different teachers. The earliest upload is the original; the rest are ranked
against it. Nothing here calls a language model -- this module only distributes
text that already exists.
"""

import logging

from .models import LessonVariant
from .readability import compare


logger = logging.getLogger(__name__)

PRIMARY_SLOTS = ("SIMPLIFIED", "ELABORATED")


def choose_representative(members):
    """The earliest-uploaded member. Ties break on material id, then object id.

    Deterministic ordering matters: the representative decides which object
    survives as a teaching step, and a reshuffle on every refresh would churn
    question links and the prerequisite graph.
    """
    return sorted(
        members,
        key=lambda item: (item.material.created_at, item.material_id, item.id),
    )[0]


def assign_group_versions(group):
    """Fill this group's primary slots from its own members.

    Confident comparisons are written immediately. Unconfident ones are
    returned for a teacher to settle and are deliberately NOT stored: an
    unreviewed guess in the database is indistinguishable from a decision.
    """
    members = [
        item
        for item in group.learning_objects.select_related("material").all()
        if (item.content or "").strip()
    ]
    if len(members) < 2:
        return {
            "representative_id": members[0].id if members else None,
            "assigned": [],
            "needs_confirmation": [],
            "extras": 0,
        }

    representative = choose_representative(members)
    candidates = [item for item in members if item.id != representative.id]

    proposals = []
    for candidate in candidates:
        verdict = compare(representative.content, candidate.content)
        proposals.append({
            "learning_object_id": candidate.id,
            "object": candidate,
            "slot": verdict["slot"],
            "confident": verdict["confident"],
            "delta_fk": verdict["delta_fk"],
            "delta_words": verdict["delta_words"],
            "ratio": verdict["ratio"],
        })

    assigned = []
    needs_confirmation = []
    extras = 0
    claimed = {}

    # Strongest margin first, so a collision resolves in favour of the clearer
    # of the two claims rather than whichever happened to be ordered first.
    for proposal in sorted(proposals, key=lambda item: -item["delta_fk"]):
        if not proposal["confident"]:
            needs_confirmation.append(_public(proposal))
            continue
        slot = proposal["slot"]
        if slot in claimed:
            _store(representative, proposal["object"], "EXTRA")
            extras += 1
            continue
        claimed[slot] = proposal["object"]
        _store(representative, proposal["object"], slot)
        assigned.append(_public(proposal))

    return {
        "representative_id": representative.id,
        "assigned": assigned,
        "needs_confirmation": needs_confirmation,
        "extras": extras,
    }


def _public(proposal):
    return {key: value for key, value in proposal.items() if key != "object"}


def _store(representative, source, slot):
    if slot == "EXTRA":
        LessonVariant.objects.update_or_create(
            learning_object=representative,
            variant="EXTRA",
            source_learning_object=source,
            defaults={
                "narration": source.content,
                "origin": LessonVariant.Origin.SOURCE_PDF,
                "assigned_by": LessonVariant.AssignedBy.HEURISTIC,
            },
        )
        return
    LessonVariant.objects.update_or_create(
        learning_object=representative,
        variant=slot,
        defaults={
            "narration": source.content,
            "origin": LessonVariant.Origin.SOURCE_PDF,
            "source_learning_object": source,
            "assigned_by": LessonVariant.AssignedBy.HEURISTIC,
        },
    )
