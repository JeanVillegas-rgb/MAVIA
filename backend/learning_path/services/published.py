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
from lessons.services.concept_bundles import bundles_for_group

from .concept_units import concepts_for_topic
from .text_signals import part_marker, strip_part_suffix


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


def _passage_parts(anchor, members):
    """The whole split passage ``anchor`` belongs to, in reading order.

    The chunker cuts an oversized passage into "(Part 1 of 2)" pieces, and
    ``concept_units`` merges those pieces back into one concept -- but a
    ``LearningPathStep`` only records the first piece's group. Reading just
    that group lost every later part: its narration never reached the student,
    and when question generation happened to put the concept's questions on a
    later part, the step had none and the engine skipped the concept outright.

    Parts are taken from the concept's own members, same material and same
    passage title, split into runs wherever the numbering restarts at 1, so a
    file carrying several passages that share one title ("Diagram description
    (Part 1 of 2)" under each state of matter) never fuses them. A chunk that
    was never split is its own single part.
    """
    marker = part_marker(anchor.title)
    if not marker:
        return [anchor]
    base, _number, total = marker
    candidates = []
    for member in members:
        member_marker = part_marker(member.title)
        if (
            member_marker
            and member.material_id == anchor.material_id
            and member_marker[0].casefold() == base.casefold()
            and member_marker[2] == total
        ):
            candidates.append((member.order, member_marker[1], member))
    candidates.sort(key=lambda entry: entry[0])

    runs, current = [], []
    for _order, number, member in candidates:
        if number == 1 and current:
            runs.append(current)
            current = []
        current.append((number, member))
    if current:
        runs.append(current)
    for run in runs:
        if any(member.id == anchor.id for _number, member in run):
            return [member for _number, member in sorted(run, key=lambda entry: entry[0])]
    return [anchor]


def _telling(anchor, members, bundle):
    """Every object one telling of a concept is taught as, in reading order.

    ``bundle`` is the telling's PDF bundle -- the objects of this concept from
    that PDF. A split passage whose later parts were grouped on their own is
    still one telling, so those parts are added; for a passage the bundle
    already holds whole, that adds nothing.
    """
    by_id = {item.id: item for item in bundle}
    for part in _passage_parts(anchor, members):
        by_id.setdefault(part.id, part)
    return sorted(by_id.values(), key=lambda item: (item.order, item.id))


def _chunk_title(parts):
    """A split passage is one idea to the student, so it is named without the
    "(Part 1 of 2)" the chunker added."""
    title = parts[0].title or ""
    return strip_part_suffix(title) if len(parts) > 1 else title


def _legacy_audio(parts, segments):
    """Fill Normal clips a playlist written before objects were named in it.

    A playlist entry now names the object it speaks for, and that is how
    ``bundle_segments`` finds a clip. Materials processed before that have
    entries keyed only by position -- built 1:1 and in order from the same
    narration script as the objects, so an object's 0-indexed ``order`` is
    the entry's 1-indexed ``narration_item_order``. Only such a playlist is
    read this way: one that names any object is never matched by position.
    """
    lookups = {}
    filled = []
    for part, segment in zip(parts, segments):
        if segment["audio_url"]:
            filled.append(segment)
            continue
        lookup = lookups.get(part.material_id)
        if lookup is None:
            playlist = (part.material.generated_json or {}).get("lesson_playlist", [])
            lookup = lookups[part.material_id] = {} if any(
                entry.get("learning_object_id") for entry in playlist
            ) else {
                entry.get("narration_item_order"): entry.get("audio_url") or ""
                for entry in playlist
                if entry.get("narration_item_order") is not None
            }
        filled.append({**segment, "audio_url": lookup.get(part.order + 1, "")})
    return filled


def _slot(segments):
    """One version, as the documented keys plus the segments behind them.

    ``parts`` and ``segments`` are the same list of ``{text, audio_url}``, one
    per object, which is what a player should play: ``parts`` is the key the
    student app reads, ``segments`` the one the lesson package serves.
    ``audio_url`` is kept for readers that predate both and is only filled
    when a single clip really covers all of ``text`` -- a reader that played
    the first of several clips alone would give a learner part of the version
    and no way to tell.
    """
    version = _version_from_segments(segments, origin=None)
    return {
        # Parts are separated by a blank line, as this contract always has.
        "text": "\n\n".join(segment["text"] for segment in segments if segment["text"]),
        "audio_url": segments[0]["audio_url"] if len(segments) == 1 else "",
        "parts": version["segments"],
        "segments": version["segments"],
    }


def _versions(parts, group=None):
    """Normal / simplified / elaborated for one telling of a concept.

    Normal is every object of the telling, joined. Simplified and Elaborated
    are generated per object; a rung short of any object is left out rather
    than half-served, so the player falls back to Normal instead. The helpers
    are the lesson package's own, imported rather than copied, so that rule
    exists in one place.

    ``group`` is passed only for the concept's Normal telling: a version
    another PDF supplies is that PDF's own objects, and it outranks anything
    generated for the same role -- including wording generated before a
    teacher connected the two bundles, which outlives the regroup.
    """
    versions = {
        "normal": _slot(_legacy_audio(parts, bundle_segments(parts))),
        "simplified": None,
        "elaborated": None,
    }

    for role, generated in _generated_versions(parts).items():
        if role.lower() in versions:
            versions[role.lower()] = _slot(generated["segments"])

    if group is not None:
        for role, objects in version_bundles(group).items():
            if role in ("NORMAL", "EXTRA"):
                continue
            versions[role.lower()] = _slot(bundle_segments(objects))

    return versions


def _questions(parts, include_answers):
    # One step, one assessment: the earliest-generated LOT question and the
    # earliest-generated HOT question, LOT first -- never more than 2, even
    # if question generation left extra final rows on this node (it isn't
    # guaranteed to cap itself at one per thinking_order). Drawn from every
    # part of the telling: generation attaches questions to whichever object
    # it was reading, which is often not the first.
    by_order = {}
    for question in GeneratedQuestion.objects.filter(node__in=parts, status="final").order_by("id"):
        order = question.thinking_order or "LOT"
        by_order.setdefault(order, question)

    questions = []
    for order in ("LOT", "HOT"):
        question = by_order.get(order)
        if question is None:
            continue
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

    # The same concept units publishing ordered, so a step sees every member
    # its concept really has -- including later parts of a split passage,
    # which live in groups of their own that no step points at.
    concepts = {concept.id: concept for concept in concepts_for_topic(node)}

    payload_steps = []
    for step in steps:
        group = step.concept
        concept = concepts.get(group.id)
        if concept is not None:
            members = sorted(concept.members, key=lambda item: (item.material_id, item.order, item.id))
            representative = concept.representative
        else:
            # A step whose group no longer forms a concept (edited since the
            # path was published): read the group itself, as before.
            members = list(
                group.learning_objects.select_related("material").order_by("material_id", "order", "id")
            )
            representative = _representative(group, members)
        if representative is None:
            continue
        sources = {}
        for member in members:
            sources.setdefault(member.material_id, member.material.title)

        parts = _telling(representative, members, normal_bundle_for(representative))
        bundles = bundles_for_group(group)
        # Independent alternates: other PDFs' own take on this concept, not
        # folded into another member's telling (`represented_by` marks that).
        # The adaptive engine's remediation ladder reaches for one of these
        # when re-explaining the representative's own text hasn't worked --
        # see adaptive/PATH_MODE.md "chunk switching". Each is a whole telling
        # too -- its own PDF bundle -- and a later part of any passage is never
        # an alternate of its own. A PDF that supplies one of this concept's
        # versions is marked represented, so it is a rung, not an alternate.
        seen = {part.id for part in parts}
        alternates = []
        for member in members:
            if member.id in seen or member.represented_by_id is not None:
                continue
            alternate_parts = _telling(member, members, bundles.get(member.material_id) or [member])
            if any(part.id in seen for part in alternate_parts):
                continue
            seen.update(part.id for part in alternate_parts)
            alternates.append(alternate_parts)

        payload_steps.append({
            "position": step.position,
            "depth": step.depth,
            "concept_id": group.id,
            "title": _chunk_title(parts),
            "section_title": representative.section_title,
            "learning_object_id": representative.id,
            "sources": [{"material_id": mid, "title": title} for mid, title in sources.items()],
            "versions": _versions(parts, representative.group if representative.group_id is not None else None),
            "questions": _questions(parts, include_answers),
            "alternates": [
                {
                    "learning_object_id": alternate_parts[0].id,
                    "material_id": alternate_parts[0].material_id,
                    "material_title": alternate_parts[0].material.title,
                    "title": _chunk_title(alternate_parts),
                    "versions": _versions(alternate_parts),
                    "questions": _questions(alternate_parts, include_answers),
                }
                for alternate_parts in alternates
            ],
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
