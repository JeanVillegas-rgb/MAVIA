from django.db import models
from lessons.models import LearningMaterial, OutlineNode


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


class ModuleQuestion(models.Model):
    BLOOM_LEVELS = [("REMEMBER", "Remember"), ("UNDERSTAND", "Understand"), ("ANALYZE", "Analyze")]
    lesson_node = models.ForeignKey(LessonNode, on_delete=models.CASCADE, related_name="module_questions")
    question = models.ForeignKey("questions.Question", on_delete=models.CASCADE)
    bloom_level = models.CharField(max_length=20, choices=BLOOM_LEVELS)
    order = models.PositiveIntegerField(default=1)
