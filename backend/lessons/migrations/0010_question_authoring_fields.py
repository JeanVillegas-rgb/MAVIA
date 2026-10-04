from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lessons", "0009_alter_courseoutline_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("open_ended", "Open ended"),
                    ("true_false", "True/False"),
                    ("multiple_choice", "Multiple choice"),
                ],
                default="open_ended",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="choices",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="question",
            name="correct_answer",
            field=models.TextField(blank=True),
        ),
    ]
