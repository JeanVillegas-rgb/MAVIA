from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("question_generation", "0005_generationrun_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="generatedquestion",
            name="generation_fingerprint",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
