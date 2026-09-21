"""Read a topic's published learning path -- the contract for the adaptive rules.

``get_published_path`` is the single source of truth. Python code in this
backend calls it directly; anything else (a student app, a frontend) reads the
same data from ``GET /api/learning-path/topics/<id>/published/``, which only
wraps it. Keeping one function behind both means the database layout can change
without breaking either reader, as long as the returned shape stays the same.

See ``learning_path/HANDOFF.md`` for the shape and how to use it.
"""

from collections import defaultdict

from course.models import bundle_segments, normal_bundle_for
from course.services import _generated_versions, _version_from_segments
from course.version_assignment import assign_group_versions, version_bundles
from question_generation.models import GeneratedQuestion

from ..models import ConceptPrerequisite, LearningPathStep


def _representative(group, members):
    """The member whose text is the concept's Normal version."""
    try:
        representative_id = assign_group_versions(group).get("representative_id")
    except Exception:  # noqa: BLE001 -- a readable path beats a failed request
        representative_id = None
    by_id = {member.id: member for member in members}
    if representative_id in by_id:
        return by_id[representative_id]
    return next((m for m in members if m.represented_by_id is None), members[0] if members else None)


def _slot(segments):
    """One version, as the documented keys plus the segments behind them.

    ``text`` and ``audio_url`` are what they always were, so existing readers
    are unaffected. ``segments`` is added because a version can be several
    objects: a single ``audio_url`` is only the first clip, and a reader that
    played it alone would give a learner one quarter of the version and no way
    to tell. The segments are what the lesson package already serves.
    """
    version = _version_from_segments(segments, origin=None)
    return {
        "text": version["text"],
        "audio_url": version["audio_url"],
        "segments": version["segments"],
    }


def _versions(representative):
    """The concept's three versions, each read as the bundle it is taught as.

    Both halves of this used to read the wrong thing. Normal was
    ``representative.content``, which is the bundle's *lead* and drops every
    object after it. Simplified and Elaborated were read from ``LessonVariant``
    rows, and a version another PDF supplies has no such row by design -- its
    text is its objects -- so that PDF's wording never reached the path at all.

    The helpers below are the lesson package's own, imported rather than
    copied: the rule that a generated version short of its bundle is reported
    missing instead of half-served is safety-critical, and it must not exist in
    two places that can drift.
    """
    normal_objects = normal_bundle_for(representative)
    versions = {
        "normal": _slot(bundle_segments(normal_objects)),
        "simplified": None,
        "elaborated": None,
    }

    for role, generated in _generated_versions(normal_objects).items():
        if role.lower() in versions:
            versions[role.lower()] = _slot(generated["segments"])

    # A version a PDF supplies is that PDF's own objects, and it outranks
    # anything generated for the same role -- including wording generated
    # before a teacher connected the two bundles, which outlives the regroup.
    if representative.group_id is not None:
        for role, objects in version_bundles(representative.group).items():
            if role in ("NORMAL", "EXTRA"):
                continue
            versions[role.lower()] = _slot(bundle_segments(objects))

    return versions


def _questions(representative, include_answers):
    questions = []
    for question in GeneratedQuestion.objects.filter(node=representative, status="final").order_by("id"):
        entry = {
            "id": question.id,
            "text": question.question_text,
            "format": question.question_format,
            "choices": question.choices,
            "bloom_level": question.bloom_level,
            "thinking_order": question.thinking_order,
            "difficulty": question.difficulty,
            "category": question.category,
        }
        if include_answers:
            entry["correct_answer"] = question.correct_answer
            entry["explanation"] = question.explanation
        questions.append(entry)
    return questions


def get_published_path(node, *, include_answers=True):
    """The topic's saved learning path, or ``None`` if it was never published.

    ``include_answers`` adds each question's correct answer and explanation.
    Server-side rules need them; anything sent to a student's device should not
    have them.
    """
    steps = list(
        LearningPathStep.objects.filter(outline_node=node)
        .select_related("concept")
        .order_by("position")
    )
    if not steps:
        return None

    concept_ids = {step.concept_id for step in steps}
    position_of = {step.concept_id: step.position for step in steps}
    needs, leads = defaultdict(list), defaultdict(list)
    for row in ConceptPrerequisite.objects.filter(
        outline_node=node,
        status__in=ConceptPrerequisite.SHAPES_PATH,
        prerequisite_id__in=concept_ids,
        dependent_id__in=concept_ids,
    ):
        needs[row.dependent_id].append(row.prerequisite_id)
        leads[row.prerequisite_id].append(row.dependent_id)

    payload_steps = []
    for step in steps:
        group = step.concept
        members = list(group.learning_objects.select_related("material").order_by("material_id", "order", "id"))
        representative = _representative(group, members)
        if representative is None:
            continue
        sources = {}
        for member in members:
            sources.setdefault(member.material_id, member.material.title)
        payload_steps.append({
            "position": step.position,
            "depth": step.depth,
            "concept_id": group.id,
            "title": representative.title,
            "section_title": representative.section_title,
            "learning_object_id": representative.id,
            "sources": [{"material_id": mid, "title": title} for mid, title in sources.items()],
            "versions": _versions(representative),
            "questions": _questions(representative, include_answers),
            "prerequisites": sorted(needs[group.id], key=position_of.get),
            "leads_to": sorted(leads[group.id], key=position_of.get),
        })

    return {
        "topic": {"id": node.id, "title": node.title},
        "published": node.published,
        "published_at": steps[0].published_at.isoformat(),
        "includes_answers": include_answers,
        "steps": payload_steps,
    }
