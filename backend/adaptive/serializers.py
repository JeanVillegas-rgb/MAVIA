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
    # The API keeps this single "one step to resume to" shape even though the
    # model now tracks a full stack (adaptive.models.LearningState.remediation_stack)
    # to support nested prerequisite detours -- clients only ever need to know
    # the next thing to pop back to, not the whole chain.
    remediation_target_position = serializers.SerializerMethodField()

    class Meta:
        model = LearningState
        fields = [
            "id",
            "course",
            "current_module",
            "current_lesson_node",
            "current_question",
            "current_step_position",
            "current_generated_question",
            "current_chunk",
            "remediation_target_position",
            "current_variant",
            "current_question_attempts",
            "mastery",
            "attempts",
            "completed",
            "started_at",
            "updated_at",
        ]

    def get_remediation_target_position(self, obj):
        return obj.remediation_stack[-1]["position"] if obj.remediation_stack else None


class StartLearningSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    # The topic the player has open. The engine's cursor is course-wide, but
    # the player is opened per lesson, so without this the two can disagree --
    # the student taps one topic and is served another's concept, or a
    # finished course leaves them with no assigned question at all.
    lesson_node_id = serializers.IntegerField(required=False, allow_null=True)


class SubmitResponseSerializer(serializers.Serializer):
    learning_state_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    selected_answer = serializers.CharField(max_length=255, allow_blank=True)
