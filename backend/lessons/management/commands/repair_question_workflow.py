from django.core.management.base import BaseCommand
from django.db import transaction

from lessons.models import Question
from lessons.services.question_workflow import enriched_question_values


class Command(BaseCommand):
    help = "Backfill question metadata and remove exact normalized duplicates per topic."

    @transaction.atomic
    def handle(self, *args, **options):
        duplicate_count = 0
        seen = {}
        for question in Question.objects.select_related("material").prefetch_related(
            "learning_object_links"
        ).order_by("id"):
            if (question.material.generated_json or {}).get("document_source") == "manual_questions":
                question.source_type = Question.SourceType.MANUAL
            values = enriched_question_values(
                question.prompt,
                question.question_type,
                question.choices,
                question.correct_answer,
            )
            for field, value in values.items():
                setattr(question, field, value)
            question.save(update_fields=[*values.keys(), "source_type"])

            scope = (
                question.material.course_id,
                question.material.outline_node_id,
                question.content_fingerprint,
            )
            keeper = seen.get(scope)
            if keeper is None:
                seen[scope] = question
                continue

            keeper_has_confirmed = keeper.learning_object_links.filter(
                review_status__in=("auto_confirmed", "teacher_confirmed")
            ).exists()
            duplicate_has_confirmed = question.learning_object_links.filter(
                review_status__in=("auto_confirmed", "teacher_confirmed")
            ).exists()
            if duplicate_has_confirmed and not keeper_has_confirmed:
                keeper.learning_object_links.all().delete()
                for link in question.learning_object_links.all():
                    link.pk = None
                    link.question = keeper
                    link.save()
            question.delete()
            duplicate_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Question workflow repaired; removed {duplicate_count} duplicate(s)."
        ))
