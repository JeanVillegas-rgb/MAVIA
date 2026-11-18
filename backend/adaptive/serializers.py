from rest_framework import serializers

from .models import Enrollment, StudentBaseline


class StudentBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    email = serializers.EmailField()
    name = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email


class EnrollmentSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = ["id", "student", "created_at"]


class StudentBaselineSerializer(serializers.ModelSerializer):
 #the cold start baseline na makit.an ni prof
    class Meta:
        model = StudentBaseline
        fields = [
            "total_responses_count",
            "total_correct_count",
            "estimated_ability",
            "estimated_l0",
            "calibrated",
            "calibrated_at",
        ]
