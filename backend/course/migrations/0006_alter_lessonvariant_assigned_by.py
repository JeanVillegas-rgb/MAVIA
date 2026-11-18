from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("course", "0005_alter_lessonvariant_unique_together_and_more")]

    operations = [
        migrations.AlterField(
            model_name="lessonvariant",
            name="assigned_by",
            field=models.CharField(
                choices=[
                    ("heuristic", "Proposed by the readability heuristic"),
                    (
                        "llm_validated",
                        "Proposed by the LLM and validated by readability rules",
                    ),
                    ("teacher", "Confirmed or corrected by a teacher"),
                ],
                default="heuristic",
                max_length=20,
            ),
        ),
    ]
