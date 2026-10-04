from django.db import migrations


def repair_cross_group_version_links(apps, schema_editor):
    """Undo version links left behind when an object changed concept.

    Separate, Connect and accepting a suggestion used to move an object to
    another group without undoing its old group's version bookkeeping. Two
    kinds of leftover follow, and both are removed here:

    * an object still marked "taught through" an original in a *different*
      group -- version generation refuses it, since it looks like a
      non-original;
    * a source version (Simplified, Elaborated or Extra) held by one object but
      written by an object that now belongs to a *different* group -- students
      would be offered another concept's text as this concept's version.

    Only rows whose two ends are in different groups are touched. Generated
    versions carry no source object, so they are never removed. The Normal text
    of every object is its own content and is untouched; a slot emptied here is
    simply reported as missing, and "Generate missing versions" fills it.

    Logic is frozen here rather than imported, so the migration keeps its
    meaning if the application code changes later.
    """
    LearningObject = apps.get_model("lessons", "LearningObject")
    LessonVariant = apps.get_model("course", "LessonVariant")

    stale_objects = [
        item.pk
        for item in LearningObject.objects.exclude(represented_by=None).select_related("represented_by")
        if item.represented_by.group_id != item.group_id or item.group_id is None
    ]
    if stale_objects:
        LearningObject.objects.filter(pk__in=stale_objects).update(represented_by=None)

    stale_rows = [
        row.pk
        for row in LessonVariant.objects.exclude(source_learning_object=None).select_related(
            "learning_object", "source_learning_object",
        )
        if row.source_learning_object.group_id != row.learning_object.group_id
        or row.learning_object.group_id is None
    ]
    if stale_rows:
        LessonVariant.objects.filter(pk__in=stale_rows).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("course", "0006_alter_lessonvariant_assigned_by"),
        ("lessons", "0017_learningobject_grouping_content_hash"),
    ]

    operations = [
        migrations.RunPython(repair_cross_group_version_links, migrations.RunPython.noop),
    ]
