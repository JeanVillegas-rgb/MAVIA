from rest_framework import serializers

from .models import Enrollment, LearningState


class StudentBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    email = serializers.EmailField()
    name = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.username


class EnrollmentSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = ["id", "student", "created_at"]


class LearningStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningState
        fields = [
            "id",
            "course",
            "current_module",
            "current_lesson_node",
            "current_question",
            "current_question_attempts",
            "mastery",
            "attempts",
            "completed",
            "started_at",
            "updated_at",
        ]


class StartLearningSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()


class SubmitResponseSerializer(serializers.Serializer):
    learning_state_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    selected_answer = serializers.CharField(max_length=255, allow_blank=True)
