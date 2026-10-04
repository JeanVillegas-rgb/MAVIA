from rest_framework import serializers

from .models import AdaptiveConfig


class AdaptiveConfigSerializer(serializers.ModelSerializer):
    updated_by_username = serializers.CharField(
        source="updated_by.username", read_only=True, default=None
    )

    class Meta:
        model = AdaptiveConfig
        fields = [
            "p_guess",
            "p_slip",
            "p_learn",
            "mastery_ceiling",
            "starting_mastery",
            "default_difficulty",
            "updated_at",
            "updated_by_username",
        ]
        read_only_fields = ["updated_at", "updated_by_username"]

    def validate(self, attrs):
        p_guess = attrs.get("p_guess", getattr(self.instance, "p_guess", None))
        p_slip = attrs.get("p_slip", getattr(self.instance, "p_slip", None))
        if p_guess is not None and p_slip is not None and p_guess + p_slip >= 1:
            raise serializers.ValidationError(
                "p_guess + p_slip must be less than 1, or the scoring model "
                "can no longer distinguish a correct answer from a guess."
            )

        starting = attrs.get("starting_mastery", getattr(self.instance, "starting_mastery", None))
        ceiling = attrs.get("mastery_ceiling", getattr(self.instance, "mastery_ceiling", None))
        if starting is not None and ceiling is not None and starting > ceiling:
            raise serializers.ValidationError(
                "starting_mastery cannot be greater than mastery_ceiling."
            )
        return attrs
