from django.core.exceptions import ValidationError
from django.db import models


class CourseGroup(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class CourseOutline(models.Model):
    course = models.ForeignKey(
        CourseGroup,
        related_name="outlines",
        on_delete=models.CASCADE,
    )
    outline_file = models.FileField(upload_to="outlines/")
    is_approved = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["uploaded_at", "id"]

    def __str__(self):
        return f"Outline for {self.course.title}"


class OutlineNode(models.Model):
    course = models.ForeignKey(
        CourseGroup,
        related_name="nodes",
        on_delete=models.CASCADE,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=255)
    related_info = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)
    depth = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["depth", "order", "id"]
        unique_together = ("course", "parent", "order")

    def __str__(self):
        return self.title


class LearningMaterial(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    course = models.ForeignKey(
        CourseGroup,
        related_name="materials",
        on_delete=models.CASCADE,
    )
    outline_node = models.ForeignKey(
        OutlineNode,
        null=True,
        blank=True,
        related_name="materials",
        on_delete=models.CASCADE,
    )
    module_node = models.ForeignKey(
        OutlineNode,
        null=True,
        blank=True,
        related_name="module_materials",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=255)
    pdf_file = models.FileField(upload_to="learning_materials/")
    file_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    extracted_text = models.TextField(blank=True)
    generated_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "file_sha256"],
                condition=~models.Q(file_sha256=""),
                name="unique_material_file_per_course",
            )
        ]

    def __str__(self):
        return self.title

    def clean(self):
        errors = {}
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the material."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if errors:
            raise ValidationError(errors)


class LearningObjectGroup(models.Model):
    """A neutral set of interchangeable learning objects for one topic.

    The group deliberately carries no difficulty or remediation label. Those
    decisions belong to the learning-path component that consumes the group.
    """

    outline_node = models.ForeignKey(
        OutlineNode,
        related_name="learning_object_groups",
        on_delete=models.CASCADE,
    )
    label = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.label or f"Learning object group {self.pk}"


class LearningObject(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"

    material = models.ForeignKey(
        LearningMaterial,
        related_name="learning_objects",
        on_delete=models.CASCADE,
    )
    group = models.ForeignKey(
        LearningObjectGroup,
        null=True,
        blank=True,
        related_name="learning_objects",
        on_delete=models.SET_NULL,
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TEXT)
    section_title = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255)
    content = models.TextField()
    image_url = models.CharField(max_length=500, blank=True)
    image_prompt = models.TextField(blank=True)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    source_block_id = models.PositiveIntegerField(null=True, blank=True)
    source_excerpt = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class LearningObjectMatchSuggestion(models.Model):
    """An explainable cross-PDF candidate awaiting or recording teacher review."""

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        TEACHER_CONFIRMED = "teacher_confirmed", "Teacher confirmed"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    outline_node = models.ForeignKey(
        OutlineNode,
        related_name="learning_object_match_suggestions",
        on_delete=models.CASCADE,
    )
    source_learning_object = models.ForeignKey(
        LearningObject,
        related_name="outgoing_match_suggestions",
        on_delete=models.CASCADE,
    )
    candidate_learning_object = models.ForeignKey(
        LearningObject,
        related_name="incoming_match_suggestions",
        on_delete=models.CASCADE,
    )
    similarity_score = models.FloatField(default=0.0)
    confidence = models.CharField(max_length=30, choices=Confidence.choices)
    evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-similarity_score", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_learning_object", "candidate_learning_object"],
                name="unique_learning_object_match_suggestion",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    source_learning_object=models.F("candidate_learning_object")
                ),
                name="match_suggestion_objects_must_differ",
            ),
        ]

    def clean(self):
        errors = {}
        source = self.source_learning_object
        candidate = self.candidate_learning_object
        if source.material_id == candidate.material_id:
            errors["candidate_learning_object"] = "Suggested variations must come from different PDFs."
        if source.material.outline_node_id != self.outline_node_id:
            errors["source_learning_object"] = "Source object must belong to the suggestion topic."
        if candidate.material.outline_node_id != self.outline_node_id:
            errors["candidate_learning_object"] = "Candidate object must belong to the suggestion topic."
        if source.kind != candidate.kind:
            errors["candidate_learning_object"] = "Suggested objects must have the same content type."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.source_learning_object_id} ↔ "
            f"{self.candidate_learning_object_id} ({self.status})"
        )


class Question(models.Model):
    """A detected question kept separate from learner-facing lesson content."""

    class Type(models.TextChoices):
        OPEN_ENDED = "open_ended", "Open ended"
        TRUE_FALSE = "true_false", "True/False"
        MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"

    material = models.ForeignKey(
        LearningMaterial,
        related_name="questions",
        on_delete=models.CASCADE,
    )
    prompt = models.TextField()
    question_type = models.CharField(
        max_length=30,
        choices=Type.choices,
        default=Type.OPEN_ENDED,
    )
    choices = models.JSONField(default=list, blank=True)
    correct_answer = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    source_block_id = models.PositiveIntegerField(null=True, blank=True)
    source_excerpt = models.TextField(blank=True)
    learning_objects = models.ManyToManyField(
        LearningObject,
        through="QuestionLearningObjectLink",
        related_name="questions",
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.prompt[:80]


class QuestionLearningObjectLink(models.Model):
    """A content-question pair; relevance is not a difficulty classification."""

    class ReviewStatus(models.TextChoices):
        AUTO_CONFIRMED = "auto_confirmed", "Automatically confirmed"
        PENDING_REVIEW = "pending_review", "Pending teacher review"
        UNMATCHED = "unmatched", "No confident match"
        TEACHER_CONFIRMED = "teacher_confirmed", "Teacher confirmed"
        TEACHER_UNPAIRED = "teacher_unpaired", "Teacher left unpaired"

    question = models.ForeignKey(
        Question,
        related_name="learning_object_links",
        on_delete=models.CASCADE,
    )
    learning_object = models.ForeignKey(
        LearningObject,
        related_name="question_links",
        on_delete=models.CASCADE,
    )
    relevance_score = models.FloatField(default=0.0)
    method = models.CharField(max_length=100, default="layout_tfidf")
    is_primary = models.BooleanField(default=True)
    review_status = models.CharField(
        max_length=30,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING_REVIEW,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_primary", "-relevance_score", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["question", "learning_object"],
                name="unique_question_learning_object_link",
            ),
            models.UniqueConstraint(
                fields=["question"],
                condition=models.Q(is_primary=True),
                name="unique_primary_learning_object_per_question",
            ),
        ]

    def __str__(self):
        return f"Question {self.question_id} -> learning object {self.learning_object_id}"
