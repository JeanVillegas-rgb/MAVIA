from django.db import migrations, models


def preserve_extra_wording(apps, schema_editor):
    """Remove the old role while retaining every source and saved wording."""
    Group = apps.get_model("lessons", "LearningObjectGroup")
    Material = apps.get_model("lessons", "LearningMaterial")
    LearningObject = apps.get_model("lessons", "LearningObject")
    OutlineNode = apps.get_model("lessons", "OutlineNode")
    Variant = apps.get_model("course", "LessonVariant")
    db = schema_editor.connection.alias

    for group in Group.objects.using(db).all().iterator():
        selection = dict(group.version_selection or {})
        roles = dict(selection.get("bundle_roles") or {})
        removed = [key for key, role in roles.items() if role == "EXTRA"]
        if not removed:
            continue
        provenance = dict(selection.get("bundle_roles_assigned_by") or {})
        decided_at = dict(selection.get("bundle_roles_decided_at") or {})
        for key in removed:
            roles.pop(key, None)
            provenance[key] = "displaced_by_teacher"
            decided_at.pop(key, None)
            try:
                material_id = int(key)
            except (TypeError, ValueError):
                continue
            LearningObject.objects.using(db).filter(
                group_id=group.pk, material_id=material_id
            ).update(represented_by=None)
        selection["bundle_roles"] = roles
        selection["bundle_roles_assigned_by"] = provenance
        selection["bundle_roles_decided_at"] = decided_at
        selection.pop("roles_signature", None)
        selection.pop("classification_assignments", None)
        group.version_selection = selection
        group.save(update_fields=["version_selection"])
        OutlineNode.objects.using(db).filter(pk=group.outline_node_id).update(
            published=False, published_at=None
        )

    for row in Variant.objects.using(db).filter(variant="EXTRA").select_related(
        "learning_object"
    ).iterator():
        material = Material.objects.using(db).get(pk=row.learning_object.material_id)
        generated = dict(material.generated_json or {})
        archived = list(generated.get("legacy_unassigned_versions") or [])
        archived.append({
            "old_variant_id": row.pk,
            "learning_object_id": row.learning_object_id,
            "source_learning_object_id": row.source_learning_object_id,
            "narration": row.narration,
            "audio_url": row.audio_url,
            "origin": row.origin,
            "assigned_by": row.assigned_by,
        })
        generated["legacy_unassigned_versions"] = archived
        material.generated_json = generated
        material.save(update_fields=["generated_json"])
        row.delete()


class Migration(migrations.Migration):
    dependencies = [("course", "0007_repair_cross_group_version_links")]

    operations = [
        migrations.RunPython(preserve_extra_wording, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lessonvariant",
            name="variant",
            field=models.CharField(
                choices=[("ELABORATED", "Elaborated"), ("SIMPLIFIED", "Simplified")],
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="lessonvariant", name="unique_primary_variant_slot"
        ),
        migrations.AddConstraint(
            model_name="lessonvariant",
            constraint=models.UniqueConstraint(
                fields=("learning_object", "variant"),
                name="unique_primary_variant_slot",
            ),
        ),
    ]
