"""Place several objects of one PDF that another PDF teaches as one concept.

Measured on topic 62 before this was written: the cross-encoder scored nearly
every item of one PDF between 0.5 and 0.65 against every section of the other,
so scores cannot find these units. Document structure finds candidates -- runs
of consecutive objects under one heading -- but on its own it over-merges: a
"Matter" section may hold Matter, Solid, Liquid and Gas. A unit is therefore
acted on only when the *other* PDF names it with a matching heading or title
and the cross-encoder confirms: at or above the auto threshold its objects are
placed into one concept, and between the review threshold and that the teacher
sees a card first. Nothing is merged -- each object keeps its own text.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Q

from ..models import (
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .learning_resource_linker import normalize_learning_object_title
from .concept_bundles import bundle_label, bundle_lead, bundle_text
from . import semantic_grouping

METHOD = "heading_unit_v1"


def heading_key(text):
    """A heading or title reduced to what two PDFs would share."""
    return semantic_grouping._singular_label(normalize_learning_object_title(text or ""))


@dataclass(frozen=True)
class Unit:
    label: str
    members: tuple

    @property
    def material_id(self):
        return self.members[0].material_id

    @property
    def ids(self):
        return [item.id for item in self.members]


def find_units(objects):
    """Runs of two or more consecutive objects under one heading.

    A run continues while the next object sits under the same section heading,
    or carries that heading as its own title -- extraction sometimes drops the
    section of a figure ("5. Comparing the Three States") but keeps its title.
    """
    ordered = sorted(objects, key=lambda item: (item.order, item.id))
    units, index = [], 0
    while index < len(ordered):
        first = ordered[index]
        label = heading_key(first.section_title) or heading_key(first.title)
        end = index + 1
        while label and end < len(ordered) and label in (
            heading_key(ordered[end].section_title),
            heading_key(ordered[end].title),
        ):
            end += 1
        if end - index >= 2:
            units.append(Unit(label, tuple(ordered[index:end])))
            index = end
        else:
            index += 1
    return units


def _connected_across_pdfs(objects):
    """How many of these objects sit in a concept another PDF already reaches.

    Read from the database rather than from the passed rows: one refresh places
    several units in turn, and an earlier placement changes the answer for a
    later candidate.
    """
    rows = LearningObject.objects.filter(
        pk__in=[item.id for item in objects],
    ).values_list("group_id", "material_id")
    return sum(
        1 for group_id, material_id in rows
        if group_id and LearningObject.objects.filter(group_id=group_id)
        .exclude(material_id=material_id).exists()
    )


def _agrees_across_pdfs(unit):
    """Two or more members already connected to other PDFs: they agree at a finer grain."""
    return _connected_across_pdfs(unit.members) >= 2


def heading_unit_candidates(node):
    """``[{"label", "left": [...], "right": [...]}]`` -- unit pairs worth scoring.

    An object whose ``represented_by`` points elsewhere is a suppressed
    duplicate -- ``semantic_grouping`` skips those too. It never joins a unit,
    because placing it would clear that pointer and put it back in the lesson.
    """
    objects = list(
        LearningObject.objects.filter(
            material__outline_node=node,
            material__generated_json__learning_objects_confirmed=True,
            represented_by__isnull=True,
        ).select_related("material")
    )
    by_material = defaultdict(list)
    for item in objects:
        by_material[item.material_id].append(item)
    all_units = {material_id: find_units(rows) for material_id, rows in by_material.items()}
    open_units = {
        material_id: [unit for unit in units if not _agrees_across_pdfs(unit)]
        for material_id, units in all_units.items()
    }

    candidates, seen = [], set()
    for left_id, units in open_units.items():
        for right_id, rows in by_material.items():
            if right_id == left_id:
                continue
            for unit in units:
                same_label_members = {
                    item.id for other in all_units[right_id] if other.label == unit.label
                    for item in other.members
                }
                singles = [
                    item for item in rows
                    if heading_key(item.title) == unit.label and item.id not in same_label_members
                ]
                others = [other for other in open_units[right_id] if other.label == unit.label]
                if len(singles) + len(others) != 1:
                    continue
                right = list(others[0].members) if others else singles
                pair = frozenset((*unit.ids, *(item.id for item in right)))
                if pair in seen:
                    continue
                seen.add(pair)
                left, right_rows = (list(unit.members), right)
                if left_id > right_id:
                    left, right_rows = right_rows, left
                candidates.append({"label": unit.label, "left": left, "right": right_rows})
    return candidates


def _resolve_unit_pairing(source, source_side, candidate_row, candidate_side):
    """Never modify a suggestion row whose status is not PENDING.

    The natural kept-row pair may already be occupied by a real decision --
    most commonly a teacher-accepted one-to-one match that happens to share
    the same kept rows as this unit. When that happens, re-key the unit
    suggestion onto an alternate member (lowest order) of whichever side
    still has extras, preferring the candidate side when both do, so the
    occupied pair is left untouched. Returns ``None`` when no alternate
    pairing is available or that alternate is itself occupied.
    """
    existing = LearningObjectMatchSuggestion.objects.filter(
        source_learning_object=source, candidate_learning_object=candidate_row,
    ).first()
    if existing is None or existing.status == LearningObjectMatchSuggestion.Status.PENDING:
        return source, source_side, candidate_row, candidate_side
    if (
        existing.status == LearningObjectMatchSuggestion.Status.REJECTED
        and (existing.evidence or {}).get("method") == METHOD
    ):
        # Our own rejected unit suggestion: leave the natural key so the
        # caller sees the decision and skips the pair.
        return source, source_side, candidate_row, candidate_side

    if len(candidate_side) > 1:
        target_is_source, target_primary, target_side = False, candidate_row, candidate_side
        other_primary, other_side = source, source_side
    elif len(source_side) > 1:
        target_is_source, target_primary, target_side = True, source, source_side
        other_primary, other_side = candidate_row, candidate_side
    else:
        return None

    alternates = sorted(
        (item for item in target_side if item.id != target_primary.id),
        key=lambda item: (item.order, item.id),
    )
    if not alternates:
        return None
    new_primary = alternates[0]

    if target_is_source:
        new_source, new_source_side = new_primary, target_side
        new_candidate, new_candidate_side = other_primary, other_side
    else:
        new_source, new_source_side = other_primary, other_side
        new_candidate, new_candidate_side = new_primary, target_side

    if new_source.id > new_candidate.id:
        new_source, new_candidate = new_candidate, new_source
        new_source_side, new_candidate_side = new_candidate_side, new_source_side

    new_existing = LearningObjectMatchSuggestion.objects.filter(
        source_learning_object=new_source, candidate_learning_object=new_candidate,
    ).first()
    if new_existing and new_existing.status != LearningObjectMatchSuggestion.Status.PENDING:
        return None
    return new_source, new_source_side, new_candidate, new_candidate_side


def place_unit(objects, target_group):
    """Move every object of a unit into one concept.

    Placement replaces the old merge: the objects keep their own text and
    order, and the concept simply holds them all.

    The move itself is the easy half. A concept it leaves keeps bookkeeping
    about the object that is going: members it represented would keep pointing
    at an object that is no longer in their concept, and every consumer of the
    published lesson filters on ``represented_by__isnull=True`` -- so those
    companions would silently vanish from the lesson, its audio and the mobile
    package. The release therefore happens here, for every caller, rather than
    in one of the three.
    """
    from course.version_assignment import prune_bundle_role, release_from_group

    movers = [item for item in objects if item.group_id != target_group.id]
    if not movers:
        return
    moving_ids = {item.id for item in movers}

    # A material arriving in a concept it has no object in yet brings no
    # history with it: any role stored for it is a ruling about text that has
    # since left, and inheriting it would hand a teacher's old decision to
    # wording nobody has looked at.
    present = set(
        target_group.learning_objects.exclude(pk__in=moving_ids).values_list(
            "material_id", flat=True,
        )
    )
    for material_id in {item.material_id for item in movers} - present:
        prune_bundle_role(target_group, material_id)

    by_group = defaultdict(list)
    for item in movers:
        if item.group_id:
            by_group[item.group_id].append(item)
    for group_id, leaving in by_group.items():
        staying = list(
            LearningObject.objects.filter(group_id=group_id)
            .exclude(pk__in=moving_ids)
            .select_related("material")
        )
        if not staying:
            continue
        for item in leaving:
            release_from_group(item, staying)
        # ``release_from_group`` undoes what each leaver is responsible for one
        # at a time; this is the guarantee for the whole move -- no object that
        # stays is taught through one that left.
        LearningObject.objects.filter(
            group_id=group_id, represented_by_id__in=moving_ids,
        ).exclude(pk__in=moving_ids).update(represented_by=None)
        gone = {item.material_id for item in leaving} - {
            item.material_id for item in staying
        }
        if gone:
            # Re-read: the releases above rewrite this concept's selection.
            group = LearningObjectGroup.objects.filter(pk=group_id).first()
            for material_id in gone:
                prune_bundle_role(group, material_id)

    emptied = {item.group_id for item in movers if item.group_id}
    for item in movers:
        item.group = target_group
        item.represented_by = None
        item.mark_grouping_current()
        item.save(update_fields=["group", "represented_by", "grouping_content_hash"])
    LearningObjectGroup.objects.filter(
        pk__in=emptied, learning_objects__isnull=True,
    ).delete()


def rejected_across_sides(left, right, exclude_pk=None):
    """A teacher declined connecting one of these objects to one of those.

    Only cross-side pairs count: a decline *within* one PDF's run says nothing
    about whether the two PDFs teach the same concept, and treating it as a
    veto would strand the unit forever.
    """
    left_ids = [item.id for item in left]
    right_ids = [item.id for item in right]
    rows = LearningObjectMatchSuggestion.objects.filter(
        status=LearningObjectMatchSuggestion.Status.REJECTED,
    ).filter(
        Q(source_learning_object_id__in=left_ids, candidate_learning_object_id__in=right_ids)
        | Q(source_learning_object_id__in=right_ids, candidate_learning_object_id__in=left_ids)
    )
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return rows.exists()


def _label_is_locked(objects):
    """A teacher named one of these concepts: only a teacher may dissolve it."""
    group_ids = {item.group_id for item in objects if item.group_id}
    return bool(group_ids) and LearningObjectGroup.objects.filter(
        pk__in=group_ids, version_selection__label_locked=True,
    ).exists()


def unpublish_topic(node):
    """A placement changes what the lesson teaches, so the topic leaves print.

    The flag is read from the database because callers hold cached topic
    instances; mirrors ``regrouping.apply_regrouping``.
    """
    node_id = getattr(node, "pk", node)
    if node_id is None:
        return False
    return bool(
        OutlineNode.objects.filter(pk=node_id, published=True)
        .update(published=False, published_at=None)
    )


def refresh_heading_unit_suggestions(node, runtime_instance=None):
    """Place confident units, raise a card for the rest.

    Returns ``{"placed": n, "pending": n}``. A unit at or above the auto
    threshold is placed into one concept straight away; between the review
    threshold and that, a teacher reviews a card first.
    """
    engine = runtime_instance or semantic_grouping.runtime()
    config = semantic_grouping.policy()
    threshold = config["review_threshold"]
    auto = config["auto_threshold"]
    automatic = semantic_grouping.mode() == "auto" and auto is not None
    placed, pending = 0, 0
    for candidate in heading_unit_candidates(node):
        left, right = candidate["left"], candidate["right"]
        try:
            score = engine.pair_scores([(bundle_text(left), bundle_text(right))])[0]
        except Exception:  # noqa: BLE001 -- model limits: no suggestion
            continue
        if score < threshold:
            continue
        if rejected_across_sides(left, right):
            # A teacher's decline outranks the score: no placement, no card.
            continue
        if any(_connected_across_pdfs(side) >= 2 for side in (left, right)):
            # The same rule ``open_units`` applies, re-read from the database:
            # an earlier placement in this very pass can have connected a side
            # since the candidates were listed.
            continue
        if automatic and score >= auto and not _label_is_locked([*left, *right]):
            target = next(
                (item.group for item in [*left, *right] if item.group_id), None,
            ) or LearningObjectGroup.objects.create(
                # A run often opens with a figure whose own title names
                # nothing, so a run of two or more is called after its
                # heading; a lone object keeps its own title (design 3.5).
                outline_node=node,
                label=(bundle_label(left) or bundle_label(right))[:255],
            )
            place_unit([*left, *right], target)
            placed += 1
            continue
        lead_left, lead_right = bundle_lead(left), bundle_lead(right)
        source, candidate_row = (lead_left, lead_right) if lead_left.id < lead_right.id else (lead_right, lead_left)
        source_side, candidate_side = (left, right) if source is lead_left else (right, left)

        pairing = _resolve_unit_pairing(source, source_side, candidate_row, candidate_side)
        if pairing is None:
            continue
        source, source_side, candidate_row, candidate_side = pairing
        source_extra = sorted(item.id for item in source_side if item.id != source.id)
        candidate_extra = sorted(item.id for item in candidate_side if item.id != candidate_row.id)

        existing = LearningObjectMatchSuggestion.objects.filter(
            source_learning_object=source, candidate_learning_object=candidate_row,
        ).first()
        if existing and existing.status != LearningObjectMatchSuggestion.Status.PENDING:
            # A teacher decision is never overwritten, even when the unit's
            # members have changed since it was declined.
            continue
        LearningObjectMatchSuggestion.objects.update_or_create(
            source_learning_object=source,
            candidate_learning_object=candidate_row,
            defaults={
                "outline_node": node,
                "similarity_score": round(score, 6),
                "confidence": LearningObjectMatchSuggestion.Confidence.MEDIUM,
                "status": LearningObjectMatchSuggestion.Status.PENDING,
                "source_extra_ids": source_extra,
                "candidate_extra_ids": candidate_extra,
                "evidence": {
                    "method": METHOD,
                    "label": candidate["label"],
                    "score": round(score, 6),
                    "source_ids": [item.id for item in source_side],
                    "candidate_ids": [item.id for item in candidate_side],
                    "title_used": True,
                },
            },
        )
        pending += 1
    if placed:
        unpublish_topic(node)
    return {"placed": placed, "pending": pending}
