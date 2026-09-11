"""Decide which grouped text becomes which version of a learning object.

A group's members are alternative presentations of one concept, written by
different teachers. Gemma chooses the baseline and assigns the remaining
versions. Readability evidence is retained for review, and a teacher can move
any source version after the automatic assignment.
"""

import logging
import hashlib

from django.conf import settings
from django.db import models, transaction

from .models import LessonVariant
from .readability import compare
from .version_classifier import VersionClassificationError, classify_group_versions


logger = logging.getLogger(__name__)

PRIMARY_SLOTS = ("SIMPLIFIED", "ELABORATED")


def choose_representative(members):
    """Return a stable display fallback before a grouped original is classified."""
    return sorted(
        members,
        key=lambda item: (item.material.created_at, item.material_id, item.id),
    )[0]


def assign_group_versions(group, *, use_llm=False):
    """Classify this group's source members and fill its version slots.

    When requested, Gemma's assignments are stored automatically. Calls that
    do not invoke the model expose unresolved members without guessing, so the
    review screen can wait for the teacher-triggered Generate all run.
    """
    members = [
        item
        for item in group.learning_objects.select_related("material").all()
        if (item.content or "").strip()
    ]
    if len(members) < 2:
        return {
            "representative_id": members[0].id if members else None,
            "original_selected": bool(members),
            "classification_complete": True,
            "assigned": [],
            "needs_confirmation": [],
            "extras": 0,
            "classification_error": "",
        }

    member_ids = {item.id for item in members}
    signature = hashlib.sha256("|".join(
        f"{item.id}:{item.content}" for item in sorted(members, key=lambda item: item.id)
    ).encode()).hexdigest()
    selection = group.version_selection or {}
    classification_complete = selection.get("classification_signature") == signature
    # Once source/generated slots have been stored, their owner is the durable
    # original. This prevents a later refresh from silently reshuffling the
    # learning path. Before that first classification, upload order is only a
    # UI fallback and is not treated as a version decision.
    stored_owner_id = (
        LessonVariant.objects.filter(learning_object_id__in=member_ids)
        .filter(models.Q(source_learning_object__isnull=False) | models.Q(assigned_by="teacher"))
        .order_by("-assigned_by", "id")
        .values_list("learning_object_id", flat=True)
        .first()
    )
    if stored_owner_id is None and selection.get("signature") == signature:
        stored_owner_id = selection.get("representative_id")
    representative = next(
        (item for item in members if item.id == stored_owner_id),
        None,
    )
    original_selected = representative is not None
    classifications = {}
    if classification_complete:
        try:
            classifications = {
                int(item_id): result
                for item_id, result in (selection.get("classification_assignments") or {}).items()
                if int(item_id) in member_ids
            }
        except (TypeError, ValueError):
            classifications = {}
            classification_complete = False
    classification_error = ""

    if use_llm and representative is None:
        try:
            classifications = classify_group_versions(members)
            original_id = next(
                item_id
                for item_id, result in classifications.items()
                if result["slot"] == "ORIGINAL"
            )
            representative = next(item for item in members if item.id == original_id)
            original_selected = True
            with transaction.atomic():
                # Remove only obsolete, unedited generated placements after a
                # successful new selection. Uploaded source objects remain.
                LessonVariant.objects.filter(
                    learning_object_id__in=member_ids,
                    origin=LessonVariant.Origin.GENERATED,
                ).exclude(assigned_by=LessonVariant.AssignedBy.TEACHER).exclude(
                    learning_object_id=representative.id,
                ).delete()
                group.version_selection = {
                    **selection,
                    "representative_id": original_id,
                    "signature": signature,
                }
                group.save(update_fields=["version_selection"])
        except (VersionClassificationError, StopIteration) as exc:
            classification_error = str(exc)
            logger.warning(
                "Content-version classification failed: group=%s model=%s error=%s",
                group.id,
                settings.CONTENT_VERSION_LLM_MODEL,
                exc,
            )

    if representative is None:
        representative = choose_representative(members)
    candidates = [item for item in members if item.id != representative.id]

    # A teacher decision (and an earlier confident automatic decision) is
    # durable.  This function is called whenever the review payload is read;
    # without consulting saved source rows, the same candidate is offered for
    # confirmation again immediately after the teacher clicks a button.
    stored_source_rows = list(
        LessonVariant.objects.filter(
            learning_object=representative,
            source_learning_object__isnull=False,
        ).order_by("id")
    )
    stored_by_source = {}
    for row in stored_source_rows:
        current = stored_by_source.get(row.source_learning_object_id)
        if current is None or (current.variant == "EXTRA" and row.variant in PRIMARY_SLOTS):
            stored_by_source[row.source_learning_object_id] = row

    unresolved = [item for item in candidates if item.id not in stored_by_source]
    if use_llm and unresolved and not classifications and original_selected:
        try:
            classifications = classify_group_versions(
                unresolved,
                representative=representative,
            )
        except VersionClassificationError as exc:
            classification_error = str(exc)
            logger.warning(
                "Content-version classification failed: group=%s model=%s error=%s",
                group.id,
                settings.CONTENT_VERSION_LLM_MODEL,
                exc,
            )

    proposals = []
    for candidate in candidates:
        verdict = compare(representative.content, candidate.content)
        llm = classifications.get(candidate.id)
        llm_slot = llm["slot"] if llm else None
        llm_confidence = llm["confidence"] if llm else None
        # Gemma owns the source-version classification. Readability remains
        # visible evidence for teacher review, but it no longer vetoes the
        # model's role assignment; teachers can move any source afterward.
        validated = bool(llm and llm_slot in (*PRIMARY_SLOTS, "EXTRA"))
        proposals.append({
            "learning_object_id": candidate.id,
            "object": candidate,
            "slot": llm_slot or verdict["slot"],
            "confident": validated,
            "llm_slot": llm_slot,
            "llm_confidence": llm_confidence,
            "llm_reason": llm["reason"] if llm else "",
            "readability_slot": verdict["slot"],
            "readability_confident": verdict["confident"],
            "delta_fk": verdict["delta_fk"],
            "delta_words": verdict["delta_words"],
            "ratio": verdict["ratio"],
        })

    assigned = []
    needs_confirmation = []
    extras = sum(row.variant == "EXTRA" for row in stored_source_rows)
    claimed = {
        row.variant: row.source_learning_object
        for row in stored_source_rows
        if row.variant in PRIMARY_SLOTS
    }

    # Strongest margin first, so a collision resolves in favour of the clearer
    # of the two claims rather than whichever happened to be ordered first.
    for proposal in sorted(proposals, key=lambda item: -item["delta_fk"]):
        stored = stored_by_source.get(proposal["learning_object_id"])
        if stored is not None:
            # Include persisted primary decisions in ``assigned`` so
            # settle_group() still marks their source objects represented.
            if stored.variant in PRIMARY_SLOTS:
                assigned.append({**_public(proposal), "slot": stored.variant, "persisted": True})
            continue
        if not proposal["confident"]:
            needs_confirmation.append(_public(proposal))
            continue
        slot = proposal["slot"]
        if slot == "EXTRA":
            _store(
                representative,
                proposal["object"],
                "EXTRA",
                assigned_by=LessonVariant.AssignedBy.LLM_VALIDATED,
            )
            extras += 1
            continue
        if slot in claimed:
            _store(
                representative,
                proposal["object"],
                "EXTRA",
                assigned_by=LessonVariant.AssignedBy.LLM_VALIDATED,
            )
            extras += 1
            continue
        claimed[slot] = proposal["object"]
        _store(
            representative,
            proposal["object"],
            slot,
            assigned_by=LessonVariant.AssignedBy.LLM_VALIDATED,
        )
        assigned.append(_public(proposal))

    if use_llm and settings.CONTENT_VERSION_LLM_ENABLED and not classification_error:
        # Persist both completion and the model evidence. A later GET can then
        # render only genuine review cases without making another slow LLM call.
        selection = group.version_selection or {}
        selection.update({
            "representative_id": representative.id,
            "signature": signature,
            "classification_signature": signature,
            "classification_assignments": {
                str(item_id): result for item_id, result in classifications.items()
            },
        })
        group.version_selection = selection
        group.save(update_fields=["version_selection"])
        classification_complete = True

    return {
        "representative_id": representative.id,
        "original_selected": original_selected,
        "classification_complete": classification_complete,
        "assigned": assigned,
        "needs_confirmation": needs_confirmation,
        "extras": extras,
        "classification_error": classification_error,
    }


@transaction.atomic
def assign_source_to_slot(representative, source, slot):
    """Persist one teacher decision without duplicating or losing source text."""
    if slot not in (*PRIMARY_SLOTS, "EXTRA"):
        raise ValueError("Unknown version slot")
    if source.id == representative.id:
        raise ValueError("The representative cannot be assigned as its own version")

    # Moving a source from one slot to another must remove its old placement.
    LessonVariant.objects.filter(
        learning_object=representative,
        source_learning_object=source,
    ).delete()

    if slot in PRIMARY_SLOTS:
        displaced = LessonVariant.objects.filter(
            learning_object=representative,
            variant=slot,
        ).select_related("source_learning_object").first()
        if displaced is not None:
            displaced_source = displaced.source_learning_object
            if displaced_source is not None and displaced_source.id != source.id:
                # The teacher's new choice wins the primary slot, but wording
                # from another PDF is retained as an extra rather than erased.
                LessonVariant.objects.create(
                    learning_object=representative,
                    variant="EXTRA",
                    narration=displaced.narration,
                    origin=displaced.origin,
                    source_learning_object=displaced_source,
                    assigned_by=displaced.assigned_by,
                )
            displaced.delete()

    row = LessonVariant.objects.create(
        learning_object=representative,
        variant=slot,
        narration=source.content,
        origin=LessonVariant.Origin.SOURCE_PDF,
        source_learning_object=source,
        assigned_by=LessonVariant.AssignedBy.TEACHER,
    )
    if source.represented_by_id != representative.id:
        source.represented_by = representative
        source.save(update_fields=["represented_by"])
    return row


@transaction.atomic
def assign_source_as_representative(group, source):
    """Make a source-PDF member Normal and swap the old Normal into its role."""
    state = assign_group_versions(group)
    representative_id = state["representative_id"]
    if representative_id is None:
        raise ValueError("This group has no Normal version")
    if source.group_id != group.id:
        raise ValueError("The selected source is not in this concept group")
    if source.id == representative_id:
        return source

    representative = group.learning_objects.get(pk=representative_id)
    selected_row = LessonVariant.objects.filter(
        learning_object=representative,
        source_learning_object=source,
    ).first()
    if selected_row is None:
        raise ValueError("Classify this PDF source before making it Normal")
    previous_slot = selected_row.variant

    # Generated wording was grounded in the old Normal and must be regenerated
    # against the new baseline. Teacher-edited generated wording is preserved.
    preserved_rows = list(
        LessonVariant.objects.filter(learning_object=representative)
        .exclude(pk=selected_row.pk)
        .exclude(
            origin=LessonVariant.Origin.GENERATED,
            assigned_by__in=(
                LessonVariant.AssignedBy.HEURISTIC,
                LessonVariant.AssignedBy.LLM_VALIDATED,
            ),
        )
    )
    LessonVariant.objects.filter(learning_object_id__in=[
        item.id for item in group.learning_objects.all()
    ]).delete()

    for row in preserved_rows:
        LessonVariant.objects.create(
            learning_object=source,
            variant=row.variant,
            narration=row.narration,
            audio_url=row.audio_url,
            source_fingerprint=row.source_fingerprint,
            generator_model=row.generator_model,
            generated_at=row.generated_at,
            origin=row.origin,
            source_learning_object=row.source_learning_object,
            assigned_by=row.assigned_by,
        )

    LessonVariant.objects.create(
        learning_object=source,
        variant=previous_slot,
        narration=representative.content,
        origin=LessonVariant.Origin.SOURCE_PDF,
        source_learning_object=representative,
        assigned_by=LessonVariant.AssignedBy.TEACHER,
    )

    group.learning_objects.exclude(pk=source.pk).update(represented_by=source)
    source.represented_by = None
    source.save(update_fields=["represented_by"])

    selection = group.version_selection or {}
    assignments = dict(selection.get("classification_assignments") or {})
    assignments[str(source.id)] = {
        "slot": "ORIGINAL",
        "confidence": 1.0,
        "reason": "Teacher selected as Normal.",
    }
    assignments[str(representative.id)] = {
        "slot": previous_slot,
        "confidence": 1.0,
        "reason": "Moved from Normal when the teacher selected a new baseline.",
    }
    selection.update({
        "representative_id": source.id,
        "classification_assignments": assignments,
    })
    group.version_selection = selection
    group.save(update_fields=["version_selection"])
    return source


def _public(proposal):
    return {key: value for key, value in proposal.items() if key != "object"}


def _store(
    representative,
    source,
    slot,
    *,
    assigned_by=LessonVariant.AssignedBy.HEURISTIC,
):
    if slot == "EXTRA":
        LessonVariant.objects.update_or_create(
            learning_object=representative,
            variant="EXTRA",
            source_learning_object=source,
            defaults={
                "narration": source.content,
                "origin": LessonVariant.Origin.SOURCE_PDF,
                "assigned_by": assigned_by,
            },
        )
        return
    LessonVariant.objects.update_or_create(
        learning_object=representative,
        variant=slot,
        defaults={
            "narration": source.content,
            "audio_url": "",
            "source_fingerprint": "",
            "generator_model": "",
            "origin": LessonVariant.Origin.SOURCE_PDF,
            "source_learning_object": source,
            "assigned_by": assigned_by,
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

    outcome = assign_group_versions(group, use_llm=True)
    if outcome["representative_id"] is None:
        return {**outcome, "generated": [], "errors": []}
    if not outcome.get("original_selected"):
        detail = outcome.get("classification_error") or "Gemma did not select an original version."
        return {
            **outcome,
            "generated": [],
            "errors": [{"learning_object_id": None, "detail": detail}],
        }

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
