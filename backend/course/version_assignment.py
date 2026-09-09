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


def settle_group(group):
    """Distribute real text, fill what is missing, then collapse the group.

    Flagging happens last and only for members whose text was actually placed.
    A member awaiting teacher confirmation stays a teaching step: until someone
    decides where its text belongs, removing it from the lesson would drop
    content rather than deduplicate it.
    """
    from .variant_generator import fill_missing_slots

    outcome = assign_group_versions(group)
    if outcome["representative_id"] is None:
        return {**outcome, "generated": [], "errors": []}

    members = {item.id: item for item in group.learning_objects.select_related("material")}
    representative = members[outcome["representative_id"]]

    filled = fill_missing_slots(representative)

    placed_ids = {entry["learning_object_id"] for entry in outcome["assigned"]}
    placed_ids |= set(
        LessonVariant.objects.filter(
            learning_object=representative,
            variant="EXTRA",
        ).values_list("source_learning_object_id", flat=True)
    )
    for member_id in placed_ids:
        member = members.get(member_id)
        if member is not None and member.represented_by_id != representative.id:
            member.represented_by = representative
            member.save(update_fields=["represented_by"])

    return {
        **outcome,
        "generated": filled["generated"],
        "errors": filled["errors"],
    }


def release_learning_object(learning_object):
    """Undo representation for one object after a teacher ungroups it.

    Generated rows on the representative are dropped because the object that
    justified them is leaving. Source rows are never dropped -- a teacher wrote
    that text, and regenerating it is not possible.
    """
    # Read the flag from the database rather than the passed-in instance:
    # settle_group() writes it through a separately loaded object, so a caller
    # holding a reference from before that call still sees represented_by as
    # None and would silently skip the release.
    current = (
        type(learning_object)
        .objects.select_related("represented_by")
        .filter(pk=learning_object.pk)
        .first()
    )
    if current is None or current.represented_by is None:
        return
    representative = current.represented_by

    LessonVariant.objects.filter(
        learning_object=representative,
        source_learning_object=current,
    ).delete()
    LessonVariant.objects.filter(
        learning_object=representative,
        origin=LessonVariant.Origin.GENERATED,
    ).delete()

    current.represented_by = None
    current.save(update_fields=["represented_by"])
    # Keep the caller's instance consistent with what was just written.
    learning_object.represented_by = None
