from django.conf import settings
from django.db import models

from lessons.models import CourseGroup, OutlineNode, Question


class Enrollment(models.Model):
    """A student's membership in a course. Teachers manage the roster from the
    web app; the per-student progress report is scoped to these rows."""

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"role": "STUDENT"},
    )
    course = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        unique_together = ("student", "course")
        ordering = ["-created_at", "id"]

    def __str__(self):
        return f"{self.student} in {self.course}"


class LearningState(models.Model):
    """One row per (student, course): where the learner currently is and how
    well they're doing. The web review player never writes this — only the
    student-facing adaptive endpoints do."""

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="learning_states",
    )
    course = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name="learning_states",
    )
    # Top-level outline node (module) and the child topic being worked on.
    current_module = models.ForeignKey(
        OutlineNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    current_lesson_node = models.ForeignKey(
        OutlineNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    current_question = models.ForeignKey(
        Question,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Attempts spent on current_question, so the engine can move a learner on
    # after repeated wrong answers rather than stranding them.
    current_question_attempts = models.PositiveIntegerField(default=0)
    mastery = models.FloatField(default=0.30)
    attempts = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "course")
        ordering = ["-updated_at", "id"]

    def __str__(self):
        return f"{self.student} · {self.course} · mastery {self.mastery:.2f}"


class StudentResponse(models.Model):
    learning_state = models.ForeignKey(
        LearningState,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="+")
    selected_answer = models.CharField(max_length=255)
    is_correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Q{self.question_id} {'✓' if self.is_correct else '✗'}"
