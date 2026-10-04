from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# Mirrors the module-level constants the adaptive engine's Bayesian
# Knowledge Tracing scorer used in the Milestone1-Jean prototype
# (backend/adaptive/services.py: P_GUESS, P_SLIP, P_LEARN, MASTERY_CEILING,
# STARTING_MASTERY, DEFAULT_DIFFICULTY). When that engine is ported into
# mavia, its scorer should read AdaptiveConfig.load() instead of hardcoded
# constants — this app exists so admins can tune it without a redeploy.

UNIT_INTERVAL = [MinValueValidator(0.0), MaxValueValidator(1.0)]


class AdaptiveConfig(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    # Singleton: always pk=1. Enforced in save(), not just by convention.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    p_guess = models.FloatField(
        default=0.20,
        validators=UNIT_INTERVAL,
        help_text="P(correct answer | learner doesn't actually know it).",
    )
    p_slip = models.FloatField(
        default=0.10,
        validators=UNIT_INTERVAL,
        help_text="P(wrong answer | learner does know it).",
    )
    p_learn = models.FloatField(
        default=0.15,
        validators=UNIT_INTERVAL,
        help_text="P(learner transitions from not-knowing to knowing after one question).",
    )
    mastery_ceiling = models.FloatField(
        default=0.99,
        validators=UNIT_INTERVAL,
        help_text="Upper cap applied to the computed mastery score.",
    )
    starting_mastery = models.FloatField(
        default=0.30,
        validators=UNIT_INTERVAL,
        help_text="Mastery a learner starts a new lesson node with.",
    )
    default_difficulty = models.CharField(
        max_length=10,
        choices=Difficulty.choices,
        default=Difficulty.MEDIUM,
        help_text="Difficulty used to pick the first question for a bloom tier.",
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Adaptive engine weights"
        verbose_name_plural = "Adaptive engine weights"

    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "Adaptive engine weights"

    @classmethod
    def load(cls) -> "AdaptiveConfig":
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
