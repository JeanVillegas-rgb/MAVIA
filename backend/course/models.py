from django.core.exceptions import ValidationError
from django.db import models

from lessons.models import LearningMaterial, LearningObject, OutlineNode
from question_generation.models import GeneratedQuestion


class CourseModule(models.Model):
    source = models.OneToOneField(
        OutlineNode,
        on_delete=models.CASCADE,
        related_name="course_package_module",
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
    """Wraps a LearningMaterial (a full lesson). Teaching + questioning both
    step through this lesson's LearningObjects (chunks) in order."""

    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lesson_nodes",
    )
    source = models.OneToOneField(
        LearningMaterial,
        on_delete=models.CASCADE,
        related_name="course_package_lesson_node",
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
    VARIANTS = [("ELABORATED", "Elaborated"), ("SIMPLIFIED", "Simplified")]

    learning_object = models.ForeignKey(
        LearningObject,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    variant = models.CharField(max_length=20, choices=VARIANTS)
    narration = models.TextField()
    audio_url = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("learning_object", "variant")
        ordering = ["learning_object_id", "variant"]

    @property
    def lesson_node(self):
        return self.learning_object.material.course_package_lesson_node

    def clean(self):
        if not hasattr(self.learning_object.material, "course_package_lesson_node"):
            raise ValidationError({
                "learning_object": "This Learning material has no "
                                    "content yet — create that first."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.variant}] {self.learning_object.title}"


def normal_variant_for(learning_object):
    generated_json = learning_object.material.generated_json or {}
    if not generated_json.get("lesson_audio_generated"):
        return None

    for entry in generated_json.get("lesson_playlist", []):
        if entry.get("learning_object_id") == learning_object.id:
            return {
                "variant": "NORMAL",
                "narration": entry.get("narration") or entry.get("text", ""),
                "audio_url": entry.get("audio_url") or entry.get("audio", ""),
            }
    return None


class ModuleQuestion(models.Model):
    lesson_node = models.ForeignKey(LessonNode, on_delete=models.CASCADE, related_name="module_questions")
    question = models.ForeignKey(GeneratedQuestion, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("lesson_node", "question")
        ordering = ["lesson_node_id", "order", "id"]

    @property
    def bloom_level(self):
        return self.question.bloom_level

    @property
    def difficulty(self):
        return self.question.difficulty

    def clean(self):
        if not self.lesson_node.source.learning_objects.filter(
            pk=self.question.node_id
        ).exists():
            raise ValidationError({
                "question": "Question's learning object must belong to this "
                             "lesson node's Learning Material."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lesson_node.title} · Q{self.order} ({self.question.bloom_level})"