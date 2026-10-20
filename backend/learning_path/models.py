from django.db import models

from lessons.models import CourseGroup, LearningObjectGroup, OutlineNode


class ConceptPrerequisite(models.Model):
    """"Learn prerequisite before dependent" between two concepts of one topic."""

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepted by the criteria"
        PENDING = "pending", "Proposed, awaiting a teacher"
        APPROVED = "approved", "Approved by a teacher"
        REJECTED = "rejected", "Rejected by a teacher"

    class Source(models.TextChoices):
        DERIVED = "derived", "Derived by the criteria"
        TEACHER = "teacher", "Decided by a teacher"

    SHAPES_PATH = (Status.ACCEPTED, Status.APPROVED)
    TEACHER_DECIDED = (Status.APPROVED, Status.REJECTED)

    outline_node = models.ForeignKey(
        OutlineNode,
        related_name="concept_prerequisites",
        on_delete=models.CASCADE,
    )
    prerequisite = models.ForeignKey(
        LearningObjectGroup,
        related_name="dependent_links",
        on_delete=models.CASCADE,
    )
    dependent = models.ForeignKey(
        LearningObjectGroup,
        related_name="prerequisite_links",
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.DERIVED)
    cross_section = models.BooleanField(default=False)
    evidence = models.JSONField(default=dict, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["outline_node_id", "prerequisite_id", "dependent_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["prerequisite", "dependent"],
                name="unique_concept_prerequisite_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(prerequisite=models.F("dependent")),
                name="concept_prerequisite_must_not_be_self_loop",
            ),
        ]

    def __str__(self):
        return f"{self.prerequisite_id} -> {self.dependent_id} ({self.status})"


class LearningPathStep(models.Model):
    """One step of a topic's published learning path."""

    outline_node = models.ForeignKey(
        OutlineNode,
        related_name="learning_path_steps",
        on_delete=models.CASCADE,
    )
    concept = models.ForeignKey(
        LearningObjectGroup,
        related_name="path_steps",
        on_delete=models.CASCADE,
    )
    position = models.PositiveIntegerField()
    depth = models.PositiveSmallIntegerField(default=0)
    published_at = models.DateTimeField()

    class Meta:
        ordering = ["outline_node_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["outline_node", "position"],
                name="unique_learning_path_position",
            ),
            models.UniqueConstraint(
                fields=["outline_node", "concept"],
                name="unique_learning_path_concept",
            ),
        ]

    def __str__(self):
        return f"{self.outline_node_id} #{self.position}: {self.concept_id}"


class CourseConceptLink(models.Model):
    """"Learn prerequisite before dependent" between concepts of two topics in a course."""

    Status = ConceptPrerequisite.Status
    Source = ConceptPrerequisite.Source
    SHAPES_PATH = ConceptPrerequisite.SHAPES_PATH
    TEACHER_DECIDED = ConceptPrerequisite.TEACHER_DECIDED

    course = models.ForeignKey(CourseGroup, related_name="course_concept_links", on_delete=models.CASCADE)
    prerequisite = models.ForeignKey(
        LearningObjectGroup, related_name="course_dependent_links", on_delete=models.CASCADE,
    )
    dependent = models.ForeignKey(
        LearningObjectGroup, related_name="course_prerequisite_links", on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=10, choices=ConceptPrerequisite.Status.choices, db_index=True)
    source = models.CharField(
        max_length=10, choices=ConceptPrerequisite.Source.choices, default=ConceptPrerequisite.Source.DERIVED,
    )
    evidence = models.JSONField(default=dict, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["prerequisite", "dependent"], name="unique_course_concept_link"),
        ]

    def __str__(self):
        return f"{self.prerequisite_id} -> {self.dependent_id} ({self.status})"
