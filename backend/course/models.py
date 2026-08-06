from django.core.exceptions import ValidationError
from django.db import models

from lessons.models import LearningMaterial, OutlineNode
from question_generation.models import GeneratedQuestion


class CourseModule(models.Model):
    source = models.OneToOneField(
        OutlineNode,
        on_delete=models.CASCADE,
        related_name="adaptive_module",
        limit_choices_to={"parent__isnull": True},
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["source__order"]

    @property
    def sequence_order(self):
        return self.source.order if self.source_id else 0

    @property
    def title(self):
        return self.source.title if self.source_id else "(unlinked)"

    def __str__(self):
        return f"Module {self.sequence_order}: {self.title}"


class LessonNode(models.Model):
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lesson_nodes",
    )
    source = models.OneToOneField(
        LearningMaterial,
        on_delete=models.CASCADE,
        related_name="adaptive_lesson_node",
    )

    class Meta:
        ordering = ["source__created_at"]

    @property
    def title(self):
        return self.source.title

    @property
    def learning_objects(self):
        return self.source.learning_objects.all()

    def __str__(self):
        return self.title


class LessonVariant(models.Model):
    VARIANTS = [("NORMAL", "Normal"), ("ELABORATED", "Elaborated"), ("SIMPLIFIED", "Simplified")]

    lesson_node = models.ForeignKey(LessonNode, on_delete=models.CASCADE, related_name="variants")
    variant = models.CharField(max_length=20, choices=VARIANTS)
    narration = models.TextField()
    audio_url = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("lesson_node", "variant")
        ordering = ["lesson_node_id", "variant"]


class ModuleQuestion(models.Model):
    BLOOM_LEVELS = [("REMEMBER", "Remember"), ("UNDERSTAND", "Understand"), ("ANALYZE", "Analyze")]

    lesson_node = models.ForeignKey(LessonNode, on_delete=models.CASCADE, related_name="module_questions")
    question = models.ForeignKey(GeneratedQuestion, on_delete=models.CASCADE)
    bloom_level = models.CharField(max_length=20, choices=BLOOM_LEVELS)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("lesson_node", "question")
        ordering = ["lesson_node_id", "bloom_level", "order", "id"]

    def clean(self):
        if not self.lesson_node.source.learning_objects.filter(
            pk=self.question.node_id
        ).exists():
            raise ValidationError({
                "question": "Question's learning object must belong to this "
                             "lesson node's LearningMaterial."
            })

    def save(self, *args, **kwargs):
        # clean() previously wasn't reachable: sync_module_questions() uses
        # update_or_create(), which never calls full_clean(). Enforce the
        # invariant here so it's actually checked on every save, including
        # from admin, shell, or future call sites that skip services.py.
        self.full_clean()
        super().save(*args, **kwargs)