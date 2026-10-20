"""Jean's section-bundle concept grouping using stored SBERT vectors."""

import math
import re
from collections import defaultdict

from django.db import transaction

from lessons.models import LearningMaterial, LearningObject, LearningObjectGroup


AUTO_GROUP_THRESHOLD = 0.60
STRONG_SEMANTIC_THRESHOLD = 0.85
GENERIC_SECTIONS = {
    "example", "examples", "everyday examples", "introduction", "overview",
    "summary", "conclusion", "key properties", "properties", "lesson text",
}


def normalize_learning_object_title(value):
    """Return a topic-neutral title key shared by grouping and evaluation."""
    value = re.sub(r"\bpart\s+\d+\s*(?:of|/)\s*\d+\b", " ", (value or "").casefold())
    value = re.sub(
        r"^\s*(?:(?:unit|lesson|chapter|section|topic)\s*)?"
        r"\d+(?:\.\d+)*\s*[.):-]*\s*",
        "",
        value,
    )
    value = re.sub(r"^\s*[a-z]\s*[.)]\s+(?=\S)", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def normalize_section(title):
    title = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", title or "")
    title = re.sub(r"^what (?:is|are)\s+", "", title.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", title))


def matching_section(obj):
    if normalize_section(obj.section_title):
        return obj.section_title
    if obj.kind != LearningObject.Kind.IMAGE:
        return ""
    caption = re.sub(
        r"^\s*(?:figure|fig\.?)\s*\d+[a-z]?\b\s*[.:-]?\s*",
        "",
        obj.source_excerpt or "",
        flags=re.IGNORECASE,
    ).strip()
    return caption if len(re.findall(r"[a-z]+", caption.casefold())) >= 4 else ""


def same_section(first, second):
    first, second = normalize_section(first), normalize_section(second)
    if not first or not second or first in GENERIC_SECTIONS or second in GENERIC_SECTIONS:
        return False
    left, right = first.split(), second.split()
    if len(left) != len(right):
        return False

    def forms(word):
        plural = word[:-1] + "ies" if word.endswith("y") else word + "s"
        return {word, plural, word + "es"}

    return all(a == b or b in forms(a) or a in forms(b) for a, b in zip(left, right))


def cosine_similarity(first_vector, second_vector):
    if not first_vector or not second_vector or len(first_vector) != len(second_vector):
        return None
    return sum(a * b for a, b in zip(first_vector, second_vector))


def section_bundles(objects):
    bundles = []
    for obj in objects:
        section = normalize_section(obj.section_title)
        if bundles and section and section == normalize_section(bundles[-1][0].section_title):
            bundles[-1].append(obj)
        else:
            bundles.append([obj])
    return bundles


def bundle_embedding(objects):
    vectors = [obj.embedding for obj in objects if obj.embedding]
    if not vectors or len({len(vector) for vector in vectors}) != 1:
        return []
    mean = [sum(values) / len(vectors) for values in zip(*vectors)]
    norm = math.sqrt(sum(value * value for value in mean))
    return [value / norm for value in mean] if norm else []


def score_section_bundle(bundle, members, engine=None):
    if not members:
        return None
    section = matching_section(bundle[0])
    member_sections = [matching_section(member) for member in members]
    sections_match = all(same_section(section, value) for value in member_sections)
    has_section_context = bool(normalize_section(section)) and all(
        normalize_section(value) for value in member_sections
    )
    by_material = defaultdict(list)
    for member in members:
        if member.material_id == bundle[0].material_id:
            return None
        by_material[member.material_id].append(member)

    vector = bundle_embedding(bundle)
    scores = [
        cosine_similarity(vector, bundle_embedding(objects))
        for objects in by_material.values()
    ]
    if not scores or any(score is None for score in scores):
        return None
    score = min(scores)
    if sections_match:
        return score
    if has_section_context and score >= STRONG_SEMANTIC_THRESHOLD:
        return score
    return None


@transaction.atomic
def group_material_learning_objects(material, *, runtime_instance=None):
    if material.outline_node_id is None:
        raise ValueError("Match the material to a subtopic first.")
    groups = list(
        LearningObjectGroup.objects.filter(outline_node_id=material.outline_node_id)
        .prefetch_related("learning_objects")
        .order_by("id")
    )
    members = {group.pk: list(group.learning_objects.all()) for group in groups}
    objects = list(material.learning_objects.order_by("order", "id"))
    created_count = joined_count = 0
    for bundle in section_bundles(objects):
        if any(obj.group_id is not None for obj in bundle):
            continue
        ranked = []
        for group in groups:
            score = score_section_bundle(bundle, members[group.pk])
            if score is not None:
                ranked.append((score, group))
        ranked.sort(key=lambda row: (-row[0], row[1].pk))
        if ranked and ranked[0][0] >= AUTO_GROUP_THRESHOLD:
            group = ranked[0][1]
            joined_count += len(bundle)
        else:
            group = LearningObjectGroup.objects.create(
                outline_node_id=material.outline_node_id,
                label=(matching_section(bundle[0]) or bundle[0].title)[:255],
            )
            groups.append(group)
            members[group.pk] = []
            created_count += 1

        LearningObject.objects.filter(pk__in=[obj.pk for obj in bundle]).update(group=group)
        for obj in bundle:
            obj.group_id = group.pk
            obj.mark_grouping_current()
        LearningObject.objects.bulk_update(bundle, ["grouping_content_hash"])
        members[group.pk].extend(bundle)
    return {
        "created_groups": created_count,
        "joined_existing_groups": joined_count,
    }


# Jean's original public name.
group_learning_objects = group_material_learning_objects


def learning_objects_are_confirmed(material):
    return bool((material.generated_json or {}).get("learning_objects_confirmed"))


def remove_empty_learning_object_groups(material):
    if material.outline_node_id is not None:
        LearningObjectGroup.objects.filter(
            outline_node_id=material.outline_node_id,
            learning_objects__isnull=True,
        ).delete()


def refresh_material_learning_relationships(material):
    """Rebuild concept groups and downstream question links after an edit."""
    if material.outline_node_id is None:
        return
    group_material_learning_objects(material)
    remove_empty_learning_object_groups(material)
    from .question_linker import refresh_question_learning_object_links

    refresh_question_learning_object_links(material)
