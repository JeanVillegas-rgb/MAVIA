from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0024_learningmaterial_draft_learning_objects_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="learningobject",
            name="kept_apart_from_section",
        ),
    ]
