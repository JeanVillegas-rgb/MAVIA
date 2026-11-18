from django.db import migrations, models


def classify_existing_question_links(apps, schema_editor):
    QuestionLearningObjectLink = apps.get_model("lessons", "QuestionLearningObjectLink")
    QuestionLearningObjectLink.objects.filter(relevance_score__gte=0.55).update(
        review_status="auto_confirmed",
        is_primary=True,
    )
    QuestionLearningObjectLink.objects.filter(relevance_score__gte=0.25, relevance_score__lt=0.55).update(
        review_status="pending_review",
        is_primary=False,
    )
    QuestionLearningObjectLink.objects.filter(relevance_score__lt=0.25).update(
        review_status="unmatched",
        is_primary=False,
    )


def restore_existing_question_links(apps, schema_editor):
    QuestionLearningObjectLink = apps.get_model("lessons", "QuestionLearningObjectLink")
    QuestionLearningObjectLink.objects.update(is_primary=False)
    question_ids = QuestionLearningObjectLink.objects.values_list("question_id", flat=True).distinct()
    for question_id in question_ids:
        link = QuestionLearningObjectLink.objects.filter(question_id=question_id).order_by(
            "-relevance_score",
            "id",
        ).first()
        if link:
            QuestionLearningObjectLink.objects.filter(pk=link.pk).update(is_primary=True)


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0007_learningobjectmatchsuggestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionlearningobjectlink",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("auto_confirmed", "Automatically confirmed"),
                    ("pending_review", "Pending teacher review"),
                    ("unmatched", "No confident match"),
                    ("teacher_confirmed", "Teacher confirmed"),
                    ("teacher_unpaired", "Teacher left unpaired"),
                ],
                default="pending_review",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="questionlearningobjectlink",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(classify_existing_question_links, restore_existing_question_links),
    ]
