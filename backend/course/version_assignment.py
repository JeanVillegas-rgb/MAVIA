"""Jean's bundle-level version process with MAVIA-compatible outputs.

The first uploaded bundle is Standard. Later PDF bundles are classified once
against it. Simplified and Elaborated fill learner-facing slots; another Normal
bundle remains an alternate source without replacing the baseline. Missing
slots are generated elsewhere.
"""

import hashlib
import re
from types import SimpleNamespace

from django.db import transaction

from lessons.services.concept_bundles import (
    bundle_heading, bundle_label, bundle_lead, bundle_text, bundles_for_group,
    material_order,
)
from .bundle_version_classifier import (
    BundleClassificationError as VersionClassificationError,
    classify_group_versions,
)
from .models import LessonVariant


PRIMARY_SLOTS = ("SIMPLIFIED", "ELABORATED")
ROLES = PRIMARY_SLOTS
REMOVED_BY_TEACHER = "removed_by_teacher"
CLASSIFICATION_VERSION = 9
_PART_SUFFIX = re.compile(
    r"\s*[\[(]?\s*part\s+\d+\s*(?:of|/)\s*\d+\s*[\])]?\s*$", re.I,
)
_LEADING_NUMBER = re.compile(
    r"^\s*(?:(?:unit|lesson|chapter|section|topic)\s+)?"
    r"\d+(?:\.\d+)*(?:\s*[.):-]\s*|\s+)", re.I,
)


def clean_group_label(title):
    original = (title or "").strip()
    cleaned = _PART_SUFFIX.sub("", _LEADING_NUMBER.sub("", original))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:;,.()[]")
    return cleaned or original


def _eligible_bundles(group):
    result = {}
    for material_id, objects in bundles_for_group(group).items():
        objects = [item for item in objects if (item.content or "").strip()]
        if objects:
            result[material_id] = objects
    return result


def _ordered_ids(group, bundles):
    ranks = {mid: i for i, mid in enumerate(material_order(group.outline_node))}
    return sorted(
        bundles,
        key=lambda mid: (
            ranks.get(mid, len(ranks)), bundles[mid][0].material.created_at, mid,
        ),
    )


# Compatibility names used by diagnostics and tests; both are the same small
# helpers used by the active Jean flow.
def _ordered_bundle_ids(group, bundles):
    return _ordered_ids(group, bundles)


def _int_map(values):
    result = {}
    for key, value in (values or {}).items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            pass
    return result


def _stored_roles(selection):
    return _int_map(selection.get("bundle_roles"))


def _stored_provenance(selection):
    return _int_map(selection.get("bundle_roles_assigned_by"))


def _signature(bundles, ordered_ids):
    parts = [f"v{CLASSIFICATION_VERSION}"] + [
        f"{mid}:{bundle_text(bundles[mid])}" for mid in ordered_ids
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _roles_signature(bundles, ordered_ids):
    return _signature(bundles, ordered_ids)


def _replacement_needed(group, bundles):
    stored = (group.version_selection or {}).get("standard_material_id")
    return stored is not None and stored not in bundles and bool(bundles)


def _standard_id(group, bundles):
    selection = group.version_selection or {}
    stored = selection.get("standard_material_id")
    if _replacement_needed(group, bundles):
        return None
    if (
        selection.get("standard_assigned_by") == LessonVariant.AssignedBy.TEACHER
        and stored in bundles
    ):
        return stored
    ordered = _ordered_ids(group, bundles)
    return ordered[0] if ordered else None


def _save(group, selection):
    group.version_selection = selection
    group.save(update_fields=["version_selection"])


def _sync_label(group, standard):
    if not standard:
        return
    selection = dict(group.version_selection or {})
    if selection.get("label_locked"):
        return
    label = clean_group_label(bundle_label(standard))[:255]
    old_auto = (selection.get("auto_label") or "").strip()
    current = (group.label or "").strip()
    if old_auto and current and current.casefold() != old_auto.casefold():
        selection["label_locked"] = True
        _save(group, selection)
        return
    if label and group.label != label:
        group.label = label
        group.save(update_fields=["label"])
    if label and old_auto != label:
        selection["auto_label"] = label
        _save(group, selection)


def standard_material_id(group):
    return _standard_id(group, _eligible_bundles(group))


def bundle_roles(group):
    bundles = _eligible_bundles(group)
    standard = _standard_id(group, bundles)
    return {
        mid: role for mid, role in _stored_roles(group.version_selection or {}).items()
        if mid in bundles and mid != standard and role in ROLES
    }


def bundle_role_provenance(group):
    bundles = _eligible_bundles(group)
    return {
        mid: source
        for mid, source in _stored_provenance(group.version_selection or {}).items()
        if mid in bundles
    }


def set_bundle_role(group, material_id, role, assigned_by=None):
    if role not in ROLES:
        raise ValueError("Unknown version slot")
    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    sources = dict(selection.get("bundle_roles_assigned_by") or {})
    roles[str(material_id)] = role
    sources[str(material_id)] = assigned_by or LessonVariant.AssignedBy.TEACHER
    selection.update({
        "bundle_roles": roles, "bundle_roles_assigned_by": sources,
    })
    _save(group, selection)


def prune_bundle_role(group, material_id):
    if group is None:
        return None
    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    sources = dict(selection.get("bundle_roles_assigned_by") or {})
    removed = roles.pop(str(material_id), None)
    sources.pop(str(material_id), None)
    selection.update({
        "bundle_roles": roles, "bundle_roles_assigned_by": sources,
    })
    _save(group, selection)
    return removed


def version_bundles(group):
    bundles = _eligible_bundles(group)
    standard = _standard_id(group, bundles)
    result = {"STANDARD": bundles[standard]} if standard in bundles else {}
    for mid, role in bundle_roles(group).items():
        result.setdefault(role, bundles[mid])
    return result


def served_version_bundles(group):
    bundles = version_bundles(group)
    provenance = bundle_role_provenance(group)
    accepted = {
        LessonVariant.AssignedBy.TEACHER,
        LessonVariant.AssignedBy.LLM_VALIDATED,
    }
    return {
        role: objects for role, objects in bundles.items()
        if role == "STANDARD" or provenance.get(objects[0].material_id) in accepted
    }


def _pending(mid, objects, result):
    return {
        "material_id": mid,
        "learning_object_id": bundle_lead(objects).id,
        "slot": result.get("slot", "UNCLASSIFIED"),
        "llm_slot": result.get("slot"),
        "reason": result.get("reason", "Comparable with Standard."),
    }


def _bundle_proxy(objects):
    """Present one PDF bundle as the single item expected by the classifier."""
    return SimpleNamespace(
        id=bundle_lead(objects).id,
        title=bundle_heading(objects),
        content=bundle_text(objects),
    )


def assign_group_versions(group, *, use_llm=False):
    """Select Standard, classify other uploaded bundles, and report gaps."""
    bundles = _eligible_bundles(group)
    ordered = _ordered_ids(group, bundles)
    selection = dict(group.version_selection or {})
    replacement = _replacement_needed(group, bundles)
    standard_id = _standard_id(group, bundles)

    if standard_id is not None and selection.get("standard_material_id") is None:
        selection.update({
            "standard_material_id": standard_id,
            "standard_assigned_by": "upload_order",
        })
        _save(group, selection)

    standard = bundles.get(standard_id, [])
    representative = bundle_lead(standard)
    _sync_label(group, standard)
    if representative is None or replacement:
        return {
            "representative_id": None, "standard_material_id": standard_id,
            "bundle_roles": {}, "original_selected": False,
            "standard_replacement_needed": replacement,
            "classification_complete": not replacement,
            "assigned": [], "needs_confirmation": [], "kept_as_own_step": [],
            "classification_error": "",
        }

    candidates = [mid for mid in ordered if mid != standard_id]
    signature = _signature(bundles, ordered)
    complete = selection.get("roles_signature") == signature
    roles = {
        mid: role for mid, role in _stored_roles(selection).items()
        if mid in candidates and role in ROLES
    }
    sources = {
        mid: source for mid, source in _stored_provenance(selection).items()
        if mid in candidates
    }
    assignments = _int_map(selection.get("classification_assignments"))
    error = ""

    if use_llm and candidates and not complete:
        roles = {
            mid: role for mid, role in roles.items()
            if sources.get(mid) == LessonVariant.AssignedBy.TEACHER
        }
        assignments = {}
        try:
            unresolved = [
                mid for mid in candidates
                if sources.get(mid) != LessonVariant.AssignedBy.TEACHER
            ]
            by_lead = {
                bundle_lead(bundles[mid]).id: mid for mid in unresolved
            }
            classified = classify_group_versions(
                [_bundle_proxy(bundles[mid]) for mid in unresolved],
                representative=_bundle_proxy(standard),
            )
            for lead_id, result in classified.items():
                mid = by_lead.get(int(lead_id))
                if mid is None:
                    continue
                assignments[mid] = result
                sources[mid] = LessonVariant.AssignedBy.LLM_VALIDATED
                if result["slot"] in ROLES:
                    roles[mid] = result["slot"]
                else:
                    roles.pop(mid, None)
        except VersionClassificationError as exc:
            error = str(exc)
        else:
            selection.update({
                "roles_signature": signature,
                "classification_assignments": {
                    str(key): value for key, value in assignments.items()
                },
                "bundle_roles": {str(key): value for key, value in roles.items()},
                "bundle_roles_assigned_by": {
                    str(key): value for key, value in sources.items()
                },
            })
            _save(group, selection)
            complete = True

    assigned, pending = [], []
    for mid in candidates:
        result = assignments.get(mid, {})
        if mid in roles:
            assigned.append({
                "material_id": mid,
                "learning_object_id": bundle_lead(bundles[mid]).id,
                "slot": roles[mid],
                "reason": result.get("reason", ""),
            })
        elif not complete:
            pending.append(_pending(mid, bundles[mid], {
                "reason": "This PDF has not been classified yet.",
            }))
    return {
        "representative_id": representative.id,
        "standard_material_id": standard_id,
        "bundle_roles": roles,
        "original_selected": True,
        "standard_replacement_needed": False,
        "classification_complete": complete or not candidates,
        "assigned": assigned,
        "needs_confirmation": pending,
        "kept_as_own_step": pending,
        "classification_error": error,
    }


@transaction.atomic
def assign_source_to_slot(representative, source, slot):
    if slot not in ROLES:
        raise ValueError("Unknown version slot")
    group = source.group
    if group is None or representative.group_id != group.id:
        raise ValueError("Both sources must belong to the same concept")
    if source.material_id == standard_material_id(group):
        raise ValueError("Choose another PDF source as Standard first")
    displaced = next(
        (mid for mid, role in bundle_roles(group).items()
         if role == slot and mid != source.material_id), None,
    )
    if displaced:
        prune_bundle_role(group, displaced)
        group.learning_objects.filter(material_id=displaced).update(represented_by=None)
    set_bundle_role(group, source.material_id, slot)
    group.learning_objects.filter(material_id=source.material_id).update(
        represented_by=representative,
    )
    displaced_lead = bundle_lead(_eligible_bundles(group).get(displaced, []))
    return {
        "material_id": source.material_id, "slot": slot,
        "displaced_material_id": displaced,
        "displaced_learning_object_id": displaced_lead.id if displaced_lead else None,
    }


@transaction.atomic
def remove_source_from_slot(group, source, slot):
    if bundle_roles(group).get(source.material_id) != slot:
        raise ValueError(f"This PDF is not the current {slot.title()} version")
    prune_bundle_role(group, source.material_id)
    selection = dict(group.version_selection or {})
    sources = dict(selection.get("bundle_roles_assigned_by") or {})
    sources[str(source.material_id)] = REMOVED_BY_TEACHER
    selection["bundle_roles_assigned_by"] = sources
    _save(group, selection)
    group.learning_objects.filter(material_id=source.material_id).update(represented_by=None)
    return source


@transaction.atomic
def assign_source_as_representative(group, source):
    if source.group_id != group.id:
        raise ValueError("The selected source is not in this concept group")
    bundles = _eligible_bundles(group)
    if source.material_id not in bundles:
        raise ValueError("The selected PDF has no content for this concept")
    old_standard = _standard_id(group, bundles)
    old_roles = bundle_roles(group)
    source_role = old_roles.get(source.material_id)
    roles = {}
    sources = {}
    # If the teacher promotes an already-classified adaptive PDF, its former
    # slot is filled by the previous Standard. This is a direct swap, not a
    # second automatic classification algorithm.
    if old_standard is not None and source_role in ROLES:
        roles[str(old_standard)] = source_role
        sources[str(old_standard)] = LessonVariant.AssignedBy.TEACHER
    selection = dict(group.version_selection or {})
    selection.update({
        "standard_material_id": source.material_id,
        "standard_assigned_by": LessonVariant.AssignedBy.TEACHER,
        "bundle_roles": roles, "bundle_roles_assigned_by": sources,
    })
    selection.pop("roles_signature", None)
    selection.pop("classification_assignments", None)
    _save(group, selection)
    group.learning_objects.update(represented_by=None)
    new_lead = bundle_lead(bundles[source.material_id])
    for material_id in (int(key) for key in roles):
        group.learning_objects.filter(material_id=material_id).update(
            represented_by=new_lead,
        )
    LessonVariant.objects.filter(
        learning_object__group=group, origin=LessonVariant.Origin.GENERATED,
    ).exclude(assigned_by=LessonVariant.AssignedBy.TEACHER).delete()
    _sync_label(group, bundles[source.material_id])
    return source


def review_gemma_role(llm, verdict=None, standard_text=None, candidate_text=None):
    slot = (llm or {}).get("slot")
    return {
        "accepted": slot in PRIMARY_SLOTS, "slot": slot,
        "issues": [], "concerns": [], "measures": None,
    }


def settle_group(group):
    from .generate_variation import fill_missing_bundle_slots

    outcome = assign_group_versions(group, use_llm=True)
    if outcome["representative_id"] is None:
        detail = (
            "Choose a replacement Standard PDF before publishing."
            if outcome.get("standard_replacement_needed")
            else outcome.get("classification_error") or "No Standard PDF."
        )
        return {**outcome, "generated": [], "errors": [{"detail": detail}]}
    filled = fill_missing_bundle_slots(group)
    representative = group.learning_objects.model.objects.get(
        pk=outcome["representative_id"],
    )
    for mid in outcome["bundle_roles"]:
        group.learning_objects.filter(material_id=mid).update(represented_by=representative)
    return {**outcome, "generated": filled["generated"], "errors": filled["errors"]}


def group_original_id(group, members):
    if group is not None:
        standard = version_bundles(group).get("STANDARD") or []
        if standard:
            return standard[0].id
    return next((item.represented_by_id for item in members if item.represented_by_id), None)


def release_from_group(learning_object, companions):
    companions = [item for item in companions if item.pk != learning_object.pk]
    group = learning_object.group
    original = group_original_id(group, [learning_object, *companions])
    removed = []
    if group is not None and not any(
        item.material_id == learning_object.material_id for item in companions
    ):
        role = prune_bundle_role(group, learning_object.material_id)
        if role:
            removed.append(role.lower())
        if original == learning_object.id:
            selection = dict(group.version_selection or {})
            selection.pop("roles_signature", None)
            selection.pop("classification_assignments", None)
            _save(group, selection)
    if learning_object.represented_by_id:
        type(learning_object).objects.filter(pk=learning_object.pk).update(represented_by=None)
    return {
        "was_original": original == learning_object.id,
        "removed_version_slots": sorted(set(removed)),
    }
