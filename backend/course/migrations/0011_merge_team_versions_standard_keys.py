from django.db import migrations

# Joins the two lines of course migrations that grew apart on 2026-10-04:
#   this branch:  0008_remove_mobile_package_models -> 0009_rename_normal_to_standard
#   the team:     0008_remove_extra_version_role -> 0009_enforce_active_version_roles
#                 -> 0010_first_relevant_pdf_is_normal
# The team's 0010 writes `normal_material_id` / `normal_assigned_by`, but this
# branch renamed the Normal version to Standard. Depending on which line a
# database applied first, a concept's version_selection can hold either name,
# so this renames any `normal_*` key to `standard_*`, whichever order ran.
# Safe to run more than once; the team's migrations are left untouched so
# their databases' histories still line up.

OLD_PREFIX, NEW_PREFIX = "normal_", "standard_"


def forwards(apps, schema_editor):
    LearningObjectGroup = apps.get_model("lessons", "LearningObjectGroup")
    for group in LearningObjectGroup.objects.exclude(version_selection=None):
        selection = group.version_selection or {}
        old_keys = [key for key in selection if key.startswith(OLD_PREFIX)]
        if not old_keys:
            continue
        for key in old_keys:
            # The team's 0010 ran last if a normal_ key is still here, so its
            # value is the current one.
            selection[NEW_PREFIX + key[len(OLD_PREFIX):]] = selection.pop(key)
        group.version_selection = selection
        group.save(update_fields=["version_selection"])


class Migration(migrations.Migration):

    dependencies = [
        ("course", "0009_rename_normal_to_standard"),
        ("course", "0010_first_relevant_pdf_is_normal"),
        ("lessons", "0022_merge_20261004_1813"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
