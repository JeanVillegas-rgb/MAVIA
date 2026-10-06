from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
# The population weights: the defaults every student starts on before the
# system knows them (the cold start). Read by adaptive/services.py on every answer.
UNIT_INTERVAL = [MinValueValidator(0.0), MaxValueValidator(1.0)]


class AdaptiveConfig(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    #BKT population weightz
    p_guess = models.FloatField(
        default=0.20,
        validators=UNIT_INTERVAL,
        help_text="Probability of(correct answer | learner doesn't actually know it), for multiple-choice questions.",
    )
    p_guess_true_false = models.FloatField(
        default=0.45,
        validators=UNIT_INTERVAL,
        help_text="Probability of(correct answer | learner doesn't actually know it), for True/False questions.",
    )
    p_slip = models.FloatField(
        default=0.10,
        validators=UNIT_INTERVAL,
        help_text="Probability of(wrong answer | learner does know it).",
    )
    p_learn = models.FloatField(
        default=0.15,
        validators=UNIT_INTERVAL,
        help_text="Probability of(learner transitions from not-knowing to knowing after one question).",
    )   
    mastery_ceiling = models.FloatField(
        default=0.99,
        validators=UNIT_INTERVAL,
        help_text="Upper cap applied to the computed mastery score.",
    )
    starting_mastery = models.FloatField(
        default=0.30,
        validators=UNIT_INTERVAL,
        help_text="Population L0: Mastery a learner starts a new lesson node with.",
    )


    #cold start calibrationz
    prior_weight = models.PositiveSmallIntegerField(
        default=10, 
        validators=[MinValueValidator(1)],
        help_text= "Blends the score of a student on avg and their personal score into one estimate--tis cuz we cant trust a new student's score from only a few answers. The avg counts as this many answers."
    )

    calibration_margin = models.FloatField(
        default=0.15,
        validators=[MinValueValidator(0.01), MaxValueValidator(1.0)],
        help_text="how close the systems guess of a students score must be before we can trust it. So basically the guess must be within 15 points of the students real score, "
        "measured 95% sure. Until then----student is still calibrating."
    )
    

    #legazy
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
