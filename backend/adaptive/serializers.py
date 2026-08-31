from rest_framework import serializers

from question_generation.models import GeneratedQuestion
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

    def validate(self, data):
        try:
            learning_state = LearningState.objects.select_related("current_node", "current_question").get(
                pk=data["learning_state_id"]
            )
        except LearningState.DoesNotExist:
            raise serializers.ValidationError({
                "learning_state_id": "No such learning state."
            })

        try:
            question = GeneratedQuestion.objects.select_related("node").get(
                pk=data["question_id"]
            )
        except GeneratedQuestion.DoesNotExist:
            raise serializers.ValidationError({
                "question_id": "No such question."
            })

        if learning_state.current_question_id != question.id:
            raise serializers.ValidationError({
                "question_id": "This isn't the question the learner is currently on."
            })

        data["learning_state"] = learning_state
        data["question"] = question
        return data