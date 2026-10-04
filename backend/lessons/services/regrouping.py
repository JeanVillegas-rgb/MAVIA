"""Review where edited learning objects belong, at the teacher's request.

Group membership is deliberately sticky. A background refresh never takes a
member away from its companions, because a teacher's grouping must not change
behind their back. The cost is that editing a grouped object -- rewriting an
"Examples" passage so it only covers solids, say -- leaves it in a group it may
no longer belong to, and nothing ever notices.

This module is the explicit version of that refresh. It only ever looks at
objects whose text changed since their grouping was decided, it proposes rather
than applies, and the teacher chooses which proposals to carry out.

Three outcomes are possible for an edited object:

* **stay** -- it still matches its group at the automatic-grouping standard;
* **move** -- it no longer matches, and another concept is a clear match;
* **separate** -- it no longer matches and no other concept clearly does.

Proposals are evaluated one object at a time against the topic as it stands.
When two edited objects share a group, each is judged against the other's new
text, and applying one does not re-judge the other.
"""

import logging
from collections import Counter

from django.db import transaction
from django.db.models import Q

from course.version_assignment import (
    bundle_roles,
    group_original_id,
    release_from_group,
)

from ..models import (
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    QuestionLearningObjectLink,
)
from . import semantic_grouping as semantic

logger = logging.getLogger(__name__)

STAY = "stay"
MOVE = "move"
SEPARATE = "separate"
UNSCORED = "unscored"

ACTIONABLE = {MOVE, SEPARATE}

# The standard an edited object must still meet to stay where it is. Grouping
# uses the same number to join objects automatically, so "still fits" means
# exactly what "fits" meant when the group was formed.
DEFAULT_FIT_THRESHOLD = 0.6


class RegroupingUnavailable(Exception):
    """The semantic models this review depends on cannot run."""


def _trace(node, message, *args):
    logger.info("[Regrouping topic %s] " + message, node.id, *args)


def changed_learning_objects(node):
    """Grouped, confirmed objects whose text changed since grouping was decided.

    Only objects that share a group count. A standalone object is already
    re-matched every time its file is confirmed, so it never needs this review.
    Unconfirmed files are skipped: an edit is not settled until the teacher
    confirms the file again, and reviewing a draft would be premature.
    """
    candidates = list(
        LearningObject.objects.filter(
            material__outline_node=node,
            material__generated_json__learning_objects_confirmed=True,
            group__isnull=False,
        )
        .select_related("material", "group")
        .order_by("material_id", "order", "id")
    )
    if not candidates:
        return []

    sizes = Counter(
        LearningObject.objects.filter(
            group_id__in={item.group_id for item in candidates},
        ).values_list("group_id", flat=True)
    )
    return [
        item for item in candidates
        if sizes[item.group_id] > 1
        and item.grouping_content_hash
        and item.grouping_content_hash != item.current_grouping_fingerprint
    ]


def _companions(learning_object):
    return list(
        LearningObject.objects.filter(group_id=learning_object.group_id)
        .exclude(pk=learning_object.pk)
        .select_related("material")
        .order_by("material_id", "order", "id")
    )


def _fit_threshold(config):
    threshold = config.get("auto_threshold")
    return DEFAULT_FIT_THRESHOLD if threshold is None else threshold


def _current_group_fit(learning_object, companions, config):
    """How well the edited text still matches every other member of its group.

    Scored exactly the way grouping scores a candidate group: every member is
    checked, and the group's score is its *weakest* member's.
    """
    ranked = semantic.rank_groups(
        learning_object.content,
        companions,
        {learning_object.group_id: companions},
        thresholds={**config, "top_k": 1},
    )
    if not ranked:
        return {"fits": False, "score": None, "complete": False}
    evidence = ranked[0]["evidence"]
    fits = (
        evidence["all_members_checked"]
        and evidence["score"] >= _fit_threshold(config)
        and evidence["minimum_group_sbert_cosine"] >= config["minimum_sbert_cosine"]
    )
    return {
        "fits": fits,
        "score": evidence["score"],
        "complete": evidence["all_members_checked"],
    }


def _teacher_made(learning_object, companions):
    """True when a teacher placed this object with any of its companions.

    Connecting objects and accepting a suggestion both record the decision as
    teacher-confirmed, so this reads that record rather than guessing.
    """
    ids = [item.id for item in companions]
    return LearningObjectMatchSuggestion.objects.filter(
        Q(source_learning_object=learning_object, candidate_learning_object_id__in=ids)
        | Q(candidate_learning_object=learning_object, source_learning_object_id__in=ids),
        status=LearningObjectMatchSuggestion.Status.ACCEPTED,
        confidence=LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
    ).exists()


def _impact(learning_object, companions):
    """What leaving its group would change, in terms a teacher reviews."""
    members = [learning_object, *companions]
    original_id = group_original_id(learning_object.group, members)
    was_original = original_id == learning_object.id

    # A PDF-supplied version is its own bundle of objects, so what a departure
    # removes is the concept's stored roles, not copied rows.
    roles = bundle_roles(learning_object.group) if learning_object.group else {}
    if was_original:
        removed_slots = list(roles.values())
    elif any(item.material_id == learning_object.material_id for item in companions):
        # The rest of this PDF's bundle stays, and keeps its role with it.
        removed_slots = []
    else:
        role = roles.get(learning_object.material_id)
        removed_slots = [role] if role else []

    return {
        "question_count": QuestionLearningObjectLink.objects.filter(
            learning_object=learning_object,
        ).count(),
        "was_original": was_original,
        "removed_version_slots": sorted({slot.lower() for slot in removed_slots}),
    }


def _percent(score):
    return "?" if score is None else f"{round(score * 100)}%"


def _group_summary(group, member_count):
    return {"id": group.id, "label": group.label, "member_count": member_count}


def _propose(learning_object, config):
    companions = _companions(learning_object)
    group = learning_object.group
    label = group.label or learning_object.title
    proposal = {
        "learning_object_id": learning_object.id,
        "title": learning_object.title,
        "material_id": learning_object.material_id,
        "material_title": learning_object.material.title,
        "current_group": _group_summary(group, len(companions) + 1),
        "destination_group": None,
        "fit_score": None,
        "destination_score": None,
        "teacher_made": _teacher_made(learning_object, companions),
        "impact": _impact(learning_object, companions),
        "suggestion_after": False,
    }

    try:
        fit = _current_group_fit(learning_object, companions, config)
    except semantic.SemanticUnavailable as exc:
        return {
            **proposal,
            "action": UNSCORED,
            "reason": f"Could not be scored automatically ({exc}). Check this group manually.",
        }
    proposal["fit_score"] = fit["score"]

    if fit["fits"]:
        return {
            **proposal,
            "action": STAY,
            "reason": f"Still matches “{label}” ({_percent(fit['score'])}).",
        }

    try:
        decision = semantic.semantic_decision(
            learning_object.material,
            learning_object.title,
            learning_object.content,
            learning_object.kind,
            learning_object.order,
            section_title=learning_object.section_title,
            source_object_id=learning_object.id,
            allow_grouped_source=True,
        )
    except semantic.SemanticUnavailable as exc:
        return {
            **proposal,
            "action": UNSCORED,
            "reason": f"Could not be scored automatically ({exc}). Check this group manually.",
        }

    no_longer = f"No longer matches “{label}” ({_percent(fit['score'])})."
    if decision and decision["confidence"] == "high":
        destination = decision["candidate"].group
        score = decision["evidence"]["score"]
        return {
            **proposal,
            "action": MOVE,
            "destination_group": _group_summary(
                destination,
                LearningObject.objects.filter(group=destination).count(),
            ),
            "destination_score": score,
            "reason": f"{no_longer} Best match is “{destination.label or decision['candidate'].title}” ({_percent(score)}).",
        }

    medium = bool(decision and decision["confidence"] == "medium")
    reason = f"{no_longer} No other concept is a clear match, so it would stand alone."
    if medium:
        reason += " A possible pairing will be offered for you to review afterwards."
    return {
        **proposal,
        "action": SEPARATE,
        "suggestion_after": medium,
        "reason": reason,
    }


def propose_regrouping(node):
    """Proposals for every edited object in this topic. Writes nothing."""
    changed = changed_learning_objects(node)
    if not changed:
        return []
    if semantic.mode() == "legacy":
        raise RegroupingUnavailable("Semantic grouping is switched off (SEMANTIC_GROUPING_MODE=legacy).")
    try:
        config = semantic.policy()
        semantic.runtime()
    except semantic.SemanticUnavailable as exc:
        raise RegroupingUnavailable(str(exc)) from exc

    _trace(node, "Checking %s edited learning object(s)", len(changed))
    proposals = []
    for index, learning_object in enumerate(changed, start=1):
        proposal = _propose(learning_object, config)
        proposal["selectable"] = proposal["action"] in ACTIONABLE
        # A teacher's own grouping wins unless they explicitly overrule it.
        proposal["default_selected"] = proposal["selectable"] and not proposal["teacher_made"]
        _trace(
            node, '(%s/%s) "%s" -> %s: %s',
            index, len(changed), learning_object.title[:60], proposal["action"], proposal["reason"],
        )
        proposals.append(proposal)
    return proposals


def apply_regrouping(node, learning_object_ids):
    """Carry out the chosen proposals and settle every reviewed object.

    Proposals are recomputed rather than taken from the request, so a stale or
    edited payload can never move an object the review would not propose.
    Every reviewed object is marked current -- including ones the teacher chose
    to leave -- because the teacher has now seen them; otherwise the review
    button would stay lit for a decision already made.
    """
    from .learning_resource_linker import record_teacher_match_decision

    proposals = propose_regrouping(node)
    chosen = {int(value) for value in learning_object_ids}
    applied = []
    affected_material_ids = set()

    with transaction.atomic():
        for proposal in proposals:
            learning_object = (
                LearningObject.objects.select_related("material", "group")
                .filter(pk=proposal["learning_object_id"])
                .first()
            )
            if learning_object is None:
                continue

            if proposal["selectable"] and learning_object.id in chosen:
                companions = _companions(learning_object)
                old_group = learning_object.group
                release_from_group(learning_object, companions)

                destination = None
                if proposal["action"] == MOVE:
                    destination = LearningObjectGroup.objects.filter(
                        pk=proposal["destination_group"]["id"],
                        outline_node=node,
                    ).first()
                if destination is None:
                    destination = LearningObjectGroup.objects.create(
                        outline_node=node,
                        label=learning_object.title[:255],
                    )
                    action = SEPARATE
                else:
                    action = MOVE

                learning_object.group = destination
                learning_object.mark_grouping_current()
                learning_object.save(update_fields=["group", "represented_by", "grouping_content_hash"])
                if not old_group.learning_objects.exists():
                    old_group.delete()

                # The teacher approved this, so it is recorded as their decision:
                # background matching will not pull the object back, and will
                # not treat its new companions as an unreviewed guess.
                for companion in companions:
                    record_teacher_match_decision(learning_object, companion, accepted=False)
                if action == MOVE:
                    for member in LearningObject.objects.filter(group=destination).exclude(pk=learning_object.id):
                        record_teacher_match_decision(learning_object, member, accepted=True)

                affected_material_ids.add(learning_object.material_id)
                affected_material_ids.update(item.material_id for item in companions)
                applied.append({**proposal, "action": action})
            else:
                learning_object.mark_grouping_current()
                learning_object.save(update_fields=["grouping_content_hash"])

        unpublished = False
        if applied and node.published:
            node.published = False
            node.published_at = None
            node.save(update_fields=["published", "published_at"])
            unpublished = True

    _trace(
        node, "Applied %s of %s proposal(s)%s",
        len(applied), len(proposals), "; topic unpublished" if unpublished else "",
    )
    return {
        "reviewed_count": len(proposals),
        "applied": applied,
        "unpublished": unpublished,
        "affected_material_ids": sorted(affected_material_ids),
    }
