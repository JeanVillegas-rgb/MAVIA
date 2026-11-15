from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0009_alter_courseoutline_options_and_more"),
        ("question_generation", "0009_generatedquestion_source_text_fingerprint"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="GeneratedQuestion",
            new_name="Question",
        ),
        migrations.CreateModel(
            name="QuestionFile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("file", models.FileField(upload_to="questionnaires/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "outline_node",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="question_files",
                        to="lessons.outlinenode",
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="question",
            name="node",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="generated_questions",
                to="lessons.learningobject",
            ),
        ),
        migrations.AlterField(
            model_name="question",
            name="question_format",
            field=models.CharField(
                blank=True,
                choices=[("MCQ", "Multiple Choice"), ("TF", "True/False")],
                default="",
                max_length=3,
            ),
        ),
        migrations.AlterField(
            model_name="question",
            name="correct_answer",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="question",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("review", "Review"),
                    ("final", "Final"),
                ],
                db_index=True,
                default="draft",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="check_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="question",
            name="check_result",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not checked"),
                    ("supported", "Supported"),
                    ("contradicted", "Contradicted"),
                    ("unsupported", "Unsupported"),
                    ("unverified", "Unverified"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="extracted_question",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="question_bank_rows",
                to="lessons.question",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="outline_node",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="question_bank_questions",
                to="lessons.outlinenode",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="paired_by",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not paired"),
                    ("auto", "Automatically paired"),
                    ("teacher", "Teacher paired"),
                ],
                default="",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="question_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="questions",
                to="question_generation.questionfile",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="source",
            field=models.CharField(
                choices=[
                    ("generated", "Generated"),
                    ("manual", "Manual"),
                    ("questionnaire", "Questionnaire"),
                    ("lesson_pdf", "Lesson PDF"),
                ],
                db_index=True,
                default="generated",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="teacher_edited",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="question",
            name="validation_issues",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(
                fields=["outline_node", "status"],
                name="question_qu_outline_4a7b3a_idx",
            ),
        ),
    ]
