from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("lessons", "0013_courseoutline_file_sha256"),
        ("question_generation", "0003_lot_hot_thinking_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="source_type",
            field=models.CharField(
                choices=[("pdf", "Uploaded PDF"), ("manual", "Manual"), ("generated", "Generated")],
                db_index=True,
                default="pdf",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="content_fingerprint",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(model_name="question", name="bloom_level", field=models.CharField(blank=True, db_index=True, max_length=20)),
        migrations.AddField(model_name="question", name="thinking_order", field=models.CharField(blank=True, db_index=True, max_length=3)),
        migrations.AddField(model_name="question", name="difficulty", field=models.CharField(blank=True, db_index=True, max_length=10)),
        migrations.AddField(model_name="question", name="category", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(
            model_name="question",
            name="validation_status",
            field=models.CharField(
                choices=[("ready", "Ready"), ("needs_review", "Needs review")],
                db_index=True,
                default="needs_review",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="validation_issues",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="question",
            name="adaptive_question",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teacher_question",
                to="question_generation.generatedquestion",
            ),
        ),
    ]
