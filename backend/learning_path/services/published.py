"""Read a topic's published learning path: the contract for the adaptive engine."""

from collections import defaultdict

from course.models import bundle_segments, standard_bundle_for
from course.services import _generated_versions, _version_from_segments
from course.version_assignment import assign_group_versions, served_version_bundles
from question_generation.models import GeneratedQuestion

from ..models import ConceptPrerequisite, LearningPathStep
from lessons.services.audio_generator import question_audio_url
from lessons.services.concept_bundles import bundles_for_group
from lessons.services.concept_titles import display_titles

from .concept_units import concepts_for_topic
from .text_signals import part_marker, strip_part_suffix


def _representative(group, members):
    """The member whose text is the concept's Standard version."""
    try:
        representative_id = assign_group_versions(group).get("representative_id")
    except Exception:
        representative_id = None
    by_id = {member.id: member for member in members}
    if representative_id in by_id:
        return by_id[representative_id]
    return next((m for m in members if m.represented_by_id is None), members[0] if members else None)


def _passage_parts(anchor, members):
    """Every part of the split passage anchor belongs to, in reading order."""
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
    """Every object one telling of a concept is taught as, in reading order."""
    by_id = {item.id: item for item in bundle}
    for part in _passage_parts(anchor, members):
        by_id.setdefault(part.id, part)
    return sorted(by_id.values(), key=lambda item: (item.order, item.id))


def _chunk_title(parts):
    """A chunk's title without the "(Part n of m)" marker."""
    title = parts[0].title or ""
    return strip_part_suffix(title) if len(parts) > 1 else title


def _legacy_audio(parts, segments):
    """Fill Standard clips from a playlist written before objects were named in it."""
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
    """One version: text, audio_url, parts and segments."""
    version = _version_from_segments(segments, origin=None)
    return {
        "text": "\n\n".join(segment["text"] for segment in segments if segment["text"]),
        "audio_url": segments[0]["audio_url"] if len(segments) == 1 else "",
        "parts": version["segments"],
        "segments": version["segments"],
    }


def _versions(parts, group=None):
    """Standard, simplified and elaborated for one telling of a concept."""
    versions = {
        "standard": _slot(_legacy_audio(parts, bundle_segments(parts))),
        "simplified": None,
        "elaborated": None,
    }

    for role, generated in _generated_versions(parts).items():
        if role.lower() in versions:
            versions[role.lower()] = _slot(generated["segments"])

    if group is not None:
        for role, objects in served_version_bundles(group).items():
            if role == "STANDARD":
                continue
            versions[role.lower()] = _slot(bundle_segments(objects))

    return versions


QUESTIONS_PER_ORDER = {"LOT": 2, "HOT": 1}


def _questions(parts, include_answers):
    """Up to QUESTIONS_PER_ORDER questions per thinking order, earliest first, LOT before HOT."""
    by_order = {}
    for question in GeneratedQuestion.objects.filter(node__in=parts, status="final").select_related("node__material").order_by("id"):
        order = question.thinking_order or "LOT"
        bucket = by_order.setdefault(order, [])
        if len(bucket) < QUESTIONS_PER_ORDER.get(order, 1):
            bucket.append(question)

    questions = []
    for order in ("LOT", "HOT"):
        for question in by_order.get(order, []):
            entry = {
                "id": question.id,
                "text": question.question_text,
                "format": question.question_format,
                "choices": question.choices,
                "bloom_level": question.bloom_level,
                "thinking_order": question.thinking_order,
                "category": question.category,
                "audio_url": question_audio_url(question),
            }
            if include_answers:
                entry["correct_answer"] = question.correct_answer
                entry["explanation"] = question.explanation
            questions.append(entry)
    return questions


def get_published_path(node, *, include_answers=True):
    """The topic's saved learning path, or None if it was never published."""
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

    concepts = {concept.id: concept for concept in concepts_for_topic(node)}

    payload_steps = []
    title_entries = []
    for step in steps:
        group = step.concept
        concept = concepts.get(group.id)
        if concept is not None:
            members = sorted(concept.members, key=lambda item: (item.material_id, item.order, item.id))
            representative = concept.representative
        else:
            members = list(
                group.learning_objects.select_related("material").order_by("material_id", "order", "id")
            )
            representative = _representative(group, members)
        if representative is None:
            continue
        sources = {}
        for member in members:
            sources.setdefault(member.material_id, member.material.title)

        parts = _telling(representative, members, standard_bundle_for(representative))
        bundles = bundles_for_group(group)
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

        title_entries.append((group, members, step.position))
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

    titles = display_titles(title_entries)
    for entry in payload_steps:
        entry["title"] = titles.get(entry["concept_id"]) or entry["title"]

    return {
        "topic": {"id": node.id, "title": node.title},
        "published": node.published,
        "published_at": steps[0].published_at.isoformat(),
        "includes_answers": include_answers,
        "steps": payload_steps,
    }
