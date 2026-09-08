from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# The incoming adaptive_portal engine reads these BKT weights and starting
# mastery. The legacy adaptive engine is unchanged. Default difficulty is
# reserved: the portal currently follows question order, not Bloom tiers.

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
