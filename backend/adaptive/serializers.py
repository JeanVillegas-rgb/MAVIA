from rest_framework import serializers

from .models import LearningState


class LearningStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningState
        fields = "__all__"


class StartLearningSerializer(serializers.Serializer):
    course_id = serializers.IntegerField(required=False)
    learner_id = serializers.CharField(required=False, default="default")


class StudentResponseSerializer(serializers.Serializer):
    learning_state_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    selected_answer = serializers.CharField(max_length=255)
    response_time = serializers.FloatField(required=False, default=0)
