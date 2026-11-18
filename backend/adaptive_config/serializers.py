from rest_framework import serializers

from .models import AdaptiveConfig


class AdaptiveConfigSerializer(serializers.ModelSerializer):
    updated_by_username = serializers.CharField(
        source="updated_by.username", read_only=True, default=None
    )

    class Meta:
        model = AdaptiveConfig
        fields = [
            # BKT population weights
            "p_guess",
            "p_guess_true_false",
            "p_slip",
            "p_learn",
            "mastery_ceiling",
            "starting_mastery",
            # cold-start calibration
            "prior_weight",
            "calibration_margin",
            # legacy
            "default_difficulty",
            "updated_at",
            "updated_by_username",
        ]
        read_only_fields = ["updated_at", "updated_by_username"]

    def _value(self, attrs, name):
        """The submitted value, or the saved one when a PATCH leaves it out."""
        return attrs.get(name, getattr(self.instance, name, None))

    def validate(self, attrs):
        p_slip = self._value(attrs, "p_slip")
        for name in ("p_guess", "p_guess_true_false"):
            guess = self._value(attrs, name)
            if guess is not None and p_slip is not None and guess + p_slip >= 1:
                raise serializers.ValidationError(
                    f"{name} + p_slip must be less than 1, or the scoring model "
                    "can no longer distinguish a correct answer from a guess."
                )

        starting = self._value(attrs, "starting_mastery")
        ceiling = self._value(attrs, "mastery_ceiling")
        if starting is not None and ceiling is not None and starting > ceiling:
            raise serializers.ValidationError(
                "starting_mastery cannot be greater than mastery_ceiling."
            )
        return attrs
