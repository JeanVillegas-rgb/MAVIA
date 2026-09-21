"""Decide which grouped text becomes which version of a learning object.

A group's members are alternative presentations of one concept, written by
different teachers. Two PDFs rarely chunk a lesson at the same grain, so a
concept holds a *bundle* per PDF -- all of that file's objects for the concept,
in document order -- and a role is decided for the whole bundle rather than for
each object. One bundle is Normal; each other bundle is Simplified, Elaborated
or an Extra.

A version supplied by a PDF is its own objects: nothing is copied. Only
generated wording is stored as a ``LessonVariant``. The roles themselves live in
``group.version_selection`` so a re-run can never duplicate a teacher's text,
and a role a teacher set is never overwritten.
"""

import logging
import hashlib
import re
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from lessons.services.concept_bundles import (
    bundle_heading,
    bundle_label,
    bundle_lead,
    bundle_text,
    bundles_for_group,
    material_order,
)

from .models import LessonVariant
from .readability import compare
from .version_classifier import VersionClassificationError, classify_group_versions


logger = logging.getLogger(__name__)

PRIMARY_SLOTS = ("SIMPLIFIED", "ELABORATED")
ROLES = (*PRIMARY_SLOTS, "EXTRA")

# Provenance for a bundle pushed out of a primary slot by a teacher's newer
# choice. It is deliberately not ``teacher``: the teacher chose the winner, not
# this bundle's consolation role, so the record shows it was displaced and the
# role stays open to reclassification.
DISPLACED_BY_TEACHER = "displaced_by_teacher"


def _displaced_provenance(current):
    """What a bundle's provenance becomes when another claim takes its slot."""
    if current == LessonVariant.AssignedBy.TEACHER:
        return DISPLACED_BY_TEACHER
    return current or LessonVariant.AssignedBy.HEURISTIC


_PART_SUFFIX = re.compile(
    r"\s*[\[(]?\s*part\s+\d+\s*(?:of|/)\s*\d+\s*[\])]?\s*$",
    re.IGNORECASE,
)
_LEADING_SECTION_NUMBER = re.compile(
    r"^\s*(?:(?:unit|lesson|chapter|section|topic)\s+)?"
    r"\d+(?:\.\d+)*(?:\s*[.):-]\s*|\s+)",
    re.IGNORECASE,
)


def clean_group_label(title):
    """Remove extraction structure without rewriting the teacher's wording."""
    original = (title or "").strip()
    cleaned = _LEADING_SECTION_NUMBER.sub("", original)
    cleaned = _PART_SUFFIX.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:;,.()[]")
    return cleaned or original


def _sync_automatic_group_label(group, representative, members=None, heading=None):
    """Name an unlocked concept after its Normal (or temporary fallback).

    ``heading`` is the Normal bundle's heading. A bundle often opens with a
    figure whose own title names nothing, so the bundle's heading -- not the
    lead object's title -- is what the concept is called.
    """
    selection = group.version_selection or {}
    if representative is None or selection.get("label_locked"):
        return
    existing = (group.label or "").strip()
    written = (selection.get("auto_label") or "").strip()
    if existing and existing.casefold() != written.casefold():
        if written or not _looks_automatic(existing, members, group):
            # Either this label is not the one this function last wrote, or --
            # for a concept labelled before that was recorded -- it is unlike
            # any member's title. Both mean a teacher named it.
            group.version_selection = {**selection, "label_locked": True}
            group.save(update_fields=["version_selection"])
            return
    label = clean_group_label(heading or representative.title)[:255]
    if not label:
        return
    if group.label != label:
        group.label = label
        group.save(update_fields=["label"])
    if written != label:
        group.version_selection = {**selection, "auto_label": label}
        group.save(update_fields=["version_selection"])


def _looks_automatic(existing, members, group):
    """Was a label written before ``auto_label`` was recorded an automatic one?

    Only a member's own title was ever used, so a label matching one is the
    pipeline's; anything else was typed by a teacher. Section headings are not
    candidates: a teacher naming a concept after its heading is still a
    teacher, and treating that as automatic would overwrite their wording.
    """
    group_members = list(members) if members is not None else list(
        group.learning_objects.only("title")
    )
    automatic_labels = {
        candidate.casefold()
        for item in group_members
        for candidate in ((item.title or "").strip(), clean_group_label(item.title))
        if candidate
    }
    return existing.casefold() in automatic_labels


def choose_representative(members):
    """Return a stable display fallback before a grouped original is classified."""
    return sorted(
        members,
        key=lambda item: (item.material.created_at, item.material_id, item.id),
    )[0]


# ── Bundles and their roles ──


def _eligible_bundles(group):
    """``{material_id: [objects]}``, dropping empty text and empty bundles."""
    bundles = {}
    for material_id, objects in bundles_for_group(group).items():
        kept = [item for item in objects if (item.content or "").strip()]
        if kept:
            bundles[material_id] = kept
    return bundles


def _ordered_bundle_ids(group, bundles):
    """The concept's material ids in upload order.

    A material whose topic is not this concept's -- possible after a move --
    has no place in ``material_order``; it sorts last by its own upload time
    rather than disappearing from the concept.
    """
    ranked = {
        material_id: rank
        for rank, material_id in enumerate(material_order(group.outline_node))
    }
    return sorted(
        bundles,
        key=lambda material_id: (
            ranked.get(material_id, len(ranked)),
            bundles[material_id][0].material.created_at,
            material_id,
        ),
    )


def _stored_roles(selection):
    roles = {}
    for key, value in (selection.get("bundle_roles") or {}).items():
        try:
            roles[int(key)] = value
        except (TypeError, ValueError):
            continue
    return roles


def _stored_provenance(selection):
    provenance = {}
    for key, value in (selection.get("bundle_roles_assigned_by") or {}).items():
        try:
            provenance[int(key)] = value
        except (TypeError, ValueError):
            continue
    return provenance


def _normal_id(group, bundles):
    selection = group.version_selection or {}
    stored = selection.get("normal_material_id")
    if stored in bundles:
        return stored
    order = _ordered_bundle_ids(group, bundles)
    return order[0] if order else None


def normal_material_id(group):
    """Which PDF supplies this concept's Normal version, if one is decided.

    Falls back to the earliest upload so the review screen always has a
    baseline to show; only a classification or a teacher makes it durable.
    """
    return _normal_id(group, _eligible_bundles(group))


def bundle_roles(group):
    """``{material_id: role}`` for every bundle other than Normal."""
    bundles = _eligible_bundles(group)
    normal_id = _normal_id(group, bundles)
    return {
        material_id: role
        for material_id, role in _stored_roles(group.version_selection or {}).items()
        if material_id in bundles and material_id != normal_id and role in ROLES
    }


def bundle_role_provenance(group):
    """``{material_id: who decided its role}`` for this concept's bundles.

    The value is usually an ``AssignedBy`` choice, but a bundle pushed out of a
    primary slot carries ``displaced_by_teacher`` instead, so callers that show
    it must have wording for that case too.
    """
    bundles = _eligible_bundles(group)
    return {
        material_id: who
        for material_id, who in _stored_provenance(group.version_selection or {}).items()
        if material_id in bundles
    }


def set_bundle_role(group, material_id, role, assigned_by=None):
    """Record one bundle's role, by default as the teacher's own decision.

    ``assigned_by`` is explicit so that displacing a bundle out of a slot does
    not silently claim the teacher decided its new role: a role only carries
    ``teacher`` provenance when a teacher actually chose it, and only such a
    role is frozen against reclassification.
    """
    if role not in ROLES:
        raise ValueError("Unknown version slot")
    who = assigned_by or LessonVariant.AssignedBy.TEACHER
    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    provenance = dict(selection.get("bundle_roles_assigned_by") or {})
    decided_at = dict(selection.get("bundle_roles_decided_at") or {})
    roles[str(material_id)] = role
    provenance[str(material_id)] = who
    # The most recent teacher decision wins a contested slot, so when a role
    # was decided has to survive the write.
    decided_at[str(material_id)] = timezone.now().isoformat()
    selection["bundle_roles"] = roles
    selection["bundle_roles_assigned_by"] = provenance
    selection["bundle_roles_decided_at"] = decided_at
    group.version_selection = selection
    group.save(update_fields=["version_selection"])


def prune_bundle_role(group, material_id):
    """Forget everything stored about one material's bundle in this concept.

    Called when a material stops contributing to a concept, and again before
    it starts: a role is a ruling about the text that was there, so a material
    that leaves and later returns with different objects must not inherit the
    role its previous objects were given. All three stores go together --
    leaving ``bundle_roles_decided_at`` behind would let a stale timestamp win
    a contested primary slot for a decision that no longer exists.
    """
    if group is None:
        return None
    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    provenance = dict(selection.get("bundle_roles_assigned_by") or {})
    decided_at = dict(selection.get("bundle_roles_decided_at") or {})
    key = str(material_id)
    if key not in roles and key not in provenance and key not in decided_at:
        return None
    dropped = roles.pop(key, None)
    provenance.pop(key, None)
    decided_at.pop(key, None)
    selection["bundle_roles"] = roles
    selection["bundle_roles_assigned_by"] = provenance
    selection["bundle_roles_decided_at"] = decided_at
    group.version_selection = selection
    group.save(update_fields=["version_selection"])
    return dropped


def version_bundles(group):
    """``{role: [objects]}`` for the versions a PDF supplies."""
    bundles = _eligible_bundles(group)
    normal_id = _normal_id(group, bundles)
    result = {}
    if normal_id in bundles:
        result["NORMAL"] = bundles[normal_id]
    for material_id, role in bundle_roles(group).items():
        result.setdefault(role, bundles[material_id])
    return result


def _proxy(objects):
    """A lightweight stand-in carrying a whole bundle's text to the classifier.

    ``classify_group_versions`` only reads ``id``, ``title`` and ``content``, so
    the bundle can be classified as one candidate without the model ever seeing
    the objects separately.
    """
    return SimpleNamespace(
        id=bundle_lead(objects).id,
        content=bundle_text(objects),
        title=bundle_heading(objects),
    )


def _roles_signature(bundles, ordered_ids):
    return hashlib.sha256("|".join(
        f"{material_id}:{bundle_text(bundles[material_id])}"
        for material_id in ordered_ids
    ).encode()).hexdigest()


def assign_group_versions(group, *, use_llm=False):
    """Classify this concept's bundles and record each one's role.

    When requested, Gemma chooses the Normal bundle and the roles are stored
    with its provenance. Calls that do not invoke the model still propose a
    role from readability, but report every unconvincing one in
    ``needs_confirmation`` so the review screen can ask instead of pretending.
    """
    bundles = _eligible_bundles(group)
    ordered_ids = _ordered_bundle_ids(group, bundles)
    selection = dict(group.version_selection or {})
    all_objects = [item for material_id in ordered_ids for item in bundles[material_id]]

    if len(bundles) < 2:
        only_id = ordered_ids[0] if ordered_ids else None
        lead = bundle_lead(bundles[only_id]) if only_id is not None else None
        _sync_automatic_group_label(
            group,
            lead,
            members=all_objects,
            heading=bundle_label(bundles[only_id]) if only_id is not None else "",
        )
        return {
            "representative_id": lead.id if lead is not None else None,
            "normal_material_id": only_id,
            "bundle_roles": {},
            "original_selected": lead is not None,
            "classification_complete": True,
            "assigned": [],
            "needs_confirmation": [],
            "extras": 0,
            "classification_error": "",
        }

    signature = _roles_signature(bundles, ordered_ids)
    classification_complete = selection.get("roles_signature") == signature
    stored_roles = _stored_roles(selection)
    stored_provenance = _stored_provenance(selection)

    # Upload order is only a display fallback; a Normal bundle is durable once
    # Gemma or a teacher has chosen it, so a later refresh cannot silently
    # reshuffle the learning path.
    original_selected = selection.get("normal_material_id") in bundles
    normal_id = _normal_id(group, bundles)

    classifications = {}
    if classification_complete:
        try:
            classifications = {
                int(item_id): result
                for item_id, result in (selection.get("classification_assignments") or {}).items()
            }
        except (TypeError, ValueError):
            classifications = {}
            classification_complete = False
    classification_error = ""

    if use_llm and not original_selected:
        try:
            proxies = [_proxy(bundles[material_id]) for material_id in ordered_ids]
            by_lead = {proxy.id: material_id for proxy, material_id in zip(proxies, ordered_ids)}
            classifications = classify_group_versions(proxies)
            original_lead_id = next(
                item_id
                for item_id, result in classifications.items()
                if result["slot"] == "ORIGINAL"
            )
            normal_id = by_lead[original_lead_id]
            original_selected = True
            normal_object_ids = {item.id for item in bundles[normal_id]}
            with transaction.atomic():
                # Remove only obsolete, unedited generated placements after a
                # successful new selection: they were written against wording
                # that is no longer the concept's baseline.
                LessonVariant.objects.filter(
                    learning_object_id__in=[item.id for item in all_objects],
                    origin=LessonVariant.Origin.GENERATED,
                ).exclude(assigned_by=LessonVariant.AssignedBy.TEACHER).exclude(
                    learning_object_id__in=normal_object_ids,
                ).delete()
                selection["normal_material_id"] = normal_id
                group.version_selection = selection
                group.save(update_fields=["version_selection"])
        except (VersionClassificationError, StopIteration, KeyError) as exc:
            classification_error = str(exc)
            logger.warning(
                "Content-version classification failed: group=%s model=%s error=%s",
                group.id,
                settings.CONTENT_VERSION_LLM_MODEL,
                exc,
            )

    normal_bundle = bundles[normal_id]
    representative = bundle_lead(normal_bundle)
    normal_text = bundle_text(normal_bundle)
    candidate_ids = [material_id for material_id in ordered_ids if material_id != normal_id]

    unresolved = [
        material_id
        for material_id in candidate_ids
        if stored_provenance.get(material_id) != LessonVariant.AssignedBy.TEACHER
        and not classification_complete
    ]
    if use_llm and unresolved and not classifications and original_selected:
        try:
            proxies = [_proxy(bundles[material_id]) for material_id in unresolved]
            classifications = classify_group_versions(
                proxies,
                representative=_proxy(normal_bundle),
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
    for material_id in candidate_ids:
        objects = bundles[material_id]
        lead = bundle_lead(objects)
        verdict = compare(normal_text, bundle_text(objects))
        llm = classifications.get(lead.id)
        llm_slot = llm["slot"] if llm and llm["slot"] in ROLES else None
        # Gemma owns the classification. Readability remains visible evidence
        # for teacher review, and proposes the role on its own when the model
        # was not asked; either way a teacher can move any bundle afterwards.
        proposals.append({
            "material_id": material_id,
            "learning_object_id": lead.id,
            "objects": objects,
            "slot": llm_slot or verdict["slot"],
            "confident": bool(llm_slot) or verdict["confident"],
            "llm_slot": llm_slot,
            "llm_confidence": llm["confidence"] if llm else None,
            "llm_reason": llm["reason"] if llm else "",
            "readability_slot": verdict["slot"],
            "readability_confident": verdict["confident"],
            "delta_fk": verdict["delta_fk"],
            "delta_words": verdict["delta_words"],
            "ratio": verdict["ratio"],
            "assigned_by": (
                LessonVariant.AssignedBy.LLM_VALIDATED if llm_slot
                else LessonVariant.AssignedBy.HEURISTIC
            ),
        })

    by_material = {proposal["material_id"]: proposal for proposal in proposals}
    decided_at = selection.get("bundle_roles_decided_at") or {}
    teacher_ids = sorted(
        (
            material_id
            for material_id in candidate_ids
            if stored_provenance.get(material_id) == LessonVariant.AssignedBy.TEACHER
            and stored_roles.get(material_id) in ROLES
        ),
        # Most recent first: when two teacher decisions claim one slot, the
        # newer one wins it and the older is displaced.
        key=lambda material_id: (decided_at.get(str(material_id)) or "", material_id),
        reverse=True,
    )
    # A teacher's ruling claims its slot before anything automatic, then a
    # decision already taken, then the strongest margin -- so a collision
    # resolves in favour of the clearer claim rather than the first one seen.
    automatic_ids = sorted(
        (material_id for material_id in candidate_ids if material_id not in teacher_ids),
        key=lambda material_id: (
            0 if by_material[material_id]["confident"] else 1,
            -by_material[material_id]["delta_fk"],
        ),
    )

    roles = {}
    provenance = {}
    persisted = set()
    claimed = {}
    for material_id in (*teacher_ids, *automatic_ids):
        proposal = by_material[material_id]
        if material_id in teacher_ids:
            slot = stored_roles[material_id]
            who = LessonVariant.AssignedBy.TEACHER
            persisted.add(material_id)
        elif (
            classification_complete
            and stored_roles.get(material_id) in ROLES
            and stored_provenance.get(material_id) == LessonVariant.AssignedBy.LLM_VALIDATED
        ):
            # Only a decision the model actually made is durable. A heuristic
            # guess is re-derived every time, so it keeps reaching the teacher.
            slot = stored_roles[material_id]
            who = LessonVariant.AssignedBy.LLM_VALIDATED
            persisted.add(material_id)
        else:
            slot = proposal["slot"]
            who = proposal["assigned_by"]
        if slot in PRIMARY_SLOTS and claimed.get(slot, material_id) != material_id:
            # Wording that loses a primary slot is kept as an extra rather
            # than dropped: a teacher wrote it.
            slot = "EXTRA"
            who = _displaced_provenance(who)
            persisted.discard(material_id)
        if slot in PRIMARY_SLOTS:
            claimed[slot] = material_id
        roles[material_id] = slot
        provenance[material_id] = who

    assigned = []
    needs_confirmation = []
    for material_id in candidate_ids:
        proposal = by_material[material_id]
        entry = {**_public(proposal), "slot": roles[material_id]}
        if material_id in persisted:
            if roles[material_id] in PRIMARY_SLOTS:
                assigned.append({**entry, "persisted": True})
            continue
        if not proposal["confident"]:
            needs_confirmation.append(entry)
            continue
        if roles[material_id] in PRIMARY_SLOTS:
            assigned.append(entry)
    extras = sum(role == "EXTRA" for role in roles.values())

    stored_role_json = {str(key): value for key, value in roles.items()}
    stored_provenance_json = {str(key): value for key, value in provenance.items()}
    # Only a run that was actually asked to classify writes a role down. A
    # plain read -- the grouping step loads the whole topic through here --
    # still returns what readability proposes, so a caller can show it as a
    # suggestion, but storing it made every page load look like a decision:
    # the concept came back labelled Simplified or Elaborated before the
    # teacher reached the classification step and before Gemma saw it.
    # Teacher roles reach the database through `set_bundle_role`, so nothing
    # a teacher decided depends on this write.
    changed = use_llm and (
        selection.get("bundle_roles") != stored_role_json
        or selection.get("bundle_roles_assigned_by") != stored_provenance_json
    )
    if changed:
        selection["bundle_roles"] = stored_role_json
        selection["bundle_roles_assigned_by"] = stored_provenance_json

    # Completion means every bundle was actually ruled on. A bundle the model
    # returned no slot for is still an open question, so recording the run as
    # complete would bury it: it would come back as a persisted decision and
    # never reach the teacher again.
    fully_classified = all(
        by_material[material_id]["llm_slot"]
        or material_id in teacher_ids
        or material_id in persisted
        for material_id in candidate_ids
    )
    if (
        use_llm
        and settings.CONTENT_VERSION_LLM_ENABLED
        and not classification_error
        and fully_classified
    ):
        # Persist both completion and the model evidence. A later GET can then
        # render only genuine review cases without making another slow LLM call.
        selection.update({
            "normal_material_id": normal_id,
            "roles_signature": signature,
            "classification_assignments": {
                str(item_id): result for item_id, result in classifications.items()
            },
        })
        changed = True
        classification_complete = True
    elif use_llm and settings.CONTENT_VERSION_LLM_ENABLED and not classification_error:
        # The Normal bundle was still chosen; only the roles stay open.
        selection["normal_material_id"] = normal_id
        selection.pop("roles_signature", None)
        changed = True
        classification_complete = False
    if changed:
        group.version_selection = selection
        group.save(update_fields=["version_selection"])

    # Before classification this is the stable first-upload fallback. Once
    # Gemma selects the Normal bundle, the same rule automatically replaces it
    # with that bundle's cleaned heading. Explicit teacher labels stay locked.
    _sync_automatic_group_label(
        group,
        representative,
        members=all_objects,
        heading=bundle_label(normal_bundle),
    )

    return {
        "representative_id": representative.id,
        "normal_material_id": normal_id,
        "bundle_roles": dict(roles),
        "original_selected": original_selected,
        "classification_complete": classification_complete,
        "assigned": assigned,
        "needs_confirmation": needs_confirmation,
        "extras": extras,
        "classification_error": classification_error,
    }


@transaction.atomic
def assign_source_to_slot(representative, source, slot):
    """Persist one teacher decision about the bundle ``source`` belongs to.

    The whole bundle moves: a role belongs to everything one PDF wrote about
    this concept, not to the single object the teacher happened to click.
    """
    if slot not in ROLES:
        raise ValueError("Unknown version slot")
    if source.id == representative.id:
        raise ValueError("The representative cannot be assigned as its own version")
    group = source.group
    if group is None:
        raise ValueError("Only grouped learning objects have version slots")

    bundles = _eligible_bundles(group)
    if source.material_id == _normal_id(group, bundles):
        raise ValueError("Choose another PDF source as Normal before moving this one")
    displaced_id = None
    displaced_provenance = None
    if slot in PRIMARY_SLOTS:
        displaced_id = next(
            (
                material_id
                for material_id, role in bundle_roles(group).items()
                if role == slot and material_id != source.material_id
            ),
            None,
        )
        if displaced_id is not None:
            # The teacher's new choice wins the primary slot, but the wording
            # it displaces is another PDF's and is kept as an extra -- under
            # its own provenance, because the teacher did not choose this role
            # for it.
            displaced_provenance = _displaced_provenance(
                _stored_provenance(group.version_selection or {}).get(displaced_id)
            )
            set_bundle_role(group, displaced_id, "EXTRA", assigned_by=displaced_provenance)

    set_bundle_role(group, source.material_id, slot)

    if displaced_id is not None:
        # Losing a slot is not the same as being demoted to an extra: the
        # displaced wording takes whichever primary slot it actually fits, and
        # only stays an extra when none is free. That role is settled here,
        # with the decision that caused it. It used to be re-derived by the
        # next page load instead, which is no longer true now that a read
        # records nothing -- the displaced bundle would have stayed an extra
        # and its wording would have been regenerated rather than used.
        rederived = assign_group_versions(group)["bundle_roles"].get(displaced_id)
        if rederived and rederived != "EXTRA":
            set_bundle_role(group, displaced_id, rederived, assigned_by=displaced_provenance)
    for item in bundles.get(source.material_id, [source]):
        if item.represented_by_id != representative.id:
            item.represented_by = representative
            item.save(update_fields=["represented_by"])
    displaced_lead = bundle_lead(bundles.get(displaced_id) or []) if displaced_id else None
    return {
        "material_id": source.material_id,
        "slot": slot,
        "displaced_material_id": displaced_id,
        "displaced_learning_object_id": displaced_lead.id if displaced_lead else None,
    }


@transaction.atomic
def assign_source_as_representative(group, source):
    """Make another PDF's bundle Normal and give the old Normal its role."""
    state = assign_group_versions(group)
    representative_id = state["representative_id"]
    if representative_id is None:
        raise ValueError("This group has no Normal version")
    if source.group_id != group.id:
        raise ValueError("The selected source is not in this concept group")
    if source.id == representative_id:
        return source

    bundles = _eligible_bundles(group)
    normal_id = state["normal_material_id"]
    previous_slot = state["bundle_roles"].get(source.material_id)
    if previous_slot is None:
        raise ValueError("Classify this PDF source before making it Normal")

    new_normal = bundles[source.material_id]
    # Generated wording was grounded in the old Normal and must be regenerated
    # against the new baseline. Teacher-edited generated wording is preserved.
    LessonVariant.objects.filter(
        learning_object__group=group,
        origin=LessonVariant.Origin.GENERATED,
        assigned_by__in=(
            LessonVariant.AssignedBy.HEURISTIC,
            LessonVariant.AssignedBy.LLM_VALIDATED,
        ),
    ).delete()

    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    provenance = dict(selection.get("bundle_roles_assigned_by") or {})
    roles.pop(str(source.material_id), None)
    provenance.pop(str(source.material_id), None)
    roles[str(normal_id)] = previous_slot
    provenance[str(normal_id)] = LessonVariant.AssignedBy.TEACHER
    selection.update({
        "normal_material_id": source.material_id,
        "bundle_roles": roles,
        "bundle_roles_assigned_by": provenance,
    })
    group.version_selection = selection
    group.save(update_fields=["version_selection"])

    new_normal_ids = [item.id for item in new_normal]
    group.learning_objects.exclude(pk__in=new_normal_ids).update(
        represented_by=bundle_lead(new_normal),
    )
    group.learning_objects.filter(pk__in=new_normal_ids).update(represented_by=None)
    source.represented_by = None
    _sync_automatic_group_label(
        group,
        bundle_lead(new_normal),
        members=[item for objects in bundles.values() for item in objects],
        heading=bundle_label(new_normal),
    )
    return source


def _public(proposal):
    return {key: value for key, value in proposal.items() if key != "objects"}


def settle_group(group):
    """Distribute real text, fill what is missing, then collapse the concept.

    Flagging happens last and only for bundles whose role was actually decided.
    A bundle awaiting teacher confirmation stays a teaching step: until someone
    decides where its text belongs, removing it from the lesson would drop
    content rather than deduplicate it.
    """
    from .variant_generator import fill_missing_bundle_slots

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

    bundles = _eligible_bundles(group)
    representative = bundle_lead(bundles[outcome["normal_material_id"]])

    # A role another PDF already supplies is never generated: that wording
    # exists as its own objects, so writing it again would teach it twice.
    # Missing versions are written one object of the Normal bundle at a time.
    filled = fill_missing_bundle_slots(group)

    # A bundle whose role is still a question stays its own teaching step:
    # hiding it would drop content nobody has ruled on yet.
    pending = {entry["material_id"] for entry in outcome["needs_confirmation"]}
    for material_id in outcome["bundle_roles"]:
        if material_id in pending:
            continue
        for item in bundles.get(material_id, []):
            if item.represented_by_id != representative.id:
                item.represented_by = representative
                item.save(update_fields=["represented_by"])

    return {
        **outcome,
        "generated": filled["generated"],
        "errors": filled["errors"],
    }


def group_original_id(group, members):
    """Which object supplies the group's Normal version, if one was decided.

    Mirrors how ``assign_group_versions`` finds the baseline: the lead of the
    Normal bundle, then whatever the others are represented by, then the
    stored selection.
    """
    if group is not None:
        bundles = _eligible_bundles(group)
        normal_id = _normal_id(group, bundles)
        if normal_id in bundles:
            lead = bundle_lead(bundles[normal_id])
            if lead is not None:
                return lead.id
    for item in members:
        if item.represented_by_id:
            return item.represented_by_id
    return ((group.version_selection or {}) if group is not None else {}).get(
        "representative_id"
    )


def release_from_group(learning_object, companions):
    """Undo version links before an object leaves the members staying behind.

    Call this **before** the object's group changes. ``companions`` are the
    members that stay in the old group.

    Without it an object carries its old group's bookkeeping into its new one:
    it stays marked "taught through" an original it no longer shares a concept
    with, so version generation refuses it.

    A PDF-supplied version is now its own objects, so there is no copied text
    to take back. What is undone is:

    * the leaving object is no longer represented by the old original;
    * its bundle's stored role is dropped when it was the last object that PDF
      contributed to the concept;
    * if it **was** the original, everyone it represented is released and the
      concept's stored selection is reset so a new Normal is chosen from those
      who remain.

    Generated versions are never deleted here, including ones a teacher edited.
    That is the difference from ``release_learning_object``, which drops every
    generated row on the original.
    """
    companions = [item for item in companions if item.pk != learning_object.pk]
    if not companions:
        return {"was_original": False, "removed_version_slots": []}
    companion_ids = [item.id for item in companions]
    group = learning_object.group
    original_id = group_original_id(group, [learning_object, *companions])

    removed = []
    if original_id == learning_object.id:
        type(learning_object).objects.filter(
            pk__in=companion_ids,
            represented_by=learning_object,
        ).update(represented_by=None)
        if group is not None:
            selection = group.version_selection or {}
            # Every role was decided against a baseline that is leaving, so
            # they all go and the concept chooses a new Normal from the rest.
            removed = [
                role for role in (selection.get("bundle_roles") or {}).values()
                if role in ROLES
            ]
            label_locked = bool(selection.get("label_locked"))
            group.version_selection = {"label_locked": True} if label_locked else {}
            group.save(update_fields=["version_selection"])
        was_original = True
    else:
        was_original = False
        if group is not None and not any(
            item.material_id == learning_object.material_id for item in companions
        ):
            dropped = prune_bundle_role(group, learning_object.material_id)
            if dropped is not None:
                removed = [dropped]

    if learning_object.represented_by_id in companion_ids:
        learning_object.represented_by = None
        type(learning_object).objects.filter(pk=learning_object.pk).update(represented_by=None)

    return {
        "was_original": was_original,
        "removed_version_slots": sorted({slot.lower() for slot in removed}),
    }


def release_learning_object(learning_object):
    """Undo representation for one object after a teacher ungroups it.

    Generated rows on the representative are dropped because the object that
    justified them is leaving.
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
        origin=LessonVariant.Origin.GENERATED,
    ).delete()

    current.represented_by = None
    current.save(update_fields=["represented_by"])
    # Keep the caller's instance consistent with what was just written.
    learning_object.represented_by = None
