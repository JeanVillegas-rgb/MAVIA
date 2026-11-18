import hashlib

from django.db import migrations, models


def _fingerprint(title, content):
    # Frozen copy of lessons.models.grouping_fingerprint: a migration must not
    # change meaning if the model helper is edited later.
    text = " ".join(f"{title or ''}\n{content or ''}".split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def baseline_existing_objects(apps, schema_editor):
    """Treat every object's current text as already grouped.

    Groups formed before this field existed were decided against whatever the
    objects say now, as far as anyone can tell. Leaving the field blank would
    light up "Review grouping changes" for every grouped object in the database
    on the first page load, which is noise rather than a finding.
    """
    LearningObject = apps.get_model("lessons", "LearningObject")
    batch = []
    for item in LearningObject.objects.only("id", "title", "content").iterator():
        item.grouping_content_hash = _fingerprint(item.title, item.content)
        batch.append(item)
        if len(batch) >= 500:
            LearningObject.objects.bulk_update(batch, ["grouping_content_hash"])
            batch = []
    if batch:
        LearningObject.objects.bulk_update(batch, ["grouping_content_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0016_learningobjectgroup_version_selection"),
    ]

    operations = [
        migrations.AddField(
            model_name="learningobject",
            name="grouping_content_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(baseline_existing_objects, migrations.RunPython.noop),
    ]
