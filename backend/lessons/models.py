from django.core.exceptions import ValidationError
from django.db import models
import re


def normalize_concept_title(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


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
    course = models.OneToOneField(
        CourseGroup,
        related_name="outline",
        on_delete=models.CASCADE,
    )
    outline_file = models.FileField(upload_to="outlines/")
    is_approved = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

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
    order = models.PositiveIntegerField(default=0)
    depth = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["depth", "order", "id"]
        unique_together = ("course", "parent", "order")

    def __str__(self):
        return self.title


class OutlineEdge(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    course = models.ForeignKey(
        CourseGroup,
        related_name="outline_edges",
        on_delete=models.CASCADE,
    )
    source = models.ForeignKey(
        OutlineNode,
        related_name="outgoing_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    target = models.ForeignKey(
        OutlineNode,
        related_name="incoming_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    score = models.FloatField(default=0.0)
    semantic_similarity = models.FloatField(null=True, blank=True)
    outline_order_score = models.FloatField(null=True, blank=True)
    explanation = models.TextField(blank=True)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source__order", "target__order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "source", "target"],
                name="unique_outline_prerequisite_edge",
            ),
            models.CheckConstraint(
                check=~models.Q(source=models.F("target")),
                name="outline_edge_source_not_target",
            ),
            models.CheckConstraint(
                check=models.Q(score__gte=0.0) & models.Q(score__lte=1.0),
                name="outline_edge_score_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(semantic_similarity__isnull=True)
                    | (
                        models.Q(semantic_similarity__gte=0.0)
                        & models.Q(semantic_similarity__lte=1.0)
                    )
                ),
                name="outline_edge_semantic_similarity_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(outline_order_score__isnull=True)
                    | (
                        models.Q(outline_order_score__gte=0.0)
                        & models.Q(outline_order_score__lte=1.0)
                    )
                ),
                name="outline_edge_order_score_between_0_and_1",
            ),
        ]

    def clean(self):
        errors = {}

        if self.source_id and self.target_id and self.source_id == self.target_id:
            errors["target"] = "Source and target cannot be the same node."

        if self.course_id:
            if self.source_id and self.source.course_id != self.course_id:
                errors["source"] = "Source node must belong to the same course as the edge."
            if self.target_id and self.target.course_id != self.course_id:
                errors["target"] = "Target node must belong to the same course as the edge."

        for field_name in ["score", "semantic_similarity", "outline_order_score"]:
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                errors[field_name] = "Value must be between 0.0 and 1.0."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.source} -> {self.target}"


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


class LearningObject(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"

    material = models.ForeignKey(
        LearningMaterial,
        related_name="learning_objects",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    content = models.TextField()
    image_prompt = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class LearningObjectPrerequisiteEdge(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    course = models.ForeignKey(
        CourseGroup,
        related_name="learning_object_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    module_node = models.ForeignKey(
        OutlineNode,
        related_name="learning_object_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    source = models.ForeignKey(
        LearningObject,
        related_name="outgoing_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    target = models.ForeignKey(
        LearningObject,
        related_name="incoming_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    score = models.FloatField(default=0.0)
    semantic_similarity = models.FloatField(null=True, blank=True)
    dependency_cue_score = models.FloatField(null=True, blank=True)
    source_order_score = models.FloatField(null=True, blank=True)
    title_overlap_score = models.FloatField(null=True, blank=True)
    explanation = models.TextField(blank=True)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source__material_id", "source__order", "target__material_id", "target__order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "module_node", "source", "target"],
                name="unique_learning_object_prerequisite_edge",
            ),
            models.CheckConstraint(
                check=~models.Q(source=models.F("target")),
                name="learning_object_edge_source_not_target",
            ),
            models.CheckConstraint(
                check=models.Q(score__gte=0.0) & models.Q(score__lte=1.0),
                name="learning_object_edge_score_between_0_and_1",
            ),
        ]

    def clean(self):
        errors = {}
        if self.source_id and self.target_id and self.source_id == self.target_id:
            errors["target"] = "Source and target cannot be the same learning object."
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the edge."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if self.source_id:
            if self.source.material.course_id != self.course_id:
                errors["source"] = "Source learning object must belong to the same course as the edge."
            if self.source.material.module_node_id != self.module_node_id:
                errors["source"] = "Source learning object must belong to the selected module."
        if self.target_id:
            if self.target.material.course_id != self.course_id:
                errors["target"] = "Target learning object must belong to the same course as the edge."
            if self.target.material.module_node_id != self.module_node_id:
                errors["target"] = "Target learning object must belong to the selected module."
        for field_name in ["score", "semantic_similarity", "dependency_cue_score", "source_order_score", "title_overlap_score"]:
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                errors[field_name] = "Value must be between 0.0 and 1.0."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.source} -> {self.target}"


class Question(models.Model):
    material = models.ForeignKey(
        LearningMaterial,
        related_name="questions",
        on_delete=models.CASCADE,
    )
    question_type = models.CharField(max_length=50, default="multiple_choice")
    prompt = models.TextField()
    choices = models.JSONField(default=list, blank=True)
    answer = models.TextField()
    explanation = models.TextField(blank=True)
    difficulty = models.CharField(max_length=50, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.prompt[:80]


class ExtractedConcept(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    course = models.ForeignKey(
        CourseGroup,
        related_name="extracted_concepts",
        on_delete=models.CASCADE,
    )
    module_node = models.ForeignKey(
        OutlineNode,
        related_name="extracted_concepts",
        on_delete=models.CASCADE,
    )
    canonical_title = models.CharField(max_length=255)
    normalized_title = models.CharField(max_length=255, editable=False)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    confidence = models.FloatField(null=True, blank=True)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module_node__order", "order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "module_node", "normalized_title"],
                name="unique_extracted_concept_normalized_title_per_module",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(confidence__isnull=True)
                    | (models.Q(confidence__gte=0.0) & models.Q(confidence__lte=1.0))
                ),
                name="extracted_concept_confidence_between_0_and_1",
            ),
        ]

    def clean(self):
        errors = {}
        self.normalized_title = normalize_concept_title(self.canonical_title)
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the concept."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            errors["confidence"] = "Confidence must be between 0.0 and 1.0."
        if not normalize_concept_title(self.canonical_title):
            errors["canonical_title"] = "Canonical title cannot be blank."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.normalized_title = normalize_concept_title(self.canonical_title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.canonical_title


class ConceptSource(models.Model):
    concept = models.ForeignKey(
        ExtractedConcept,
        related_name="sources",
        on_delete=models.CASCADE,
    )
    learning_material = models.ForeignKey(
        LearningMaterial,
        related_name="concept_sources",
        on_delete=models.CASCADE,
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    section_title = models.CharField(max_length=255, blank=True)
    source_excerpt = models.TextField(blank=True)
    first_appearance_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_appearance_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "concept",
                    "learning_material",
                    "page_number",
                    "section_title",
                    "source_excerpt",
                ],
                name="unique_concept_source_reference",
            ),
        ]

    def clean(self):
        errors = {}
        if self.concept_id and self.learning_material_id:
            if self.learning_material.course_id != self.concept.course_id:
                errors["learning_material"] = "Learning material must belong to the same course as the concept."
            if self.learning_material.module_node_id != self.concept.module_node_id:
                errors["learning_material"] = "Learning material must belong to the same module as the concept."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.concept} from {self.learning_material}"


class ConceptPrerequisiteEdge(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    course = models.ForeignKey(
        CourseGroup,
        related_name="concept_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    module_node = models.ForeignKey(
        OutlineNode,
        related_name="concept_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    source = models.ForeignKey(
        ExtractedConcept,
        related_name="outgoing_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    target = models.ForeignKey(
        ExtractedConcept,
        related_name="incoming_prerequisite_edges",
        on_delete=models.CASCADE,
    )
    score = models.FloatField(default=0.0)
    semantic_similarity = models.FloatField(null=True, blank=True)
    instructional_order_score = models.FloatField(null=True, blank=True)
    dependency_cue_score = models.FloatField(null=True, blank=True)
    source_order_score = models.FloatField(null=True, blank=True)
    title_overlap_score = models.FloatField(null=True, blank=True)
    explanation = models.TextField(blank=True)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source__order", "target__order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "module_node", "source", "target"],
                name="unique_concept_prerequisite_edge",
            ),
            models.CheckConstraint(
                check=~models.Q(source=models.F("target")),
                name="concept_edge_source_not_target",
            ),
            models.CheckConstraint(
                check=models.Q(score__gte=0.0) & models.Q(score__lte=1.0),
                name="concept_edge_score_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(semantic_similarity__isnull=True)
                    | (models.Q(semantic_similarity__gte=0.0) & models.Q(semantic_similarity__lte=1.0))
                ),
                name="concept_edge_semantic_similarity_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(instructional_order_score__isnull=True)
                    | (
                        models.Q(instructional_order_score__gte=0.0)
                        & models.Q(instructional_order_score__lte=1.0)
                    )
                ),
                name="concept_edge_instructional_order_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(dependency_cue_score__isnull=True)
                    | (models.Q(dependency_cue_score__gte=0.0) & models.Q(dependency_cue_score__lte=1.0))
                ),
                name="concept_edge_dependency_cue_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(source_order_score__isnull=True)
                    | (models.Q(source_order_score__gte=0.0) & models.Q(source_order_score__lte=1.0))
                ),
                name="concept_edge_source_order_between_0_and_1",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(title_overlap_score__isnull=True)
                    | (models.Q(title_overlap_score__gte=0.0) & models.Q(title_overlap_score__lte=1.0))
                ),
                name="concept_edge_title_overlap_between_0_and_1",
            ),
        ]

    def clean(self):
        errors = {}
        if self.source_id and self.target_id and self.source_id == self.target_id:
            errors["target"] = "Source and target cannot be the same concept."
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the edge."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if self.source_id:
            if self.source.course_id != self.course_id:
                errors["source"] = "Source concept must belong to the same course as the edge."
            if self.module_node_id and self.source.module_node_id != self.module_node_id:
                errors["source"] = "Source concept must belong to the selected module."
        if self.target_id:
            if self.target.course_id != self.course_id:
                errors["target"] = "Target concept must belong to the same course as the edge."
            if self.module_node_id and self.target.module_node_id != self.module_node_id:
                errors["target"] = "Target concept must belong to the selected module."
        for field_name in [
            "score",
            "semantic_similarity",
            "instructional_order_score",
            "dependency_cue_score",
            "source_order_score",
            "title_overlap_score",
        ]:
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                errors[field_name] = "Value must be between 0.0 and 1.0."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.source} -> {self.target}"


class ModuleConceptDAGState(models.Model):
    course = models.ForeignKey(
        CourseGroup,
        related_name="concept_dag_states",
        on_delete=models.CASCADE,
    )
    module_node = models.OneToOneField(
        OutlineNode,
        related_name="concept_dag_state",
        on_delete=models.CASCADE,
    )
    is_confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module_node__order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "module_node"],
                name="unique_module_concept_dag_state",
            ),
        ]

    def clean(self):
        errors = {}
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the DAG state."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Concept DAG state for {self.module_node}"
