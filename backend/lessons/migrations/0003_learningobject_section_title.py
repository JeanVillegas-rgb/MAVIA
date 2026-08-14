from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0002_learningobject_image_url_alter_learningobject_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="learningobject",
            name="section_title",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
