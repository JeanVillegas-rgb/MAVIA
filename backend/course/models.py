from django.db import models

from lessons.models import OutlineNode


class CourseModule(models.Model):
    source = models.OneToOneField(
        OutlineNode,
        on_delete=models.CASCADE,
        related_name="adaptive_module",
        limit_choices_to={"parent__isnull": True},
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["source__order", "source__id"]

    @property
    def sequence_order(self):
        return self.source.order

    @property
    def title(self):
        return self.source.title

    def __str__(self):
        return f"Module {self.sequence_order}: {self.title}"


class LessonNode(models.Model):
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lesson_nodes",
    )
    source = models.OneToOneField(
        OutlineNode,
        on_delete=models.CASCADE,
        related_name="adaptive_lesson_node",
    )

    class Meta:
        ordering = ["source__depth", "source__order", "source__id"]

    @property
    def node_order(self):
        return self.source.order

    @property
    def title(self):
        return self.source.title

    def __str__(self):
        return self.title


class LessonVariant(models.Model):
    class Variant(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        ELABORATED = "ELABORATED", "Elaborated"
        SIMPLIFIED = "SIMPLIFIED", "Simplified"

    lesson_node = models.ForeignKey(
        LessonNode,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    variant = models.CharField(max_length=20, choices=Variant.choices)
    narration = models.TextField()
    audio_url = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("lesson_node", "variant")
        ordering = ["lesson_node_id", "variant"]


class ModuleQuestion(models.Model):
    class BloomLevel(models.TextChoices):
        REMEMBER = "REMEMBER", "Remember"
        UNDERSTAND = "UNDERSTAND", "Understand"
        ANALYZE = "ANALYZE", "Analyze"

    lesson_node = models.ForeignKey(
        LessonNode,
        on_delete=models.CASCADE,
        related_name="module_questions",
    )
    question = models.ForeignKey(
        "question_generation.GeneratedQuestion",
        on_delete=models.CASCADE,
    )
    bloom_level = models.CharField(max_length=20, choices=BloomLevel.choices)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("lesson_node", "question")
        ordering = ["lesson_node_id", "bloom_level", "order", "id"]
