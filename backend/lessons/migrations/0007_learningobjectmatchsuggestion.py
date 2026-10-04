import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0006_learningobject_source_block_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="LearningObjectMatchSuggestion",
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
                ("similarity_score", models.FloatField(default=0.0)),
                (
                    "confidence",
                    models.CharField(
                        choices=[
                            ("high", "High"),
                            ("medium", "Medium"),
                            ("teacher_confirmed", "Teacher confirmed"),
                        ],
                        max_length=30,
                    ),
                ),
                ("evidence", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "candidate_learning_object",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_match_suggestions",
                        to="lessons.learningobject",
                    ),
                ),
                (
                    "outline_node",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learning_object_match_suggestions",
                        to="lessons.outlinenode",
                    ),
                ),
                (
                    "source_learning_object",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing_match_suggestions",
                        to="lessons.learningobject",
                    ),
                ),
            ],
            options={
                "ordering": ["-similarity_score", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="learningobjectmatchsuggestion",
            constraint=models.UniqueConstraint(
                fields=("source_learning_object", "candidate_learning_object"),
                name="unique_learning_object_match_suggestion",
            ),
        ),
        migrations.AddConstraint(
            model_name="learningobjectmatchsuggestion",
            constraint=models.CheckConstraint(
                condition=~models.Q(
                    source_learning_object=models.F("candidate_learning_object")
                ),
                name="match_suggestion_objects_must_differ",
            ),
        ),
    ]
