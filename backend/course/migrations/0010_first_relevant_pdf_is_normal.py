"""Rebase existing concepts onto their first relevant PDF without deleting text."""

from django.db import migrations


def rebase_existing_concepts(apps, schema_editor):
    Group = apps.get_model("lessons", "LearningObjectGroup")
    LearningObject = apps.get_model("lessons", "LearningObject")
    OutlineNode = apps.get_model("lessons", "OutlineNode")
    db = schema_editor.connection.alias
    affected_topics = set()

    for group in Group.objects.using(db).all().iterator():
        objects = LearningObject.objects.using(db).filter(group_id=group.pk).select_related("material").order_by(
            "material__created_at", "material_id", "order", "id",
        )
        relevant_ids = list(dict.fromkeys(
            obj.material_id for obj in objects if (obj.content or "").strip()
        ))
        if not relevant_ids:
            continue

        selection = dict(group.version_selection or {})
        # Keys use the Standard name (the Normal version was renamed Standard;
        # course/0009_rename_normal_to_standard). course/0011 renames any
        # normal_ key an older database still carries.
        stored = selection.get("standard_material_id")
        first = relevant_ids[0]
        if stored is None:
            selection["standard_material_id"] = first
            selection["standard_assigned_by"] = "upload_order"
            group.version_selection = selection
            group.save(using=db, update_fields=["version_selection"])
            continue
        if stored not in relevant_ids:
            # The standard source was removed. Runtime review requires the
            # teacher to choose a replacement; do not promote another PDF.
            affected_topics.add(group.outline_node_id)
            continue
        if selection.get("standard_assigned_by") == "teacher" or stored == first:
            continue

        selection["baseline_rebase_backup"] = dict(selection)
        selection["standard_material_id"] = first
        selection["standard_assigned_by"] = "upload_order"
        selection["bundle_roles"] = {}
        selection["bundle_roles_assigned_by"] = {}
        selection["bundle_roles_decided_at"] = {}
        selection.pop("roles_signature", None)
        selection.pop("classification_assignments", None)
        group.version_selection = selection
        group.save(using=db, update_fields=["version_selection"])
        LearningObject.objects.using(db).filter(group_id=group.pk).exclude(represented_by=None).update(
            represented_by=None,
        )
        affected_topics.add(group.outline_node_id)

    if affected_topics:
        OutlineNode.objects.using(db).filter(pk__in=affected_topics).update(
            published=False, published_at=None,
        )


class Migration(migrations.Migration):
    dependencies = [("course", "0009_enforce_active_version_roles")]

    operations = [
        migrations.RunPython(rebase_existing_concepts, migrations.RunPython.noop),
    ]
