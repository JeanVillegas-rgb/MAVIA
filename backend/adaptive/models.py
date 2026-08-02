from django.db import models

from course.models import CourseModule, LessonNode


class LearningState(models.Model):
    class Variant(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        ELABORATED = "ELABORATED", "Elaborated"
        SIMPLIFIED = "SIMPLIFIED", "Simplified"

    class BloomLevel(models.TextChoices):
        REMEMBER = "REMEMBER", "Remember"
        UNDERSTAND = "UNDERSTAND", "Understand"
        ANALYZE = "ANALYZE", "Analyze"

    learner_id = models.CharField(max_length=80, default="default", db_index=True)
    current_module = models.ForeignKey(CourseModule, on_delete=models.CASCADE)
    current_node = models.ForeignKey(LessonNode, on_delete=models.CASCADE)
    mastery = models.FloatField(default=0.30)
    attempts = models.PositiveIntegerField(default=0)
    tier_attempts = models.PositiveIntegerField(default=0)
    current_variant = models.CharField(
        max_length=20,
        choices=Variant.choices,
        default=Variant.NORMAL,
    )
    current_bloom = models.CharField(
        max_length=20,
        choices=BloomLevel.choices,
        default=BloomLevel.REMEMBER,
    )
    reward = models.FloatField(default=0.0)
    completed = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.learner_id}: {self.current_module.title} | {self.current_node.title}"


class StudentResponse(models.Model):
    learning_state = models.ForeignKey(
        LearningState,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    question = models.ForeignKey(
        "question_generation.GeneratedQuestion",
        on_delete=models.CASCADE,
    )
    selected_answer = models.CharField(max_length=255)
    is_correct = models.BooleanField()
    response_time = models.FloatField(default=0)
    reward = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        label = "Correct" if self.is_correct else "Wrong"
        return f"Question {self.question_id} | {label}"
