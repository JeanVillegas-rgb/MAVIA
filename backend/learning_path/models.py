from django.core.exceptions import ValidationError
from django.db import models

from lessons.models import LearningObject


class PrerequisiteEdge(models.Model):
    """A directed dependency between two learning objects.

    ``prerequisite`` must be taught before ``dependent``. Edges are *derived*
    from textual signals rather than authored, so each row records which signal
    produced it and the evidence behind it. That keeps the graph inspectable
    and, later, correctable by a teacher.

    The same pair can be supported by more than one signal; the graph layer
    collapses those into a single edge, so the uniqueness constraint is on
    ``(prerequisite, dependent, signal)`` rather than on the pair alone.
    """

    class Signal(models.TextChoices):
        # Individual criteria (reference asymmetry, section reference,
        # co-occurrence) are no longer signals of their own. They are votes,
        # and one pair yields one edge whose ``evidence`` records which
        # criteria fired in which direction.
        VOTED = "voted", "Carried a majority of the derivation criteria"
        CHUNK_CONTINUATION = "chunk_continuation", "Next part of a passage the chunker split"
        TEACHER_AUTHORED = "teacher_authored", "Added by a teacher during review"

    class Source(models.TextChoices):
        DERIVED = "derived", "Derived from text signals"
        TEACHER = "teacher", "Added by a teacher"

    prerequisite = models.ForeignKey(
        LearningObject,
        related_name="dependent_edges",
        on_delete=models.CASCADE,
    )
    dependent = models.ForeignKey(
        LearningObject,
        related_name="prerequisite_edges",
        on_delete=models.CASCADE,
    )
    signal = models.CharField(max_length=30, choices=Signal.choices)
    # Teacher-added edges survive re-derivation; derived ones are replaced.
    source = models.CharField(
        max_length=10,
        choices=Source.choices,
        default=Source.DERIVED,
        db_index=True,
    )
    # How much confidence this edge carries. Consumed by the topological sort:
    # within one dependency layer, better-evidenced nodes are taught first.
    weight = models.FloatField(default=1.0)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["dependent_id", "-weight", "id"]
        indexes = [
            models.Index(fields=["dependent"]),
            models.Index(fields=["prerequisite"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["prerequisite", "dependent", "signal"],
                name="unique_prerequisite_edge_per_signal",
            ),
            models.CheckConstraint(
                condition=~models.Q(prerequisite=models.F("dependent")),
                name="prerequisite_edge_must_not_be_self_loop",
            ),
        ]

    def clean(self):
        if self.prerequisite_id == self.dependent_id:
            raise ValidationError({"dependent": "A learning object cannot be its own prerequisite."})
        if self.prerequisite.material_id != self.dependent.material_id:
            raise ValidationError({
                "dependent": "Prerequisite edges are derived within a single learning material."
            })

    def __str__(self):
        return f"{self.prerequisite_id} -> {self.dependent_id} ({self.signal})"
