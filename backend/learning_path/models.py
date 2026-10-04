from django.db import models

from lessons.models import CourseGroup, LearningObjectGroup, OutlineNode


class ConceptPrerequisite(models.Model):
    """"Learn ``prerequisite`` before ``dependent``", between two concepts of a topic.

    A concept is a ``LearningObjectGroup``: the same idea taught across a
    topic's PDFs. Rows are proposed by the v3 criteria or decided by a teacher,
    and ``status`` says which -- only ``accepted`` and ``approved`` rows shape
    the learning path.

    Teacher decisions outlive re-derivation: publishing again recomputes the
    derived rows but never touches an ``approved`` or ``rejected`` one, so a
    pair a teacher turned down is not proposed again.
    """

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepted by the criteria"
        # Stored but hidden from teachers by default: measured on a blind
        # hand-check, most pending proposals were wrong.
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
    # Both concepts sit under lesson headings that share nothing. Such an edge
    # is never accepted automatically; see learning_path/CRITERIA.md.
    cross_section = models.BooleanField(default=False)
    # The votes and scores behind a derived row, kept so a decision can be
    # explained and a later scoring change needs no re-derivation to compare.
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
    """One step of a topic's published learning path.

    Saved when a topic publishes successfully and replaced wholesale on the
    next successful publish, so students always follow the path that matches
    the content they can see. Everything a step teaches hangs off its concept:
    the Normal, Simplified and Elaborated versions and the generated questions
    all belong to the concept's representative learning object.
    """

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
    # How many prerequisite links lead to this step at most. Steps sharing a
    # depth do not depend on one another.
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
    """"Learn ``prerequisite`` before ``dependent``", across two topics of one course.

    The course-level counterpart of ``ConceptPrerequisite``, kept apart so a
    topic's path and screen never see a concept from another topic. Same
    statuses and the same rule: re-deriving never overwrites a teacher's
    ``approved`` or ``rejected``.
    """

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
    # Votes and scores behind a derived row, plus ``contradicts_outline``.
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
