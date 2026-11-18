from django.db import migrations

# The version once called "Normal" is now "Standard". These are the places
# that name stored as data rather than code.
OLD_KEY, NEW_KEY = "normal_material_id", "standard_material_id"
OLD_GENERATOR, NEW_GENERATOR = "normal-text-fallback", "standard-text-fallback"


def _rename(apps, *, key_from, key_to, generator_from, generator_to, variant_from, variant_to):
    LearningObjectGroup = apps.get_model("lessons", "LearningObjectGroup")
    for group in LearningObjectGroup.objects.exclude(version_selection=None):
        selection = group.version_selection or {}
        if key_from in selection:
            selection[key_to] = selection.pop(key_from)
            group.version_selection = selection
            group.save(update_fields=["version_selection"])

    LessonVariant = apps.get_model("course", "LessonVariant")
    LessonVariant.objects.filter(generator_model=generator_from).update(generator_model=generator_to)

    LearningState = apps.get_model("adaptive", "LearningState")
    LearningState.objects.filter(current_variant=variant_from).update(current_variant=variant_to)

    StudentResponse = apps.get_model("adaptive", "StudentResponse")
    StudentResponse.objects.filter(variant=variant_from).update(variant=variant_to)
    StudentResponse.objects.filter(next_variant=variant_from).update(next_variant=variant_to)


def forwards(apps, schema_editor):
    _rename(apps, key_from=OLD_KEY, key_to=NEW_KEY,
            generator_from=OLD_GENERATOR, generator_to=NEW_GENERATOR,
            variant_from="normal", variant_to="standard")


def backwards(apps, schema_editor):
    _rename(apps, key_from=NEW_KEY, key_to=OLD_KEY,
            generator_from=NEW_GENERATOR, generator_to=OLD_GENERATOR,
            variant_from="standard", variant_to="normal")


class Migration(migrations.Migration):

    dependencies = [
        ("course", "0008_remove_mobile_package_models"),
        ("lessons", "0019_drop_merged_from"),
        ("adaptive", "0008_standard_variant"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
