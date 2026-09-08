import uuid

from django.db import migrations, models


MODEL_NAMES = ("CourseOutline", "LearningMaterial", "LearningObject")


def populate_metadata_ids(apps, schema_editor):
    # Generate one UUID per existing row, not one shared migration default.
    for name in MODEL_NAMES:
        model = apps.get_model("lessons", name)
        rows = model.objects.using(schema_editor.connection.alias)
        for pk in rows.values_list("pk", flat=True).iterator():
            rows.filter(pk=pk).update(metadata_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("lessons", "0011_outlinenode_published_outlinenode_published_at")]

    operations = [
        migrations.AddField(
            model_name=name.lower(),
            name="metadata_id",
            field=models.UUIDField(null=True, editable=False),
        )
        for name in MODEL_NAMES
    ] + [
        migrations.RunPython(populate_metadata_ids, migrations.RunPython.noop),
    ] + [
        migrations.AlterField(
            model_name=name.lower(),
            name="metadata_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        )
        for name in MODEL_NAMES
    ]
