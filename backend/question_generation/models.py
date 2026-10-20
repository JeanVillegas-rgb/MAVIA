from django.db import models

from lessons.models import LearningMaterial, LearningObject


class GeneratedQuestion(models.Model):
    """A generated question; only status="final" rows are served or shown."""
    THINKING_ORDER_CHOICES = [
        ("LOT", "Lower Order Thinking"),
        ("HOT", "Higher Order Thinking"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("final", "Final"),
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

    node = models.ForeignKey(
        LearningObject,
        related_name="generated_questions",
        on_delete=models.CASCADE,
    )

    question_text = models.TextField()
    question_format = models.CharField(max_length=3, choices=FORMAT_CHOICES)
    choices = models.JSONField(null=True, blank=True)
    correct_answer = models.CharField(max_length=255)
    explanation = models.TextField(blank=True, default="")

    bloom_level = models.CharField(
        max_length=20, choices=BLOOM_CHOICES, blank=True, default="", db_index=True)
    thinking_order = models.CharField(
        max_length=3, choices=THINKING_ORDER_CHOICES, blank=True, default="", db_index=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, blank=True, default="")

    status = models.CharField(
        max_length=5, choices=STATUS_CHOICES, default="draft", db_index=True)
    generation_fingerprint = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
    )
    source_text_fingerprint = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["node", "thinking_order"]),
            models.Index(fields=["node", "status"]),
        ]

    def __str__(self):
        return f"[{self.thinking_order or 'unclassified'}] {self.question_text[:50]}"


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
    """One run of a pipeline (questions, extraction, publish or versions) and its trace."""
    STATUS_CHOICES = [
        ("running", "Running"),
        ("finished", "Finished"),
        ("failed", "Failed"),
    ]

    material = models.ForeignKey(
        LearningMaterial,
        null=True,
        blank=True,
        related_name="question_generation_runs",
        on_delete=models.CASCADE,
    )
    node = models.ForeignKey(
        LearningObject,
        null=True,
        blank=True,
        related_name="question_generation_runs",
        on_delete=models.SET_NULL,
    )
    outline_node = models.ForeignKey(
        "lessons.OutlineNode",
        null=True,
        blank=True,
        related_name="publish_runs",
        on_delete=models.CASCADE,
    )
    class Kind(models.TextChoices):
        EXTRACTION = "extraction", "PDF extraction"
        QUESTIONS = "questions", "Question generation"
        PUBLISH = "publish", "Topic publish"
        VERSIONS = "versions", "Content version classification"

    kind = models.CharField(
        max_length=12,
        choices=Kind.choices,
        default=Kind.QUESTIONS,
        db_index=True,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="running")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        if self.outline_node_id:
            return f"Publish {self.id} [{self.status}] {self.outline_node.title}"
        scope = self.node.title if self.node else "all nodes"
        title = self.material.title if self.material_id else "(no material)"
        return f"Run {self.id} [{self.status}] {title} ({scope})"


class GenerationEvent(models.Model):
    """One trace event of a run."""
    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    event_type = models.CharField(max_length=64)
    message = models.TextField(blank=True, default="")
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        indexes = [models.Index(fields=["run", "seq"])]
