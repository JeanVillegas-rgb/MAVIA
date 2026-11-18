from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lessons", "0015_learningobject_represented_by")]
    operations = [
        migrations.AddField(
            model_name="learningobjectgroup",
            name="version_selection",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
