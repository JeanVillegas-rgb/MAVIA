from django.db import models

from lessons.models import LearningMaterial, LearningObject


class GeneratedQuestion(models.Model):
    # Derived from the classifier's Bloom level. Records whether a question
    # demands lower- or higher-order thinking, and is what the generation
    # pipeline targets/distributes on. A separate easy/medium/hard
    # `difficulty` field derived straight from bloom_level used to sit
    # alongside this one; it was removed because nothing read it (the
    # adaptive engine's sequencing never used it) and difficulty is not
    # soundly derivable from Bloom's level in the first place.
    THINKING_ORDER_CHOICES = [
        ("LOT", "Lower Order Thinking"),
        ("HOT", "Higher Order Thinking"),
    ]

    # Questions are written straight to the DB as drafts the moment the LLM
    # returns them, then classified, deduplicated and trimmed by a separate
    # deterministic pass. Only "final" rows are ever served to learners or
    # shown to teachers — every read path must filter on this.
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

    # The adaptive quiz walks a chunk through exactly these three tiers, in
    # order — one correct answer per tier advances to the next. "apply" and
    # "analyze" are treated as interchangeable for the third tier since a
    # chunk's generated pool won't reliably contain both.
    TIER_BUCKETS = [
        ("remember",),
        ("understand",),
        ("apply", "analyze"),
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

    # Classification (from Bloom's classifier — the authoritative labels).
    # Blank on a draft row; populated by the post-generation finalize pass.
    bloom_level = models.CharField(
        max_length=20, choices=BLOOM_CHOICES, blank=True, default="", db_index=True)
    thinking_order = models.CharField(
        max_length=3, choices=THINKING_ORDER_CHOICES, blank=True, default="", db_index=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, blank=True, default="")

    status = models.CharField(
        max_length=5, choices=STATUS_CHOICES, default="draft", db_index=True)
    # Hash of the source content and complete generation configuration. A full
    # "Generate all" run may reuse a complete bank only when every row carries
    # the current fingerprint. Explicit single-concept generation still
    # regenerates on demand.
    generation_fingerprint = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
    )
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
    """One teacher-triggered execution of question generation for a material.

    When `node` is set, the run covered only that learning object; otherwise
    it covered every narrated learning object of the material."""
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
    # A publish run covers a whole topic rather than one material, so it sets
    # outline_node instead. One run model serves both so the teacher-facing
    # trace endpoint and its polling client stay shared.
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

    # Which pipeline this run belongs to. Not decoration: question generation
    # refuses to start while another run is live, and that check had nothing to
    # scope itself by. Without a kind, an extraction or publish run would make
    # question generation report a conflict against unrelated work.
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
