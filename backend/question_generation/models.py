from django.db import models

from lessons.models import LearningMaterial, LearningObject


class GeneratedQuestion(models.Model):
    DIFFICULTY_CHOICES = [
        ("easy", "Easy"),
        ("medium", "Medium"),
        ("hard", "Hard"),
    ]

    BLOOM_CHOICES = [
        ("remember", "Remember"),
        ("understand", "Understand"),
        ("apply", "Apply"),
        ("analyze", "Analyze"),
        ("evaluate", "Evaluate"),
        ("create", "Create"),
    ]

    FORMAT_CHOICES = [
        ("MCQ", "Multiple Choice"),
        ("TF", "True/False"),
    ]

    CATEGORY_CHOICES = [
        ("Facts and Information", "Facts and Information"),
        ("Meaning", "Meaning"),
        ("Skills", "Skills"),
        ("Outcome", "Outcome"),
    ]

    # the content node this question assesses
    node = models.ForeignKey(
        LearningObject,
        related_name="generated_questions",
        on_delete=models.CASCADE,
    )

    # question content
    question_text = models.TextField()
    question_format = models.CharField(max_length=3, choices=FORMAT_CHOICES)
    choices = models.JSONField(null=True, blank=True)
    correct_answer = models.CharField(max_length=255)
    explanation = models.TextField(blank=True, default="")

    # classification (from Bloom's classifier — the authoritative labels)
    bloom_level = models.CharField(max_length=20, choices=BLOOM_CHOICES, db_index=True)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, db_index=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)

    # metadata
    intended_difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES)
    difficulty_match = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["node", "difficulty"]),
        ]

    def __str__(self):
        return f"[{self.difficulty}] {self.question_text[:50]}"


class LearnerResponse(models.Model):
    learner_id = models.CharField(max_length=50, db_index=True)
    question = models.ForeignKey(GeneratedQuestion, on_delete=models.CASCADE)
    selected_answer = models.CharField(max_length=255)
    is_correct = models.BooleanField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["learner_id", "question"]),
        ]


class GenerationRun(models.Model):
    """One teacher-triggered execution of question generation for a material."""
    STATUS_CHOICES = [
        ("running", "Running"),
        ("finished", "Finished"),
        ("failed", "Failed"),
    ]

    material = models.ForeignKey(
        LearningMaterial,
        related_name="question_generation_runs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="running")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Run {self.id} [{self.status}] {self.material.title}"


class GenerationEvent(models.Model):
    """A single trace event emitted by the pipeline during a run."""
    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    event_type = models.CharField(max_length=30)
    message = models.TextField(blank=True, default="")
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        indexes = [models.Index(fields=["run", "seq"])]
