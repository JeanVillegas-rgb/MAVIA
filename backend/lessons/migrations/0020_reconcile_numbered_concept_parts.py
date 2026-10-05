"""Join safe, complete numbered-part concepts in existing courses."""

import re

from django.db import migrations
from django.db.models import F


PART = re.compile(r"\s*\(\s*part\s+(\d+)\s*(?:of|/)\s*(\d+)\s*\)\s*$", re.I)
AUTOMATIC_KEYS = {"normal_material_id", "normal_assigned_by", "auto_label"}


def _key(value):
    value = PART.sub("", value or "")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def reconcile_existing_parts(apps, schema_editor):
    Object = apps.get_model("lessons", "LearningObject")
    Group = apps.get_model("lessons", "LearningObjectGroup")
    Material = apps.get_model("lessons", "LearningMaterial")
    Suggestion = apps.get_model("lessons", "LearningObjectMatchSuggestion")
    Topic = apps.get_model("lessons", "OutlineNode")
    Prerequisite = apps.get_model("learning_path", "ConceptPrerequisite")
    PathStep = apps.get_model("learning_path", "LearningPathStep")
    CourseLink = apps.get_model("learning_path", "CourseConceptLink")
    db = schema_editor.connection.alias
    affected = set()

    for material in Material.objects.using(db).filter(outline_node__isnull=False).iterator():
        if not (material.generated_json or {}).get("learning_objects_confirmed"):
            continue
        rows = list(Object.objects.using(db).filter(material_id=material.pk).order_by("order", "id"))
        for index, first in enumerate(rows):
            marker = PART.search(first.title or "")
            if not marker or int(marker.group(1)) != 1:
                continue
            total = int(marker.group(2))
            if total < 2 or index + total > len(rows):
                continue
            base = _key(first.title[:marker.start()])
            series = rows[index:index + total]
            if not base or not all(
                row.kind == first.kind
                and row.represented_by_id is None
                and row.order == first.order + offset
                and (row.section_title or "").strip().casefold()
                    == (first.section_title or "").strip().casefold()
                and (part := PART.search(row.title or "")) is not None
                and int(part.group(1)) == offset + 1
                and int(part.group(2)) == total
                and _key(row.title[:part.start()]) == base
                for offset, row in enumerate(series)
            ):
                continue
            group_ids = {row.group_id for row in series}
            if None in group_ids or len(group_ids) < 2:
                continue
            groups = list(Group.objects.using(db).filter(pk__in=group_ids))
            if len(groups) != len(group_ids):
                continue
            target_id = first.group_id
            target = next(group for group in groups if group.pk == target_id)
            if any(
                group.outline_node_id != material.outline_node_id
                or _key(group.label) != base
                or set(group.version_selection or {}) - AUTOMATIC_KEYS
                or group.version_selection != target.version_selection
                for group in groups
            ):
                continue
            if (
                PathStep.objects.using(db).filter(concept_id__in=group_ids).exists()
                or Prerequisite.objects.using(db).filter(prerequisite_id__in=group_ids).exists()
                or Prerequisite.objects.using(db).filter(dependent_id__in=group_ids).exists()
                or CourseLink.objects.using(db).filter(prerequisite_id__in=group_ids).exists()
                or CourseLink.objects.using(db).filter(dependent_id__in=group_ids).exists()
                or Suggestion.objects.using(db).filter(
                    status="rejected",
                    source_learning_object__group_id__in=group_ids,
                    candidate_learning_object__group_id__in=group_ids,
                ).exclude(source_learning_object__group_id=F("candidate_learning_object__group_id")).exists()
            ):
                continue
            members = list(Object.objects.using(db).filter(group_id__in=group_ids))
            series_ids = {row.pk for row in series}
            if any(
                row.represented_by_id is not None
                or (row.material_id == material.pk and row.pk not in series_ids)
                for row in members
            ):
                continue
            Object.objects.using(db).filter(group_id__in=group_ids).exclude(group_id=target_id).update(
                group_id=target_id,
            )
            Group.objects.using(db).filter(pk__in=group_ids).exclude(pk=target_id).delete()
            affected.add(material.outline_node_id)

    if affected:
        Topic.objects.using(db).filter(pk__in=affected).update(published=False, published_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("lessons", "0019_drop_merged_from"),
        ("course", "0010_first_relevant_pdf_is_normal"),
        ("learning_path", "0009_courseconceptlink"),
    ]

    operations = [migrations.RunPython(reconcile_existing_parts, migrations.RunPython.noop)]
