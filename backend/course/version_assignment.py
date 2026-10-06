"""Decide which grouped text becomes which version of a learning object.

A group's members are alternative presentations of one concept, written by
different teachers. Two PDFs rarely chunk a lesson at the same grain, so a
concept holds a *bundle* per PDF -- all of that file's objects for the concept,
in document order -- and a role is decided for the whole bundle rather than for
each object. The first relevant PDF is Standard unless the teacher explicitly
replaces it; later PDFs can be Simplified or Elaborated.
Unassigned source bundles remain available for teacher review.

A version supplied by a PDF is its own objects: nothing is copied. Only
generated wording is stored as a ``LessonVariant``. The roles themselves live in
``group.version_selection`` so a re-run can never duplicate a teacher's text,
and a role a teacher set is never overwritten.
"""

import logging
import hashlib
import json
import re
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from config.console import name
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
ROLES = PRIMARY_SLOTS

# Provenance for a bundle pushed out of a primary slot by a teacher's newer
# choice. The displaced bundle no longer has a version role.
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
    """Name an unlocked concept after its Standard (or temporary fallback).

    ``heading`` is the Standard bundle's heading. A bundle often opens with a
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


def _standard_id(group, bundles):
    selection = group.version_selection or {}
    stored = selection.get("standard_material_id")
    if _standard_replacement_needed(group, bundles):
        return None
    # Only a teacher can override upload order. Older automatic selections
    # have no provenance, so they migrate to the first relevant PDF on read.
    if selection.get("standard_assigned_by") == LessonVariant.AssignedBy.TEACHER and stored in bundles:
        return stored
    order = _ordered_bundle_ids(group, bundles)
    return order[0] if order else None


def _standard_replacement_needed(group, bundles):
    """A previously selected primary PDF vanished; do not silently promote one."""
    selection = group.version_selection or {}
    stored = selection.get("standard_material_id")
    return stored is not None and stored not in bundles and bool(bundles)


def _baseline_changed(group, bundles):
    stored = (group.version_selection or {}).get("standard_material_id")
    return stored is not None and stored != _standard_id(group, bundles)


def standard_material_id(group):
    """The first relevant PDF, or a teacher's explicit replacement."""
    return _standard_id(group, _eligible_bundles(group))


def bundle_roles(group):
    """``{material_id: role}`` for every bundle other than Standard."""
    bundles = _eligible_bundles(group)
    standard_id = _standard_id(group, bundles)
    selection = group.version_selection or {}
    if _baseline_changed(group, bundles):
        return {}
    automatic_current = selection.get("roles_signature") == _roles_signature(
        bundles, _ordered_bundle_ids(group, bundles)
    )
    provenance = _stored_provenance(selection)
    return {
        material_id: role
        for material_id, role in _stored_roles(selection).items()
        if material_id in bundles and material_id != standard_id and role in ROLES
        and (
            automatic_current
            or provenance.get(material_id) in (LessonVariant.AssignedBy.TEACHER, DISPLACED_BY_TEACHER)
        )
    }


def bundle_role_provenance(group):
    """``{material_id: who decided its role}`` for this concept's bundles.

    The value is usually an ``AssignedBy`` choice, but a bundle pushed out of a
    primary slot carries ``displaced_by_teacher`` instead, so callers that show
    it must have wording for that case too.
    """
    bundles = _eligible_bundles(group)
    selection = group.version_selection or {}
    if _baseline_changed(group, bundles):
        return {}
    automatic_current = selection.get("roles_signature") == _roles_signature(
        bundles, _ordered_bundle_ids(group, bundles)
    )
    return {
        material_id: who
        for material_id, who in _stored_provenance(selection).items()
        if material_id in bundles
        and (automatic_current or who in (LessonVariant.AssignedBy.TEACHER, DISPLACED_BY_TEACHER))
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


# Who may make a PDF's text a learner-facing version: a teacher, or the model
# with the text measurements agreeing. A role set any other way -- readability
# alone, or one still flagged for review -- is a candidate, not a version.
CONFIRMED_ROLE_SOURCES = frozenset({
    LessonVariant.AssignedBy.TEACHER,
    LessonVariant.AssignedBy.LLM_VALIDATED,
})


def served_version_bundles(group):
    """``version_bundles`` limited to what learners are actually given.

    A flagged PDF version does not hold publishing back and is not served
    either: until someone confirms it, the concept's written Simplified or
    Elaborated stands in. Everything that decides what a learner gets -- the
    published path, the lesson package, the audio, the publish check and the
    version writer -- reads this, so they cannot disagree about a version.
    """
    bundles = version_bundles(group)
    provenance = bundle_role_provenance(group)
    return {
        role: objects for role, objects in bundles.items()
        if role == "STANDARD"
        or provenance.get(objects[0].material_id) in CONFIRMED_ROLE_SOURCES
    }


def version_bundles(group):
    """``{role: [objects]}`` for the versions a PDF supplies."""
    bundles = _eligible_bundles(group)
    standard_id = _standard_id(group, bundles)
    result = {}
    if standard_id in bundles:
        result["STANDARD"] = bundles[standard_id]
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


# Bumped when the classification prompt or its checks change, so results
# stored under the old rules are classified once more under the new ones.
# 2: Gemma reports its evidence and its label is checked against it.
# 3: evidence shortened to one phrase per list; the label is checked by
#    measurements of the texts (content_measures), not by the evidence.
# 4: weak coverage of any Standard sentence asks for review, and novelty no
#    longer changes a Simplified proposal into Elaborated automatically.
# 5: remove Extra; unmatched or displaced source bundles need teacher review.
# 6: first relevant PDF is Standard; the LLM classifies only later PDFs.
CLASSIFICATION_VERSION = 6


def _roles_signature(bundles, ordered_ids):
    return hashlib.sha256("|".join([
        f"v{CLASSIFICATION_VERSION}",
        *(
            f"{material_id}:{bundle_text(bundles[material_id])}"
            for material_id in ordered_ids
        ),
    ]).encode()).hexdigest()


ROLE_REVIEW_CACHE_KEY = "role_review_cache"


def _review_key(llm, standard_text, candidate_text):
    """What a role check depends on: the model's answer and both texts."""
    payload = json.dumps(
        {"llm": llm, "standard": standard_text, "candidate": candidate_text},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assign_group_versions(group, *, use_llm=False):
    """Classify this concept's bundles and record each one's role.

    The first relevant PDF is Standard unless a teacher explicitly chose another.
    Gemma only proposes roles for the supplementary PDFs. Calls that do not
    invoke the model still propose a role from readability, but report every unconvincing one in
    ``needs_confirmation`` so the review screen can ask instead of pretending.
    """
    bundles = _eligible_bundles(group)
    ordered_ids = _ordered_bundle_ids(group, bundles)
    selection = dict(group.version_selection or {})
    all_objects = [item for material_id in ordered_ids for item in bundles[material_id]]
    standard_id = _standard_id(group, bundles)
    replacement_needed = _standard_replacement_needed(group, bundles)
    if standard_id is not None and selection.get("standard_material_id") is None:
        # Record the first relevant upload even for a one-PDF concept. If its
        # last object is later deleted, the next PDF must not silently become
        # Standard just because classification had not run yet.
        selection["standard_material_id"] = standard_id
        selection["standard_assigned_by"] = "upload_order"
        group.version_selection = selection
        group.save(update_fields=["version_selection"])

    if len(bundles) < 2 or replacement_needed:
        lead = bundle_lead(bundles[standard_id]) if standard_id is not None else None
        _sync_automatic_group_label(
            group,
            lead,
            members=all_objects,
            heading=bundle_label(bundles[standard_id]) if standard_id is not None else "",
        )
        return {
            "representative_id": lead.id if lead is not None else None,
            "standard_material_id": standard_id,
            "bundle_roles": {},
            "original_selected": lead is not None and not replacement_needed,
            "standard_replacement_needed": replacement_needed,
            "classification_complete": not replacement_needed,
            "assigned": [],
            "needs_confirmation": [],
            "kept_as_own_step": [],
            "classification_error": "",
        }

    signature = _roles_signature(bundles, ordered_ids)
    baseline_changed = _baseline_changed(group, bundles)
    classification_complete = not baseline_changed and selection.get("roles_signature") == signature
    # An old role judged against another Standard is not reusable, even when a
    # teacher once confirmed that role. Keep its source text, but review anew.
    stored_roles = {} if baseline_changed else _stored_roles(selection)
    stored_provenance = {} if baseline_changed else _stored_provenance(selection)
    original_selected = True
    if baseline_changed and use_llm:
        # Old suppression links point at the former Standard. Clear them before
        # settling new roles, or the new Standard itself could remain hidden
        # from the lesson while an obsolete PDF is still its anchor.
        group.learning_objects.exclude(represented_by__isnull=True).update(represented_by=None)

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

    standard_bundle = bundles[standard_id]
    representative = bundle_lead(standard_bundle)
    standard_text = bundle_text(standard_bundle)
    candidate_ids = [material_id for material_id in ordered_ids if material_id != standard_id]

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
                representative=_proxy(standard_bundle),
            )
        except VersionClassificationError as exc:
            classification_error = str(exc)
            logger.warning(
                "[Versions] %s  the model could not sort its PDF versions (%s): %s  (concept %s)",
                name(group.label),
                settings.CONTENT_VERSION_LLM_MODEL,
                exc,
                group.id,
            )

    # The check of each model-proposed role measures the two texts with the
    # grouping encoder. Its result only changes when a text or the model's
    # answer does, so it is stored and reused: reading the concept (every page
    # load, on every step) no longer re-measures, loads models or logs.
    review_cache = dict(selection.get(ROLE_REVIEW_CACHE_KEY) or {})
    review_cache_changed = False

    proposals = []
    for material_id in candidate_ids:
        objects = bundles[material_id]
        lead = bundle_lead(objects)
        verdict = compare(standard_text, bundle_text(objects))
        llm = classifications.get(lead.id)
        llm_slot = llm["slot"] if llm and llm["slot"] in ROLES else None
        llm_needs_review = bool(llm and llm["slot"] == "NEEDS_REVIEW")
        llm_confidence = llm["confidence"] if llm else None
        # Gemma decides the role; measurements of the two texts check it
        # (facts kept, content added, easier to read). FKGL and confidence are
        # recorded, never decisive. (Readability still proposes a role on its
        # own only when Gemma gave no answer at all, as before.)
        review = None
        reviewed_now = False
        if llm_slot:
            review_key = _review_key(llm, standard_text, bundle_text(objects))
            cached = review_cache.get(str(material_id)) or {}
            if cached.get("key") == review_key:
                review = cached["review"]
            else:
                review = review_gemma_role(llm, verdict, standard_text, bundle_text(objects))
                review_cache[str(material_id)] = {"key": review_key, "review": review}
                review_cache_changed = reviewed_now = True
        accepted = bool(review and review["accepted"])
        proposals.append({
            "material_id": material_id,
            "learning_object_id": lead.id,
            "objects": objects,
            # The role to store: Gemma's, or the overlap-rule correction.
            "slot": (review["slot"] if review else None) or llm_slot or verdict["slot"],
            "confident": accepted if llm_slot else verdict["confident"],
            # A role that failed the check is not stored, so the text is never
            # served as that version; it stays available as its own telling.
            "rejected": llm_needs_review or (bool(llm_slot) and not accepted),
            "review_issues": review["issues"] if review else [],
            "review_concerns": review["concerns"] if review else [],
            "review_measures": review["measures"] if review else None,
            "llm_evidence": (llm or {}).get("evidence"),
            "llm_slot": llm_slot,
            "llm_classified": llm is not None,
            "llm_confidence": llm_confidence,
            "llm_reason": llm["reason"] if llm else "",
            "readability_slot": verdict["slot"],
            "readability_confident": verdict["confident"],
            "delta_fk": verdict["delta_fk"],
            "delta_words": verdict["delta_words"],
            "ratio": verdict["ratio"],
            "assigned_by": (
                LessonVariant.AssignedBy.LLM_VALIDATED if accepted
                else LessonVariant.AssignedBy.HEURISTIC
            ),
        })
        if reviewed_now:
            logger.info(
                "[Versions] %s  PDF %s: model says %s%s, reading level says %s%s -> %s  (concept %s)",
                name(group.label), material_id, llm_slot.lower(),
                f" ({round(llm_confidence * 100)}% sure)" if llm_confidence is not None else "",
                (verdict["slot"] or "no clear role").lower(),
                "" if verdict["confident"] else " (unsure)",
                f"used as {llm_slot.lower()}" if accepted else "not used, failed the check",
                group.id,
            )
            if review["issues"] or review["concerns"]:
                logger.debug(
                    "[Versions] %s  PDF %s check details: issues=%s concerns=%s",
                    name(group.label), material_id, review["issues"], review["concerns"],
                )

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
        if stored_provenance.get(material_id) == DISPLACED_BY_TEACHER:
            continue
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
        elif proposal.get("rejected"):
            # Readability caught Gemma's role (or nothing supported a
            # low-confidence one). No role is stored, so the text is not
            # served as that version and claims no slot; it stays a teaching
            # step and is listed for the teacher, like any open question.
            continue
        else:
            slot = proposal["slot"]
            who = proposal["assigned_by"]
        if slot in PRIMARY_SLOTS and claimed.get(slot, material_id) != material_id:
            # A source that loses a contested slot remains unassigned for
            # teacher review; it is never forced into another version role.
            persisted.discard(material_id)
            continue
        if slot not in PRIMARY_SLOTS:
            continue
        claimed[slot] = material_id
        roles[material_id] = slot
        provenance[material_id] = who

    assigned = []
    needs_confirmation = []
    # Every unassigned source remains visible for teacher review. None is
    # silently dropped or forced into a learner-facing version.
    kept_as_own_step = []
    for material_id in candidate_ids:
        proposal = by_material[material_id]
        role = roles.get(material_id)
        entry = {**_public(proposal), "slot": role}
        if material_id in persisted:
            if role in PRIMARY_SLOTS:
                assigned.append({**entry, "persisted": True})
            continue
        if role is None:
            needs_confirmation.append(entry)
            continue
        if not proposal["confident"]:
            needs_confirmation.append(entry)
            continue
        if role in PRIMARY_SLOTS:
            assigned.append(entry)

    stored_role_json = {str(key): value for key, value in roles.items()}
    stored_provenance_json = {str(key): value for key, value in provenance.items()}
    stored_provenance_json.update({
        str(material_id): DISPLACED_BY_TEACHER
        for material_id in candidate_ids
        if stored_provenance.get(material_id) == DISPLACED_BY_TEACHER
        and material_id not in roles
    })
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
        by_material[material_id]["llm_classified"]
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
            "standard_material_id": standard_id,
            "standard_assigned_by": selection.get("standard_assigned_by") or "upload_order",
            "roles_signature": signature,
            "classification_assignments": {
                str(item_id): result for item_id, result in classifications.items()
            },
        })
        changed = True
        classification_complete = True
    elif use_llm and settings.CONTENT_VERSION_LLM_ENABLED and not classification_error:
        # The Standard bundle was still chosen; only the roles stay open.
        selection["standard_material_id"] = standard_id
        selection["standard_assigned_by"] = selection.get("standard_assigned_by") or "upload_order"
        selection.pop("roles_signature", None)
        changed = True
        classification_complete = False
    if review_cache_changed:
        selection[ROLE_REVIEW_CACHE_KEY] = {
            key: value for key, value in review_cache.items() if int(key) in candidate_ids
        }
        changed = True
    if changed:
        group.version_selection = selection
        group.save(update_fields=["version_selection"])

    # The first relevant PDF is the baseline before and after classification.
    # Explicit teacher labels stay locked.
    _sync_automatic_group_label(
        group,
        representative,
        members=all_objects,
        heading=bundle_label(standard_bundle),
    )

    return {
        "representative_id": representative.id,
        "standard_material_id": standard_id,
        "bundle_roles": dict(roles),
        "original_selected": original_selected,
        "standard_replacement_needed": False,
        "classification_complete": classification_complete,
        "assigned": assigned,
        "needs_confirmation": needs_confirmation,
        "kept_as_own_step": kept_as_own_step,
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
    group.refresh_from_db(fields=["version_selection"])

    bundles = _eligible_bundles(group)
    if source.material_id == _standard_id(group, bundles):
        raise ValueError("Choose another PDF source as Standard before moving this one")
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
            # Keep the displaced PDF wording, but leave its role open for
            # review and stop representing it through the old Standard.
            prune_bundle_role(group, displaced_id)
            selection = dict(group.version_selection or {})
            provenance = dict(selection.get("bundle_roles_assigned_by") or {})
            provenance[str(displaced_id)] = DISPLACED_BY_TEACHER
            selection["bundle_roles_assigned_by"] = provenance
            group.version_selection = selection
            group.save(update_fields=["version_selection"])
            for item in bundles.get(displaced_id, []):
                if item.represented_by_id:
                    item.represented_by = None
                    item.save(update_fields=["represented_by"])

    set_bundle_role(group, source.material_id, slot)

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
    """Record a teacher's explicit choice of a replacement Standard PDF."""
    group.refresh_from_db(fields=["version_selection"])
    state = assign_group_versions(group)
    representative_id = state["representative_id"]
    replacement_needed = state.get("standard_replacement_needed", False)
    if representative_id is None and not replacement_needed:
        raise ValueError("This group has no Standard version")
    if source.group_id != group.id:
        raise ValueError("The selected source is not in this concept group")
    if source.id == representative_id and not replacement_needed:
        selection = dict(group.version_selection or {})
        if selection.get("standard_material_id") != source.material_id:
            # The first-upload rule has already made this the visible Standard,
            # but an older LLM choice may still be stored. Confirming it must
            # also clear roles judged against that older baseline.
            selection.update({
                "standard_material_id": source.material_id,
                "standard_assigned_by": LessonVariant.AssignedBy.TEACHER,
                "bundle_roles": {},
                "bundle_roles_assigned_by": {},
            })
            selection.pop("roles_signature", None)
            selection.pop("classification_assignments", None)
            group.version_selection = selection
            group.save(update_fields=["version_selection"])
        return source

    bundles = _eligible_bundles(group)
    standard_id = state["standard_material_id"]
    previous_slot = None if replacement_needed else bundle_roles(group).get(source.material_id)

    new_standard = bundles[source.material_id]
    # Generated wording was grounded in the old Standard and must be regenerated
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
    if replacement_needed:
        # Every old role was judged against a deleted baseline. No surviving
        # source gets a new role until it is reviewed against this choice.
        roles.clear()
        provenance.clear()
    elif previous_slot in PRIMARY_SLOTS:
        roles[str(standard_id)] = previous_slot
        provenance[str(standard_id)] = LessonVariant.AssignedBy.TEACHER
    else:
        # The teacher chose a new baseline, not a role for the old baseline.
        # Keep the old PDF visible until its role is reviewed.
        roles.pop(str(standard_id), None)
        provenance[str(standard_id)] = DISPLACED_BY_TEACHER
    selection.update({
        "standard_material_id": source.material_id,
        "standard_assigned_by": LessonVariant.AssignedBy.TEACHER,
        "bundle_roles": roles,
        "bundle_roles_assigned_by": provenance,
    })
    selection.pop("roles_signature", None)
    selection.pop("classification_assignments", None)
    group.version_selection = selection
    group.save(update_fields=["version_selection"])

    new_standard_ids = [item.id for item in new_standard]
    group.learning_objects.exclude(pk__in=new_standard_ids).update(represented_by=None)
    for material_id, objects in bundles.items():
        if roles.get(str(material_id)) in PRIMARY_SLOTS:
            type(source).objects.filter(pk__in=[item.pk for item in objects]).update(
                represented_by=bundle_lead(new_standard)
            )
    group.learning_objects.filter(pk__in=new_standard_ids).update(represented_by=None)
    source.represented_by = None
    _sync_automatic_group_label(
        group,
        bundle_lead(new_standard),
        members=[item for objects in bundles.values() for item in objects],
        heading=bundle_label(new_standard),
    )
    return source


def review_gemma_role(llm, verdict, standard_text=None, candidate_text=None):
    """Keep Gemma's role only when measurements of the two texts agree with it.

    Gemma proposes the role; ``content_measures`` checks it without Gemma:

    * SIMPLIFIED -- the facts are kept, nothing is added, and it is easier to
      read (Dale-Chall);
    * ELABORATED -- the facts are kept and something is added.

    A Simplified proposal with a novel sentence is sent for review, not
    relabelled Elaborated: novelty does not prove the sentence explains the
    concept. Any role the measurements do not support is not stored. A problem
    Gemma itself reports (a missing fact, a contradiction, another topic) also
    stops it. FKGL and Gemma's self-reported
    confidence are recorded as concerns only.

    Earlier thresholds were measured on 21 labelled pairs, but those results
    predate the per-sentence coverage gate and must not be treated as accuracy
    figures for this rule. Similarity still cannot prove entailment or catch
    every contradiction inside a matched sentence.

    The returned ``slot`` remains Gemma's proposal; a rejected one needs review.
    """
    from .content_measures import MeasurementUnavailable, measure_versions

    llm = llm or {}
    slot = llm.get("slot")
    issues, concerns, measures = [], [], None
    problems = (llm.get("evidence") or {}).get("problems") or []
    if problems:
        issues.append("gemma_reports_a_problem")
    if slot in PRIMARY_SLOTS and standard_text is not None and candidate_text is not None:
        try:
            measures = measure_versions(standard_text, candidate_text)
        except MeasurementUnavailable:
            # Without the encoder there is nothing to check against; Gemma's
            # role is kept and the gap is recorded.
            concerns.append("measurements_unavailable")
        if measures:
            if not measures["facts_kept"]:
                issues.append("facts_not_kept")
            if slot == "SIMPLIFIED" and measures["adds_content"]:
                issues.append("simplified_but_adds_content")
            if slot == "SIMPLIFIED" and not measures["easier"]:
                issues.append("simplified_but_not_easier")
            if slot == "ELABORATED" and not measures["adds_content"]:
                issues.append("elaborated_but_adds_nothing")
    if slot in PRIMARY_SLOTS and verdict.get("confident") and verdict.get("slot") != slot:
        concerns.append("fkgl_points_the_other_way")
    confidence = llm.get("confidence")
    threshold = float(getattr(settings, "CONTENT_VERSION_LLM_AUTO_THRESHOLD", 0.80))
    if confidence is not None and float(confidence) < threshold:
        concerns.append("low_self_reported_confidence")
    return {
        "accepted": slot in ROLES and not issues,
        "slot": slot,
        "issues": issues,
        "concerns": concerns,
        "measures": measures,
    }


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
    if outcome.get("standard_replacement_needed"):
        return {
            **outcome, "generated": [],
            "errors": [{"learning_object_id": None, "detail": "Choose a replacement Standard PDF before publishing."}],
        }
    if outcome["representative_id"] is None:
        return {**outcome, "generated": [], "errors": []}
    if not outcome.get("original_selected"):
        detail = outcome.get("classification_error") or "This concept has no Standard PDF."
        return {
            **outcome,
            "generated": [],
            "errors": [{"learning_object_id": None, "detail": detail}],
        }

    bundles = _eligible_bundles(group)
    representative = bundle_lead(bundles[outcome["standard_material_id"]])

    # A role another PDF already supplies is never generated: that wording
    # exists as its own objects, so writing it again would teach it twice.
    # Missing versions are written one object of the Standard bundle at a time.
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
    """Which object supplies the group's Standard version, if one was decided.

    Mirrors how ``assign_group_versions`` finds the baseline: the lead of the
    Standard bundle, then whatever the others are represented by, then the
    stored selection.
    """
    if group is not None:
        bundles = _eligible_bundles(group)
        standard_id = _standard_id(group, bundles)
        if standard_id in bundles:
            lead = bundle_lead(bundles[standard_id])
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
    * if it **was** the Standard lead, a remaining object from the same PDF
      becomes the lead; otherwise the teacher must choose a replacement PDF.

    Generated versions are never deleted here, including ones a teacher edited.
    """
    companions = [item for item in companions if item.pk != learning_object.pk]
    if not companions:
        return {"was_original": False, "removed_version_slots": []}
    companion_ids = [item.id for item in companions]
    group = learning_object.group
    original_id = group_original_id(group, [learning_object, *companions])

    removed = []
    if original_id == learning_object.id:
        same_pdf = sorted(
            (item for item in companions if item.material_id == learning_object.material_id),
            key=lambda item: (item.order, item.id),
        )
        new_lead = same_pdf[0] if same_pdf else None
        type(learning_object).objects.filter(
            pk__in=companion_ids, represented_by=learning_object,
        ).update(represented_by=new_lead)
        if group is not None:
            if not same_pdf:
                selection = dict(group.version_selection or {})
                selection.setdefault("standard_material_id", learning_object.material_id)
                selection.setdefault("standard_assigned_by", "upload_order")
                removed = [
                    role for role in (selection.get("bundle_roles") or {}).values()
                    if role in ROLES
                ]
                selection.pop("roles_signature", None)
                selection.pop("classification_assignments", None)
                group.version_selection = selection
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
